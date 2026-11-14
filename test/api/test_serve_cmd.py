"""``bdnex serve``: it refuses to open the library to the network without a token."""

import pytest

pytest.importorskip("uvicorn")

from bdnex.ui import serve_cmd  # noqa: E402


@pytest.fixture
def config(tmp_path, monkeypatch):
    path = tmp_path / "c.yaml"
    path.write_text(f"config_version: 2\ncollection_path: {tmp_path}/comics\nlibrary: {tmp_path}/lib.sqlite\n")
    (tmp_path / "lib.sqlite").write_bytes(b"")
    monkeypatch.setenv("BDNEX_CONFIG", str(path))
    return path


def test_a_public_address_needs_a_token(config, capsys, monkeypatch):
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: pytest.fail("the server started"))
    assert serve_cmd.run(["--host", "0.0.0.0"]) == 2
    assert "api.token" in capsys.readouterr().err


def test_a_public_address_with_a_token_starts(config, monkeypatch):
    started = {}
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: started.update(kw))
    assert serve_cmd.run(["--host", "0.0.0.0", "--token", "x"]) == 0
    assert started["host"] == "0.0.0.0" and started["port"] == 8765


def test_local_by_default(config, monkeypatch):
    started = {}
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: started.update(kw))
    assert serve_cmd.run([]) == 0 and started["host"] == "127.0.0.1"


def test_no_library_yet(tmp_path, monkeypatch, capsys):
    path = tmp_path / "c.yaml"
    path.write_text(f"config_version: 2\ncollection_path: {tmp_path}/comics\nlibrary: {tmp_path}/none.sqlite\n")
    monkeypatch.setenv("BDNEX_CONFIG", str(path))
    assert serve_cmd.run([]) == 1 and "No library yet" in capsys.readouterr().err


def test_a_public_address_with_a_password_starts_and_the_login_is_passed_on(config, monkeypatch):
    seen = {}
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: seen.update(kw))
    monkeypatch.setattr("bdnex.api.app.create_app", lambda settings, **kw: seen.update(app=kw))
    monkeypatch.setenv("BDNEX_PASSWORD", "pw")
    monkeypatch.setenv("BDNEX_USER", "me")
    assert serve_cmd.run(["--host", "0.0.0.0"]) == 0
    assert seen["app"]["password"] == "pw" and seen["app"]["username"] == "me"
