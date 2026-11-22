"""Measure the matcher on a library whose files are already linked: how often does it give the album the file has?

A library that has been through an import links each file to an album. Some of those links are confirmed by something
the name matching does not use (the year, an author, the file's own tags: ``confirm.assess`` says ``confirmed``).
Those files are the answer key. The matcher is run again on them, and the report says, per score, how often its first
choice is the album the file has, and what each safeguard (a margin to the second choice, a veto when the page
contradicts the file) would keep and drop. Nothing is written to the library; no file is read (the tags come from the
library).

    BDNEX_CONFIG=my.yaml uv run python tools/eval_matching.py run results.json [--sample N]
    uv run python tools/eval_matching.py report results.json
"""
import argparse
import json
import os
import random
import sys


def run(out_path: str, sample: int | None) -> None:
    from bdnex.importer import confirm, matching
    from bdnex.importer.registry import create_source
    from bdnex.library import Library
    from bdnex.ui import context

    settings = context.settings()
    from bdnex.importer.registry import offline_settings

    offline = offline_settings(settings)
    with Library.for_settings(settings, readonly=True) as library:
        source = create_source(settings.source, library, offline)
        source.warm()
        files = [f for f in library.query([], files=True) if f["id"] is not None]
        if sample:
            random.seed(5)
            files = random.sample(files, min(sample, len(files)))
        embedded = {}
        matching._embedded_of = lambda source, path: embedded.get(path)  # the tags are in the library: read no file
        results = []
        for number, row in enumerate(files, start=1):
            path = row["path"]
            file, album = library.get_file(path), library.get_album(row["id"])
            embedded[path] = file["embedded"] or {}
            found = matching.find_matches(source, path, limit=5)
            evidence = confirm.assess(path, album, file["embedded"] or None)
            results.append({"path": path, "album": album["source_id"], "verdict": evidence.verdict,
                            "reason": evidence.reason, "doubt": found[0].doubt if found else "", "matches": [[m.candidate.source_id, round(m.score, 1)] for m in found]})
            if number % 500 == 0:
                print(f"{number}/{len(files)}", file=sys.stderr, flush=True)
    json.dump(results, open(out_path, "w"))


def top(entry, skip_same=True):
    matches = entry["matches"]
    first = matches[0] if matches else None
    second = next((m for m in matches[1:] if m[0] != (first or [None])[0]), None)
    return first, second


def report(path: str) -> None:
    data = json.load(open(path))
    truth = [e for e in data if e["verdict"] == "confirmed"]
    looks_wrong = [e for e in data if e["verdict"] in ("doubtful", "contradicted")]
    print(f"{len(data)} linked files; answer key: {len(truth)} confirmed by year, author or the file's own tags; "
          f"{len(looks_wrong)} look wrong.\n")

    def ok(entry):
        first, _ = top(entry)
        return first is not None and first[0] == entry["album"]

    print("first choice vs the answer key, by its score:")
    print(f"{'score':>8} {'files':>6} {'right':>6} {'precision':>9}")
    for low, high in ((100, 101), (95, 100), (90, 95), (85, 90), (80, 85), (70, 80), (0, 70)):
        rows = [e for e in truth if top(e)[0] and low <= top(e)[0][1] < high]
        if rows:
            right = sum(ok(e) for e in rows)
            print(f"{low:>3}-{high - 1 if high > 100 else high:<4} {len(rows):>6} {right:>6} {right / len(rows):>9.1%}")
    print("\napplied unasked at a threshold (first choice >= it): how many, and how many are wrong")
    for threshold in (100, 95, 90, 85, 80, 70):
        rows = [e for e in truth if top(e)[0] and top(e)[0][1] >= threshold]
        wrong = [e for e in rows if not ok(e)]
        print(f"  >= {threshold:<3} {len(rows):>5} applied, {len(wrong):>3} wrong ({len(wrong) / max(1, len(rows)):.1%}); "
              f"{len(truth) - len(rows)} left to ask")
    print("\na margin to the second choice (strong only when first - second >= m):")
    for margin in (0, 2, 5, 10):
        strong = [e for e in truth if top(e)[0] and top(e)[0][1] >= 90]
        kept = [e for e in strong if top(e)[1] is None or top(e)[0][1] - top(e)[1][1] >= margin]
        dropped = [e for e in strong if e not in kept]
        wrong_kept = sum(not ok(e) for e in kept)
        print(f"  m={margin:<3} kept {len(kept):>5} (wrong {wrong_kept:>3}), asked instead {len(dropped):>4} "
              f"(of which right {sum(ok(e) for e in dropped)})")
    sure = [e for e in truth if top(e)[0] and top(e)[0][1] >= 90 and not e.get("doubt")]
    print(f"\nas the matcher now decides (>= 90 and no reason to doubt it): {len(sure)} applied unasked, "
          f"{sum(not ok(e) for e in sure)} wrong; {sum(1 for e in truth if top(e)[0] and top(e)[0][1] >= 90 and e.get('doubt'))} "
          f"strong ones are asked about instead")
    sure_bad = [e for e in looks_wrong if top(e)[0] and top(e)[0][1] >= 90 and not e.get("doubt")]
    print(f"files that look wrong and are still applied unasked: {len(sure_bad)} of {len(looks_wrong)}")
    strong_bad = [e for e in looks_wrong if top(e)[0] and top(e)[0][1] >= 90]
    print(f"\nfiles that look wrong and would be applied unasked (>= 90): {len(strong_bad)} of {len(looks_wrong)}; "
          f"the matcher still picks the album they have for {sum(ok(e) for e in strong_bad)} of them")
    for e in random.Random(2).sample(strong_bad, min(12, len(strong_bad))):
        print(f"  {os.path.basename(e['path'])[:60]:<60} {e['reason'][:110]}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=("run", "report"))
    ap.add_argument("file")
    ap.add_argument("--sample", type=int)
    args = ap.parse_args()
    run(args.file, args.sample) if args.mode == "run" else report(args.file)
