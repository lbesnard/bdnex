import os
import sys
import types

import pytest

from bdnex.importer.bedetheque import Bedetheque, DirectoryFetcher, load_fetcher, flaresolverr_fetch
from bdnex.library import Library

TEST_ROOT = os.path.dirname(os.path.dirname(__file__))
ONESHOT_URL = "https://www.bedetheque.com/BD-Bibliomule-de-Cordoue-433432.html"
ONESHOT_HTML = os.path.join(TEST_ROOT, "bedetheque_oneshot_bibliomule.html")


def test_load_fetcher_resolves_module_function(monkeypatch):
    module = types.ModuleType("fake_fetcher_mod")
    module.fetch = lambda url: b"x"
    monkeypatch.setitem(sys.modules, "fake_fetcher_mod", module)
    assert load_fetcher("fake_fetcher_mod:fetch")("u") == b"x"


@pytest.mark.parametrize("spec", ["nocolon", ":func", "mod:"])
def test_load_fetcher_rejects_bad_spec(spec):
    with pytest.raises(ValueError):
        load_fetcher(spec)


def test_load_fetcher_rejects_non_callable(monkeypatch):
    module = types.ModuleType("fake_fetcher_mod2")
    module.value = 3
    monkeypatch.setitem(sys.modules, "fake_fetcher_mod2", module)
    with pytest.raises(TypeError):
        load_fetcher("fake_fetcher_mod2:value")


def _config_with_endpoint(monkeypatch, endpoint):
    settings = types.SimpleNamespace(bedetheque=types.SimpleNamespace(fetcher_endpoint=endpoint))
    monkeypatch.setattr("bdnex.importer.bedetheque.fetchers.load_config",
                        lambda: types.SimpleNamespace(settings=settings))


def test_flaresolverr_fetch_parses_response_without_network(monkeypatch):
    calls = []

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"status": "ok", "solution": {"response": "<html>page</html>"}}

    _config_with_endpoint(monkeypatch, "http://test.invalid/v1")
    monkeypatch.setattr("requests.post", lambda *a, **k: calls.append((a, k)) or Resp())
    assert flaresolverr_fetch(ONESHOT_URL, timeout=5) == b"<html>page</html>"
    assert calls[0][0][0] == "http://test.invalid/v1"  # endpoint comes from the config
    assert calls[0][1]["json"]["url"] == ONESHOT_URL


def test_flaresolverr_fetch_error_status_raises(monkeypatch):
    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"status": "error", "message": "boom"}

    _config_with_endpoint(monkeypatch, "http://test.invalid/v1")
    monkeypatch.setattr("requests.post", lambda *a, **k: Resp())
    with pytest.raises(RuntimeError, match="boom"):
        flaresolverr_fetch(ONESHOT_URL)


def test_directory_fetcher_by_id(tmp_path):
    (tmp_path / "433432.html").write_bytes(b"<html>page</html>")
    assert DirectoryFetcher(tmp_path)(ONESHOT_URL) == b"<html>page</html>"


def test_directory_fetcher_matches_saved_browser_name(tmp_path):
    (tmp_path / "BD-Bibliomule-de-Cordoue-433432.html").write_bytes(b"ok")
    assert DirectoryFetcher(tmp_path)(ONESHOT_URL) == b"ok"


def test_directory_fetcher_missing_page_message(tmp_path):
    with pytest.raises(FileNotFoundError, match="433432.html"):
        DirectoryFetcher(tmp_path)(ONESHOT_URL)
    with pytest.raises(FileNotFoundError):
        DirectoryFetcher(tmp_path / "nope")(ONESHOT_URL)


def test_plugin_with_directory_fetcher_end_to_end(tmp_path):
    saved = tmp_path / "pages"
    saved.mkdir()
    with open(ONESHOT_HTML, "rb") as src:
        (saved / "433432.html").write_bytes(src.read())
    with Library(":memory:") as lib:
        plugin = Bedetheque(lib, fetch=DirectoryFetcher(saved), sleep=lambda s: None)
        info = plugin.album_info(ONESHOT_URL)
        assert info.kind == "oneshot" and info.title == "La bibliomule de Cordoue"
        lib.add_album(info)
        assert lib.stats()["oneshots"] == 1


# ------------------------------------------------------------- friendly errors
from bdnex.importer.bedetheque import FetcherError  # noqa: E402


def test_function_name_written_into_the_module_path_gets_a_suggestion():
    with pytest.raises(FetcherError) as err:
        load_fetcher("bdnex.importer.bedetheque.flaresolverr_fetch:flaresolverr_fetch")
    message = str(err.value)
    assert "can not import the module 'bdnex.importer.bedetheque.flaresolverr_fetch'" in message
    assert "Did you mean 'bdnex.importer.bedetheque:flaresolverr_fetch'?" in message


@pytest.mark.parametrize("spec", ["bdnex.importer.bedetheque:flaresolverr_fetch",
                                  "bdnex.importer.bedetheque.fetchers:flaresolverr_fetch"])
def test_both_valid_spellings_load_the_same_function(spec):
    assert load_fetcher(spec).__name__ == "flaresolverr_fetch"


def test_the_suggestion_is_itself_loadable():
    with pytest.raises(FetcherError) as err:
        load_fetcher("bdnex.importer.bedetheque.flaresolverr_fetch:flaresolverr_fetch")
    suggested = err.value.args[0].split("Did you mean '")[1].split("'")[0]
    assert load_fetcher(suggested).__name__ == "flaresolverr_fetch"


def test_unknown_module_without_a_suggestion_still_explains():
    with pytest.raises(FetcherError, match="part before ':' must be a module"):
        load_fetcher("no_such_package.thing:func")


def test_missing_function_in_an_existing_module():
    with pytest.raises(FetcherError, match="has no function 'nope'"):
        load_fetcher("bdnex.importer.bedetheque.fetchers:nope")


def test_fetcher_error_is_still_a_value_error():
    assert issubclass(FetcherError, ValueError)  # code and tests that expect ValueError keep working


def test_flaresolverr_without_an_endpoint_says_what_to_set(tmp_path, monkeypatch):
    import pytest, yaml
    from bdnex.importer.bedetheque import FetcherError, flaresolverr_fetch
    conf = tmp_path / "bdnex.yaml"
    conf.write_text(yaml.safe_dump({"config_version": 2}))
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    with pytest.raises(FetcherError, match="fetcher_endpoint is not set"):
        flaresolverr_fetch("https://www.bedetheque.com/BD-X-1.html")


def _ok(html="<html>page</html>"):
    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"status": "ok", "solution": {"response": html}}
    return Resp()


def test_several_endpoints_share_the_requests_round_robin(monkeypatch):
    from bdnex.importer.bedetheque import fetchers
    fetchers._down.clear()
    _config_with_endpoint(monkeypatch, ["http://a/v1", "http://b/v1", "http://c/v1"])
    used = []
    monkeypatch.setattr("requests.post", lambda endpoint, **k: used.append(endpoint) or _ok())
    for _ in range(6):
        flaresolverr_fetch(ONESHOT_URL)
    assert sorted(used) == ["http://a/v1"] * 2 + ["http://b/v1"] * 2 + ["http://c/v1"] * 2  # each took its share


def test_a_failing_endpoint_falls_back_to_the_next_and_is_left_alone_for_a_while(monkeypatch):
    import requests
    from bdnex.importer.bedetheque import fetchers
    fetchers._down.clear()
    _config_with_endpoint(monkeypatch, ["http://down/v1", "http://up/v1"])
    used = []

    def post(endpoint, **k):
        used.append(endpoint)
        if "down" in endpoint:
            raise requests.ConnectionError("refused")
        return _ok()

    monkeypatch.setattr("requests.post", post)
    for _ in range(4):
        assert flaresolverr_fetch(ONESHOT_URL) == b"<html>page</html>"
    assert used.count("http://down/v1") == 1 and used.count("http://up/v1") == 4  # tried once, then left for a minute


def test_when_every_endpoint_fails_the_error_says_why_for_each(monkeypatch):
    import requests
    from bdnex.importer.bedetheque import fetchers
    fetchers._down.clear()
    _config_with_endpoint(monkeypatch, ["http://a/v1", "http://b/v1"])
    monkeypatch.setattr("requests.post", lambda endpoint, **k: (_ for _ in ()).throw(requests.ConnectionError(f"no {endpoint}")))
    with pytest.raises(RuntimeError, match=r"no FlareSolverr endpoint.*http://a/v1.*http://b/v1"):
        flaresolverr_fetch(ONESHOT_URL)


def test_which_endpoint_served_each_page_is_logged_and_a_fallback_is_not_silent(monkeypatch, caplog):
    import logging
    import requests
    from bdnex.importer.bedetheque import fetchers
    import itertools
    fetchers._down.clear()
    monkeypatch.setattr(fetchers, "_turn", itertools.count())  # the first request starts at the first endpoint
    _config_with_endpoint(monkeypatch, ["http://down/v1", "http://up/v1"])

    def post(endpoint, **k):
        if "down" in endpoint:
            raise requests.ConnectionError("refused")
        return _ok()

    monkeypatch.setattr("requests.post", post)
    with caplog.at_level(logging.INFO, logger="bdnex.importer.bedetheque.fetchers"):
        flaresolverr_fetch(ONESHOT_URL)
    text = caplog.text
    assert "http://down/v1 failed (refused): trying the next endpoint" in text  # shown by default (a warning)
    assert "fetched through http://up/v1 in" in text  # which one did the work (shown with -v)
    assert any(r.levelno == logging.WARNING for r in caplog.records) and any(r.levelno == logging.INFO for r in caplog.records)
