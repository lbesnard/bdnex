import sys
import types

import pytest

from bdnex.config import build_settings
from bdnex.importer import registry
from bdnex.importer.bedetheque import Bedetheque
from bdnex.importer.registry import UnknownSource, available_sources, create_source
from bdnex.importer.session import PARTIAL, TAGGED, ImportSession
from bdnex.importer.source import MetadataSource, check_source
from bdnex.library import Library
import helpers


def settings(**extra):
    return build_settings({"config_version": 2, **extra})[0]


@pytest.fixture
def lib():
    with Library(":memory:") as library:
        yield library


def test_default_source_is_bedetheque_and_satisfies_the_contract(lib):
    s = settings()
    assert s.source == "bedetheque"
    source = create_source("bedetheque", lib, s)
    assert isinstance(source, Bedetheque) and isinstance(source, MetadataSource)
    check_source(source)


def test_available_sources_lists_builtin_and_installed(monkeypatch):
    ep = types.SimpleNamespace(name="other", value="pkg.mod:factory")
    monkeypatch.setattr(registry, "entry_points", lambda group: [ep] if group == "bdnex.sources" else [])
    assert available_sources() == {"other": "pkg.mod:factory",
                                   "bedetheque": "bdnex.importer.bedetheque:create_source"}


def test_installed_entry_point_is_found_by_name(lib, monkeypatch):
    ep = types.SimpleNamespace(name="fake", value="helpers:create_fake_source")
    monkeypatch.setattr(registry, "entry_points", lambda group: [ep])
    assert create_source("fake", lib, settings()).name == "fake"


def test_builtin_wins_over_an_installed_plugin_with_the_same_name(monkeypatch):
    ep = types.SimpleNamespace(name="bedetheque", value="evil:factory")
    monkeypatch.setattr(registry, "entry_points", lambda group: [ep])
    assert available_sources()["bedetheque"].startswith("bdnex.")


def test_dotted_path_source_gets_its_options(lib):
    s = settings(plugins={"fake": {"unavailable": ["https://fake.example/albums/100"]}})
    source = create_source("helpers:create_fake_source", lib, s)
    assert source.options == {"unavailable": ["https://fake.example/albums/100"]}


def test_unknown_names_and_bad_paths_explain_themselves(lib):
    with pytest.raises(UnknownSource, match="unknown source 'nope'.*bedetheque"):
        create_source("nope", lib, settings())
    with pytest.raises(UnknownSource, match="can not load"):
        create_source("no_such_module:factory", lib, settings())
    with pytest.raises(UnknownSource, match="can not load"):
        create_source("helpers:missing_function", lib, settings())
    with pytest.raises(UnknownSource, match="not callable"):
        create_source("sys:version", lib, settings())


def test_incomplete_plugin_is_rejected_naming_the_missing_members(lib):
    with pytest.raises(TypeError, match="missing: name, fetch_errors, is_ready"):
        create_source("helpers:create_incomplete_source", lib, settings())


def test_plugin_options_are_free_form_in_the_config():
    s = settings(plugins={"fake": {"anything": {"nested": [1, 2]}}, "other": {}})
    assert s.plugins["fake"]["anything"]["nested"] == [1, 2]


# ---------------- the importer working with a source that is not Bédéthèque
def fake_session(lib, **kw):
    s = settings(**kw.pop("config", {}))
    source = create_source("helpers:create_fake_source", lib, s)
    return ImportSession(lib, source, s, **kw), source


def test_import_with_a_foreign_source(lib, make_cbz):
    session, source = fake_session(lib, auto="all")
    path = make_cbz("Tintin - T05 - Le lotus bleu.cbz")
    source.prepare()  # `run()` does this; calling import_file directly, we do it ourselves
    result = session.import_file(path)
    assert result.outcome == TAGGED
    album = lib.get_album(result.album_id)
    assert album["source"] == "fake" and album["series"] == "Tintin" and album["volume"] == 5
    assert lib.find_album("fake", "100") == result.album_id and lib.find_album("bedetheque", "100") is None
    assert source.fetched == ["https://fake.example/albums/100"]


def test_run_prepares_the_source_once_and_reports_its_name(lib, make_cbz, tmp_path):
    session, source = fake_session(lib, auto="all")
    messages = []
    session.progress = messages.append
    make_cbz("Tintin T05 lotus bleu.cbz")
    session.run([str(tmp_path)])
    assert source.prepared == 1
    assert messages[0] == "Preparing the fake source (first run only, may take a minute)..."
    assert "  fake index loaded" in messages
    session.run([str(tmp_path)])
    assert source.prepared == 1  # is_ready() is true now


def test_foreign_source_unavailable_page_falls_back_to_partial_info(lib, make_cbz):
    session, source = fake_session(lib, auto="all", config={"plugins": {"fake": {
        "unavailable": ["https://fake.example/albums/100"]}}})
    source.prepare()
    result = session.import_file(make_cbz("Tintin T05 lotus bleu.cbz"))
    assert result.outcome == PARTIAL
    assert lib.get_album(result.album_id)["extra"]["partial"] is True


def test_duplicate_detection_uses_the_sources_own_name(lib, make_cbz):
    session, source = fake_session(lib, config={"duplicates": {"action": "skip"}}, auto="all")
    source.prepare()
    first = session.import_file(make_cbz("Tintin T05 lotus bleu.cbz", pages=3))
    second = session.import_file(make_cbz("copy Tintin T05 lotus bleu.cbz", pages=4))
    assert first.outcome == TAGGED and second.outcome == "duplicate"


def test_a_one_shot_from_a_foreign_source(lib, make_cbz):
    session, source = fake_session(lib, auto="all")
    source.prepare()
    result = session.import_file(make_cbz("Maus.cbz"))
    album = lib.get_album(result.album_id)
    assert album["kind"] == "oneshot" and album["credits"] == {"Writer": ["Spiegelman, Art"]}


def test_the_minimal_source_in_the_docs_really_works(lib, make_cbz, monkeypatch):
    """Runs the code block of docs/dev/plugins.md, so the guide can not drift from the contract."""
    import os
    import re

    text = open(os.path.join(os.path.dirname(__file__), "..", "..", "docs", "dev", "plugins.md"),
                encoding="utf-8").read()
    block = re.search(r"## A minimal source\n\n```python\n(.*?)```", text, re.S).group(1)
    module = types.ModuleType("mysource")
    exec(compile(block, "docs/dev/plugins.md", "exec"), module.__dict__)
    monkeypatch.setitem(sys.modules, "mysource", module)

    s = settings(source="mysource:create_source")
    source = create_source(s.source, lib, s)
    check_source(source)
    result = ImportSession(lib, source, s, auto="all").run([make_cbz("Maus.cbz")])
    assert result.results[0].outcome == TAGGED
    assert lib.get_album(result.results[0].album_id)["credits"] == {"Writer": ["Spiegelman, Art"]}
