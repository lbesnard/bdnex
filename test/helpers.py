"""Test helpers importable by dotted path (used for the `fetcher:` config option)."""


def fake_fetch(url):
    """A plain `url -> bytes` function, as named by `bedetheque.fetcher`."""
    return f"<html>{url}</html>".encode()
