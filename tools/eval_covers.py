"""Measure how well a cover comparison tells a right match from a wrong one, on a library whose files are already linked.

Three kinds of pair, so that the answer does not rest on a guess:
  right      a file against the saved cover of its own album, for the matches that something other than the name confirms (the year, an author,
             the file's own tags: `confirm.assess` says ``confirmed``), the answer key of `tools/eval_matching.py`
  other      the same file against the cover of an album of ANOTHER SERIES: a wrong match for sure
  neighbour  the same file against the nearest OTHER VOLUME of its own series: the mistake the matcher can really make (and the hard case for a
             comparison, since the covers of a series often share a layout)
  flagged    the files `confirm.assess` calls contradicted or doubtful, against their own album: only a guess (a reissue, a date in the name...),
             some are right, so it is read last
For each score the report gives the AUC against each kind of wrong pair (1.0 is perfect, 0.5 a coin toss) and, for a threshold that flags at most 1, 2 or
5 % of the RIGHT pairs, how many of the wrong ones it catches. Nothing is written to the library; the files are only read.

    BDNEX_CONFIG=my.yaml uv run python tools/eval_covers.py run covers.json [--right 400] [--workers 6]
    uv run python tools/eval_covers.py report covers.json

The scores (all 0-100 except ``inliers``, a count):
  current      what bdnex does now: SIFT, Lowe ratio 0.6, share of the smaller image's keypoints that match
  ratio75      the same with a looser ratio (0.75): more matches, more false ones
  geometry     matches (ratio 0.75) that agree on ONE perspective transform (RANSAC): a true cover gives many, chance matches few
  geometry_n   the number of those agreeing matches (not a share)
  trimmed      geometry after cutting 6 % off every edge (a scan's border, a price sticker, a banner)
  dhash        a 64-bit difference hash: 100 - 100 * (bits that differ) / 64
  colour       the correlation of the two colour histograms
"""
import argparse
import json
import os
import random
import sys

HEIGHT = 600  # both pictures are scaled to this height first (as bdnex does)


# ----------------------------------------------------------------------------- the scores
def prepare(cv2, numpy, image: bytes, trim: float = 0.0):
    """Keypoints and descriptors of a picture scaled to HEIGHT (optionally with ``trim`` cut off every edge), or ``None``."""
    grey = cv2.imdecode(numpy.frombuffer(image, numpy.uint8), cv2.IMREAD_GRAYSCALE)
    if grey is None:
        return None
    rows, cols = grey.shape[:2]
    grey = cv2.resize(grey, (max(1, round(cols * HEIGHT / rows)), HEIGHT), interpolation=cv2.INTER_AREA)
    if trim:
        margin_y, margin_x = int(grey.shape[0] * trim), int(grey.shape[1] * trim)
        grey = grey[margin_y:-margin_y or None, margin_x:-margin_x or None]
    points, described = cv2.SIFT_create().detectAndCompute(grey, None)
    if described is None or len(points) < 2:
        return None
    return points, described


def matches_of(cv2, a, b, ratio: float):
    """The matches whose best candidate is clearly better than the second best (Lowe's ratio test)."""
    matcher = cv2.FlannBasedMatcher({"algorithm": 1, "trees": 5}, {})
    pairs = matcher.knnMatch(a[1], b[1], k=2)
    return [pair[0] for pair in pairs if len(pair) == 2 and pair[0].distance < ratio * pair[1].distance]


def share(good: int, a, b) -> float:
    return min(100.0, 100.0 * good / min(len(a[0]), len(b[0])))


def inliers(cv2, numpy, a, b, ratio: float = 0.75) -> int:
    """How many matches agree on one perspective transform of the first picture onto the second."""
    good = matches_of(cv2, a, b, ratio)
    if len(good) < 4:
        return 0
    source = numpy.float32([a[0][m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    target = numpy.float32([b[0][m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    transform, mask = cv2.findHomography(source, target, cv2.RANSAC, 5.0)
    return int(mask.sum()) if mask is not None else 0


def scores(local: bytes, online: bytes) -> dict:
    """Every score for one pair of pictures (``None`` for a score that could not be worked out)."""
    import cv2
    import numpy

    from bdnex.importer import covers

    out = {name: None for name in ("current", "ratio75", "geometry", "geometry_n", "trimmed", "dhash", "colour")}
    a, b = prepare(cv2, numpy, local), prepare(cv2, numpy, online)
    if a is not None and b is not None:
        out["current"] = share(len(matches_of(cv2, a, b, 0.6)), a, b)
        out["ratio75"] = share(len(matches_of(cv2, a, b, 0.75)), a, b)
        count = inliers(cv2, numpy, a, b)
        out["geometry"], out["geometry_n"] = share(count, a, b), float(count)
    ta, tb = prepare(cv2, numpy, local, 0.06), prepare(cv2, numpy, online, 0.06)
    if ta is not None and tb is not None:
        out["trimmed"] = share(inliers(cv2, numpy, ta, tb), ta, tb)
    ha, hb = covers.cover_hash(local), covers.cover_hash(online)
    if ha is not None and hb is not None:
        out["dhash"] = 100.0 - 100.0 * covers.hash_distance(ha, hb) / 64.0
    ca, cb = (cv2.imdecode(numpy.frombuffer(x, numpy.uint8), cv2.IMREAD_COLOR) for x in (local, online))
    if ca is not None and cb is not None:
        hist = [cv2.calcHist([cv2.cvtColor(x, cv2.COLOR_BGR2HSV)], [0, 1], None, [16, 16], [0, 180, 0, 256]) for x in (ca, cb)]
        for h in hist:
            cv2.normalize(h, h)
        out["colour"] = max(0.0, 100.0 * cv2.compareHist(hist[0], hist[1], cv2.HISTCMP_CORREL))
    return out


# ------------------------------------------------------------------------------ run / report
def one(item):
    """All the pairs of one file: its first page against the cover of its own album (``right``), of an album of another series
    (``other``), of the nearest other volume of its own series (``neighbour``) and, for the files the answer key doubts, of its own album (``flagged``)."""
    from bdnex.importer import covers

    path, fmt, pairs = item
    local = covers.front_cover(path, fmt)
    out = []
    for group, saved, label in pairs:
        if not local:
            out.append({"path": path, "group": group, "label": label, "error": "the file's first page could not be read"})
            continue
        try:
            out.append({"path": path, "group": group, "label": label, "scores": scores(local, open(saved, "rb").read())})
        except Exception as err:
            out.append({"path": path, "group": group, "label": label, "error": f"{type(err).__name__}: {err}"})
    return out


def run(out_path: str, right: int, workers: int) -> None:
    from bdnex import parallel
    from bdnex.importer import confirm, covers
    from bdnex.library import Library
    from bdnex.ui import context

    settings = context.settings()
    rng = random.Random(7)
    with Library.for_settings(settings, readonly=True) as library:
        saved, by_series, albums = {}, {}, {}
        for album in library.query([]):
            full = library.get_album(album["id"])
            kept = full.get("cover_file") or covers.find_cover(settings.cover_path, full["source"], full["source_id"])
            if kept and (settings.cover_path / kept).exists():
                saved[full["id"]] = str(settings.cover_path / kept)
                label = f"{full.get('series') or ''} {full['title']} (vol {full.get('volume')})".strip()
                albums[full["id"]] = (full.get("series"), full.get("volume"), label)
                by_series.setdefault(full.get("series"), []).append(full["id"])
        candidates = {"right": [], "flagged": []}
        for row in library.query([], files=True):
            if row["id"] is None or row["id"] not in saved:
                continue
            file, album = library.get_file(row["path"]), library.get_album(row["id"])
            verdict = confirm.assess(row["path"], album, file["embedded"] or None).verdict
            group = {"confirmed": "right", "contradicted": "flagged", "doubtful": "flagged"}.get(verdict)
            if group:
                candidates[group].append((row["path"], file["format"], row["id"]))
    rng.shuffle(candidates["right"])
    ids = list(saved)
    chosen = []
    for path, fmt, album_id in candidates["right"][:right]:
        series, volume, label = albums[album_id]
        pairs = [("right", saved[album_id], label)]
        other = rng.choice(ids)  # another series: a wrong match for sure
        for _ in range(20):
            if albums[other][0] != series:
                break
            other = rng.choice(ids)
        else:
            other = None
        if other is not None:
            pairs.append(("other", saved[other], albums[other][2]))
        near = sorted((i for i in by_series.get(series, []) if i != album_id and series), key=lambda i: abs((albums[i][1] or 0) - (volume or 0)))
        if near:  # the next volume of the same series: the mistake the matcher can really make
            pairs.append(("neighbour", saved[near[0]], albums[near[0]][2]))
        chosen.append((path, fmt, pairs))
    for path, fmt, album_id in candidates["flagged"]:
        chosen.append((path, fmt, [("flagged", saved[album_id], albums[album_id][2])]))
    print(f"{len(candidates['right'])} right matches (sampling {min(right, len(candidates['right']))}), {len(candidates['flagged'])} flagged by the answer key: "
          f"{len(chosen)} files to compare, {workers} at a time", file=sys.stderr)
    results = []
    for number, (_, result) in enumerate(parallel.map_ordered(one, chosen, workers), start=1):
        results.extend(result if isinstance(result, list) else [{"error": str(result), "group": "?"}])
        if number % 50 == 0:
            print(f"  {number}/{len(chosen)} files", file=sys.stderr, flush=True)
    json.dump(results, open(out_path, "w"))


def auc(positive: list, negative: list) -> float:
    """The chance that a RIGHT match scores higher than a WRONG one (ties count half)."""
    if not positive or not negative:
        return float("nan")
    wins = sum((p > n) + 0.5 * (p == n) for p in positive for n in negative)
    return wins / (len(positive) * len(negative))


def add_combined(ok: list) -> None:
    """``combined``: the average, over the scores that see the picture differently, of where a pair stands among the RIGHT matches (0: lower than
    every right match, 100: higher than all). A pair that looks alike to SIFT or to the hash or by its matches that agree is alike."""
    parts = ("current", "geometry_n", "dhash")
    right = {name: sorted(e["scores"][name] for e in ok if e["group"] == "right" and e["scores"][name] is not None) for name in parts}
    for e in ok:
        ranks = [100.0 * sum(x <= e["scores"][name] for x in right[name]) / len(right[name]) for name in parts
                 if e["scores"][name] is not None and right[name]]
        e["scores"]["combined"] = sum(ranks) / len(ranks) if ranks else None


NEGATIVES = ("other", "neighbour", "flagged")


def examples(ok: list, name: str = "combined", count: int = 10) -> None:
    """The pairs that go against their group, to look at."""
    right = sorted((e for e in ok if e["group"] == "right" and e["scores"][name] is not None), key=lambda e: e["scores"][name])
    print(f"\nthe RIGHT matches that score lowest on `{name}` (another edition's cover? a first page that is no cover?):")
    for e in right[:count]:
        print(f"  {e['scores'][name]:>5.1f}  {os.path.basename(e['path'])[:70]}")
    for group, what in (("neighbour", "the file against ANOTHER VOLUME of its own series: a wrong match for sure"),
                        ("other", "the file against an album of ANOTHER SERIES: a wrong match for sure"),
                        ("flagged", "the files the answer key doubts, against their own album (may in fact be right)")):
        rows = sorted((e for e in ok if e["group"] == group and e["scores"][name] is not None), key=lambda e: -e["scores"][name])
        if rows:
            print(f"\n{what}, highest `{name}`:")
            for e in rows[:count]:
                print(f"  {e['scores'][name]:>5.1f}  {os.path.basename(e['path'])[:55]}   vs   {e.get('label', '')[:50]}")


def report(path: str) -> None:
    data = json.load(open(path))
    ok = [e for e in data if "scores" in e]
    add_combined(ok)
    counts = {g: sum(e["group"] == g for e in ok) for g in ("right",) + NEGATIVES}
    print(f"{len(ok)} pairs compared: " + ", ".join(f"{n} {g}" for g, n in counts.items()) + f"; {len(data) - len(ok)} could not be read")
    print("  right = the file against its own album's cover (the answer key says the match is right)")
    print("  other = against an album of another series; neighbour = against another volume of its own series (both wrong for sure)")
    print("  flagged = files the answer key doubts, against their own album (a guess: some are right)\n")
    names = list(ok[0]["scores"]) if ok else []
    print(f"{'score':<11} AUC vs: other neighbour flagged | wrong ones caught when 1 % / 2 % / 5 % of the RIGHT ones are flagged (threshold), for other and neighbour")
    for name in names:
        right = [e["scores"][name] for e in ok if e["group"] == "right" and e["scores"][name] is not None]
        if not right:
            continue
        values = {g: [e["scores"][name] for e in ok if e["group"] == g and e["scores"][name] is not None] for g in NEGATIVES}
        line = f"{name:<11}         " + " ".join(f"{auc(right, values[g]):>7.3f}" if values[g] else "      -" for g in NEGATIVES) + " |"
        ordered = sorted(right)
        for allowed in (0.01, 0.02, 0.05):
            threshold = ordered[min(len(ordered) - 1, int(allowed * len(ordered)))]
            caught = " ".join(f"{sum(x < threshold for x in values[g]) / len(values[g]):>4.0%}" if values[g] else "   -" for g in ("other", "neighbour"))
            line += f"  {caught} ({threshold:>4.1f})"
        print(line)
    examples(ok)
    print("\nread: `neighbour` is the hard test (the mistake the matcher really makes); a score with an AUC near 1.0 there separates a right match from the next volume. "
          "`flagged` is a guess: if its AUC is lower than the two others, many of those files are probably right.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=("run", "report"))
    ap.add_argument("file")
    ap.add_argument("--right", type=int, default=400, help="how many RIGHT matches to sample (all the WRONG ones are used)")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    run(args.file, args.right, args.workers) if args.mode == "run" else report(args.file)
