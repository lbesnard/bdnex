"""hooks: commands of your own that run after bdnex changed something."""

import io
import sys
from types import SimpleNamespace

import yaml

from bdnex.ui import hooks
from helpers import ALBUM_URL  # noqa: F401  (keeps the helpers on the path like the other tests)


def settings_with(**events):
    return SimpleNamespace(hooks=SimpleNamespace(**{"after_import": [], "after_write": [], "after_move": [], **events}))


def marker_command(path, text="ran"):
    return f"{sys.executable} -c \"import os; open({str(path)!r}, 'w').write(os.environ['BDNEX_EVENT'] + ' ' + os.environ['BDNEX_CHANGED'])\""


def test_a_hook_runs_with_the_event_and_the_count_in_its_environment(tmp_path):
    marker = tmp_path / "marker"
    stream = io.StringIO()
    assert hooks.run_hooks(settings_with(after_import=[marker_command(marker)]), "after_import", 3, "import", stream) == 1
    assert marker.read_text() == "after_import 3" and "hook after_import" in stream.getvalue()


def test_nothing_runs_when_nothing_changed_or_no_hook_is_set(tmp_path):
    marker = tmp_path / "marker"
    assert hooks.run_hooks(settings_with(after_import=[marker_command(marker)]), "after_import", 0, "import") == 0
    assert not marker.exists()
    assert hooks.run_hooks(settings_with(), "after_move", 5, "move") == 0


def test_a_failing_or_missing_hook_is_reported_and_never_raises(tmp_path):
    stream = io.StringIO()
    ran = hooks.run_hooks(settings_with(after_write=["definitely-not-a-program-xyz", f"{sys.executable} -c 'raise SystemExit(3)'"]),
                          "after_write", 1, "write", stream)
    text = stream.getvalue()
    assert ran == 1 and "could not run" in text and "ended with code 3" in text


def test_the_defaults_have_no_hook_and_a_bad_value_is_refused(tmp_path, monkeypatch):
    import pytest

    from bdnex.config import ConfigError, build_settings, load_defaults

    assert build_settings(load_defaults())[0].hooks.after_import == []
    broken = load_defaults()
    broken["hooks"]["after_import"] = "bdnex komga push"  # a list is needed
    with pytest.raises(ConfigError):
        build_settings(broken)


def test_a_hook_is_not_handed_the_web_password_or_token(tmp_path, monkeypatch):
    monkeypatch.setenv("BDNEX_PASSWORD", "secret")
    monkeypatch.setenv("BDNEX_TOKEN", "secret")
    monkeypatch.setenv("KEPT", "yes")
    marker = tmp_path / "marker"
    command = (f"{sys.executable} -c \"import os; open({str(marker)!r}, 'w').write(repr(("
               "os.environ.get('BDNEX_PASSWORD'), os.environ.get('BDNEX_TOKEN'), os.environ.get('KEPT'))))\"")
    hooks.run_hooks(settings_with(after_import=[command]), "after_import", 1, "import", io.StringIO())
    assert marker.read_text() == "(None, None, 'yes')"  # the rest of the environment is yours to use
