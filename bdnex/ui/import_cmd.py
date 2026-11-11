"""``bdnex import``: match files to Bédéthèque albums and record them in the library."""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Optional

from bdnex.config import ConfigError, load_config
from bdnex.importer.bedetheque import Bedetheque, build_fetcher
from bdnex.importer.bedetheque.bedetheque import http_fetch
from bdnex.importer.bedetheque.sitemap import Candidate
from bdnex.importer.matching import Match
from bdnex.importer.session import ImportSession
from bdnex.library import Library


class InquirerPrompter:
    """Interactive prompts (InquirerPy)."""

    SKIP = "__skip__"

    def choose_match(self, path: str, matches: list[Match]) -> Optional[Candidate]:
        from InquirerPy import inquirer

        choices = [{"name": f"{m.score:5.1f}  {m.candidate.url.rsplit('/', 1)[-1]}", "value": m.candidate}
                   for m in matches]
        choices.append({"name": "Skip this file", "value": self.SKIP})
        answer = inquirer.select(message=f"Match for {path}", choices=choices).execute()
        return None if answer == self.SKIP else answer

    def duplicate_action(self, path: str, others: list[str], what: str) -> str:
        from InquirerPy import inquirer

        return inquirer.select(
            message=f"{what}: {path}\n  already: {', '.join(others)}",
            choices=[{"name": "Skip", "value": "skip"},
                     {"name": "Keep both", "value": "keep_both"},
                     {"name": "Replace (the new file becomes the album's file)", "value": "replace"}],
        ).execute()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bdnex import", description=__doc__)
    parser.add_argument("paths", nargs="+", help="comic files or folders to import")
    parser.add_argument("-n", "--pretend", action="store_true",
                        help="show what would be matched; change nothing")
    parser.add_argument("--force", action="store_true",
                        help="never ask: apply the best match (strong or medium), re-import known files")
    parser.add_argument("--recache", action="store_true",
                        help="refetch sitemaps and album pages instead of using the cache")
    return parser


def run(argv: list[str], out=sys.stdout) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = load_config().settings
    except ConfigError as err:
        print(err, file=sys.stderr)
        return 1

    with Library(settings.library_path) as library:
        source = Bedetheque(library, fetch=build_fetcher(settings), sitemap_fetch=http_fetch,
                            delay=tuple(settings.bedetheque.delay))
        session = ImportSession(library, source, settings, prompter=InquirerPrompter(),
                                pretend=args.pretend, force=args.force, recache=args.recache)
        report = session.run(args.paths)

    for r in report.results:
        score = f"{r.score:5.1f}" if r.score is not None else "  -  "
        print(f"{r.outcome:<9} {score}  {r.path}" + (f"  ({r.note})" if r.note else ""), file=out)
    summary = ", ".join(f"{report.count(o)} {o}" for o in
                        ("tagged", "partial", "skipped", "duplicate", "failed", "pretend") if report.count(o))
    print(f"\n{len(report.results)} file(s): {summary or 'nothing to do'}", file=out)
    return 1 if report.count("failed") else 0


