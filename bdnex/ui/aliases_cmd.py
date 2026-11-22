"""``bdnex aliases``: list your short commands, add one or remove one (the configuration file is edited for you)."""

from __future__ import annotations

import argparse
import json
import sys

import yaml

from bdnex.config import ConfigError, build_settings, ensure_user_config, load_defaults
from bdnex.config_template import set_aliases
from bdnex.ui.commands import parse_alias
from bdnex.ui.output import add_pretend


def _name(text: str) -> str:
    return str(text).partition("=")[0].strip()


def _own(user: dict) -> list[str]:
    value = user.get("aliases")
    return [str(item) for item in value] if isinstance(value, list) else []


def listing(ships: list[str], mine: list[str]) -> list[dict]:
    """Every alias at work, in the order they are listed: where it comes from, what it runs, and what it is for."""
    mine_names = {_name(text) for text in mine}
    gone = {_name(text) for text in mine if "=" in text and not text.partition("=")[2].strip().split(" # ")[0].strip()}
    rows = []
    for text in ships:
        if _name(text) in mine_names:
            continue
        name, words, why = parse_alias(text)
        rows.append({"name": name, "command": "bdnex " + " ".join(words), "why": why, "from": "bdnex"})
    for text in mine:
        if _name(text) in gone:
            continue
        try:
            name, words, why = parse_alias(text)
        except ValueError as err:  # hand-edited and wrong: say so here (`--rm NAME` removes it), never a traceback
            rows.append({"name": _name(text), "command": "", "why": str(err), "from": "broken (ignored)"})
            continue
        shipped = name in {_name(item) for item in ships}
        rows.append({"name": name, "command": "bdnex " + " ".join(words), "why": why,
                     "from": "yours (replaces bdnex's)" if shipped else "yours"})
    return rows


def run(argv: list[str], out=sys.stdout) -> int:
    parser = argparse.ArgumentParser(
        prog="bdnex aliases", description=__doc__,
        epilog="An alias is a name for a bdnex command and its options: `bdnex settle-doubtful-matches -n` runs "
               "`bdnex review --auto -n` (what you type after the name is added). bdnex ships some; yours are added to them "
               "(the same name replaces one). --add and --rm write the `aliases:` list of your configuration file (the "
               "previous file is kept as `.bak`, your comments elsewhere stay). Removing one of bdnex's writes `NAME =`, "
               "which switches it off for you.")
    parser.add_argument("--add", metavar='"NAME = COMMAND  # WHY"', action="append", default=[],
                        help="add an alias (or replace the one with that name): a long name that says what it does, the "
                             "command with its options, and after ` # ` what it is for; repeatable")
    parser.add_argument("--rm", metavar="NAME", action="append", default=[],
                        help="remove an alias of yours, or switch off one of bdnex's; repeatable")
    parser.add_argument("--json", action="store_true", help="list them as JSON")
    add_pretend(parser, "change")
    args = parser.parse_args(argv)

    path, _ = ensure_user_config()
    text = path.read_text(encoding="utf-8")
    ships = [str(item) for item in load_defaults().get("aliases") or []]
    mine = _own(yaml.safe_load(text) or {})
    changed = False

    for new in args.add:
        try:
            name = parse_alias(new)[0]
        except ValueError as err:
            print(err, file=sys.stderr)
            return 2
        mine = [item for item in mine if _name(item) != name] + [new.strip()]
        print(f"{'Would add' if args.pretend else 'Added'} {name}", file=out)
        changed = True
    for name in args.rm:
        if any(_name(item) == name and parse_alias_ok(item) for item in mine):
            mine = [item for item in mine if _name(item) != name]
            back = " (bdnex's own of that name is back)" if name in {_name(s) for s in ships} else ""
            print(f"{'Would remove' if args.pretend else 'Removed'} {name}{back}", file=out)
        elif any(_name(item) == name and not parse_alias_ok(item) for item in mine):
            mine = [item for item in mine if _name(item) != name]  # a switched-off or broken entry: removing it brings back bdnex's own
            print(f"{'Would remove' if args.pretend else 'Removed'} {name} (bdnex's own of that name is back, if there is one)", file=out)
        elif name in {_name(s) for s in ships} and not any(_name(item) == name for item in mine):
            mine.append(f"{name} =")
            print(f"{'Would switch off' if args.pretend else 'Switched off'} {name} (one of bdnex's)", file=out)
        else:
            print(f"No alias called {name!r} (`bdnex aliases` lists them).", file=sys.stderr)
            return 1
        changed = True

    if changed:
        new_text = set_aliases(text, mine)
        try:
            build_settings(yaml.safe_load(new_text) or {})  # the whole file is checked before it is written
        except ConfigError as err:
            print(f"Nothing changed: {err}", file=sys.stderr)
            return 1
        if not args.pretend:
            from bdnex.config import write_config_file

            backup = write_config_file(path, new_text) or path.with_name(path.name + ".bak")
            print(f"Updated {path} (the previous file is {backup.name}).", file=out)
        text = new_text
        mine = _own(yaml.safe_load(text) or {}) if not args.pretend else mine

    rows = listing(ships, mine)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2), file=out)
        return 0
    from bdnex.ui import color

    on = color.enabled(out)
    if changed:
        print(file=out)
    width = max((len(row["name"]) for row in rows), default=0)
    print(color.hints(f"{color.role(str(len(rows)), 'count', on)} alias(es). `bdnex aliases --add \"NAME = COMMAND  # WHY\"` "
                      "adds one, `--rm NAME` removes one.\n", on), file=out)
    for row in rows:
        mine = row["from"] != "bdnex"  # yours stand out: they are the ones you can change
        shown = f"{row['name']:<{width}}"
        print(f"  {color.role(shown, 'good' if mine else 'name', on)}  {row['command']}" + (f"   {color.role('# ' + row['why'], 'note', on)}" if row["why"] else "")
              + (f"   {color.role('[' + row['from'] + ']', 'good', on)}" if mine else ""), file=out)
    return 0


def parse_alias_ok(text: str) -> bool:
    """Is ``text`` an alias of yours (not the ``NAME =`` that switches one of bdnex's off)?"""
    try:
        parse_alias(text)
    except ValueError:
        return False
    return True
