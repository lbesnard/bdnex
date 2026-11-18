"""The program that reads CBR files: 7z is the last choice (a 7z without the RAR codec fails on every page)."""

import pytest

rarfile = pytest.importorskip("rarfile")

from bdnex import rartool  # noqa: E402


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(rartool, "_chosen", False)


def test_7z_is_only_used_when_nothing_else_reads_rar(monkeypatch):
    asked = []

    def tool_setup(force=False, **kwargs):
        asked.append(kwargs)
        if not kwargs:
            return object()
        raise rarfile.RarCannotExec("nothing but 7z")

    monkeypatch.setattr(rarfile, "tool_setup", tool_setup)
    rartool.ensure()
    assert asked == [{"sevenzip": False, "sevenzip2": False}, {}]


def test_unrar_unar_or_bsdtar_win_over_7z(monkeypatch):
    asked = []
    monkeypatch.setattr(rarfile, "tool_setup", lambda force=False, **kw: asked.append(kw) or object())
    rartool.ensure()
    rartool.ensure()  # once only
    assert asked == [{"sevenzip": False, "sevenzip2": False}]


def test_no_tool_at_all_is_not_a_crash(monkeypatch):
    def none(force=False, **kwargs):
        raise rarfile.RarCannotExec("nothing")

    monkeypatch.setattr(rarfile, "tool_setup", none)
    rartool.ensure()  # the read of a CBR fails later, with rarfile's own message
