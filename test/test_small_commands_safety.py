"""Findings of the review of the small commands: listing changes nothing, JSON is JSON, completion survives odd names, a cover is not a bomb."""

import io
import json
import struct
import zlib

import yaml

from bdnex.importer import covers
from bdnex.library import AlbumInfo, Library
from bdnex.ui import completion_cmd, ls_cmd, series_cmd, stats_cmd, thumbnail

from test_ls_cmd import setup  # noqa: F401,E402  (the fixture: a library with three files, one of them unmatched)


def run(module, *argv):
    out = io.StringIO()
    code = module.run(list(argv), out=out)
    return code, out.getvalue()


def png_header(width, height):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IEND", b"")


# ------------------------------------------------------------------ listing
def test_the_json_of_unmatched_files_is_the_rows_not_a_padded_screen_column(setup):
    code, text = run(ls_cmd, "--unmatched", "--json")
    rows = json.loads(text)
    assert code == 0 and rows and all("best" not in row for row in rows)  # `best` is for the screen: '    -' or ' 83.5' next to the real `score`
    code, text = run(ls_cmd, "--unmatched")
    assert "-" in text  # the screen still has its column


def test_limit_applies_to_unmatched_files_too(setup):
    with Library(setup / "lib.sqlite") as lib:
        for n in range(3):
            lib.log_import(f"/x/gone{n}.cbz", "failed", 10.0 + n)
    _, everything = run(ls_cmd, "--unmatched", "--count")
    _, limited = run(ls_cmd, "--unmatched", "--limit", "1", "--count")
    assert int(limited) == 1 and int(everything) > 1  # it was ignored on this path


def test_listing_opens_the_library_read_only_and_without_one_says_so(setup, tmp_path, monkeypatch):
    db = setup / "lib.sqlite"
    before = db.read_bytes()
    for command in (("ls",), ("ls", "--unmatched"), ("ls", "--missing")):
        ls_cmd.run(list(command[1:]), out=io.StringIO())
    series_cmd.run([], out=io.StringIO())
    assert db.read_bytes() == before  # no migration, no path rewriting, no backup just to list
    assert not list(setup.glob("*.bak*"))
    conf = tmp_path / "other.yaml"
    conf.write_text(yaml.safe_dump({"config_version": 2, "library": str(tmp_path / "none" / "lib.sqlite")}))
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    assert ls_cmd.run([], out=io.StringIO()) == 1 and not (tmp_path / "none").exists()  # it used to create an empty library to list it


# ------------------------------------------------------------------ completion
def test_the_flags_come_from_the_options_list_not_from_the_examples_under_it(setup):
    trash = completion_cmd.options("trash")
    assert "--empty" in trash and "--match" not in trash and "--force" not in trash  # `trash --help` shows `bdnex import --match --force ...`
    assert "--force" in completion_cmd.options("import")


def test_completion_does_not_need_a_valid_configuration(tmp_path, monkeypatch):
    conf = tmp_path / "bad.yaml"
    conf.write_text("config_version: 2\nnonsense_option: 1\n")  # `ui.main` would stop here, and every command would get no flags
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    assert "--pretend" in completion_cmd.options("trash")
    assert conf.read_text() == "config_version: 2\nnonsense_option: 1\n"


def test_the_scripts_split_file_names_at_newlines_only_and_zsh_loads_compinit_first():
    bash = completion_cmd.script("bash")
    assert "local IFS=$'\\n'" in bash and "mapfile -t" not in bash.replace("`mapfile`", "")
    zsh = completion_cmd.script("zsh")
    assert zsh.index("compinit") < zsh.index("bashcompinit")


# ------------------------------------------------------------------ pictures
def test_a_picture_whose_header_promises_gigabytes_is_refused_before_it_is_opened():
    assert covers.image_size(png_header(600, 900)) == (600, 900)
    bomb = png_header(30000, 30000)
    try:
        covers.check_cover_image(bomb)
    except covers.CoverError as err:
        assert "not opened" in str(err)
    else:
        raise AssertionError("a 900-megapixel picture was accepted")
    assert thumbnail.small_png(bomb) is None  # the terminal picture never decodes it either
    from bdnex.ui import cover_text

    assert cover_text.half_blocks(bomb) is None


def test_the_tmux_setting_is_asked_once_per_session(monkeypatch):
    import subprocess

    calls = []

    def fake(*args, **kwargs):
        calls.append(args)
        return type("R", (), {"stdout": "on\n"})()

    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,1,0")
    thumbnail._PASSTHROUGH.clear()
    assert thumbnail.passthrough() is True and thumbnail.passthrough() is True and thumbnail.passthrough() is True
    assert len(calls) == 1  # a match question started a process (up to 2 s if tmux hangs) every time
    thumbnail._PASSTHROUGH.clear()
