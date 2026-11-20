"""Measure how well a cover comparison tells a right match from a wrong one, on a library whose files are already linked.

The answer key is the one of `tools/eval_matching.py`: a link that something other than the name confirms (the year, an author, the
file's own tags: `confirm.assess` says ``confirmed``) is a RIGHT match, one that it calls ``contradicted`` or ``doubtful`` is probably a
WRONG one. For each, the file's first page is compared with the album's saved cover by several scores; the report says, per score, how
well it separates the two (AUC: 1.0 is perfect, 0.5 is a coin toss) and, for a threshold that flags at most 1, 2 or 5 % of the RIGHT
matches, how many of the WRONG ones it catches. Nothing is written to the library; the files are only read.

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
    from bdnex.importer import covers

    path, fmt, saved, group = item
    local = covers.front_cover(path, fmt)
    if not local:
        return {"path": path, "group": group, "error": "the file's first page could not be read"}
    try:
        online = open(saved, "rb").read()
        return {"path": path, "group": group, "scores": scores(local, online)}
    except Exception as err:
        return {"path": path, "group": group, "error": f"{type(err).__name__}: {err}"}


def run(out_path: str, right: int, workers: int) -> None:
    from bdnex import parallel
    from bdnex.importer import confirm, covers
    from bdnex.library import Library
    from bdnex.ui import context

    settings = context.settings()
    items = {"right": [], "wrong": []}
    with Library.for_settings(settings, readonly=True) as library:
        for row in library.query([], files=True):
            if row["id"] is None:
                continue
            file, album = library.get_file(row["path"]), library.get_album(row["id"])
            kept = album.get("cover_file") or covers.find_cover(settings.cover_path, album["source"], album["source_id"])
            if not kept or not (settings.cover_path / kept).exists():
                continue
            verdict = confirm.assess(row["path"], album, file["embedded"] or None).verdict
            group = {"confirmed": "right", "contradicted": "wrong", "doubtful": "wrong"}.get(verdict)
            if group:
                items[group].append((row["path"], file["format"], str(settings.cover_path / kept), group))
    random.Random(7).shuffle(items["right"])
    chosen = items["right"][:right] + items["wrong"]
    print(f"{len(items['right'])} right matches (sampling {min(right, len(items['right']))}), {len(items['wrong'])} wrong ones: "
          f"{len(chosen)} pairs to compare, {workers} at a time", file=sys.stderr)
    results = []
    for number, (_, result) in enumerate(parallel.map_ordered(one, chosen, workers), start=1):
        results.append(result if isinstance(result, dict) else {"error": str(result), "group": "?"})
        if number % 50 == 0:
            print(f"  {number}/{len(chosen)}", file=sys.stderr, flush=True)
    json.dump(results, open(out_path, "w"))


def auc(positive: list, negative: list) -> float:
    """The chance that a RIGHT match scores higher than a WRONG one (ties count half)."""
    if not positive or not negative:
        return float("nan")
    wins = sum((p > n) + 0.5 * (p == n) for p in positive for n in negative)
    return wins / (len(positive) * len(negative))


def report(path: str) -> None:
    data = json.load(open(path))
    ok = [e for e in data if "scores" in e]
    print(f"{len(ok)} pairs compared ({sum(e['group'] == 'right' for e in ok)} right, {sum(e['group'] == 'wrong' for e in ok)} wrong); "
          f"{len(data) - len(ok)} could not be read\n")
    names = list(ok[0]["scores"]) if ok else []
    print(f"{'score':<11} {'AUC':>5} | of the RIGHT matches flagged: 1 %  2 %  5 % -> WRONG ones caught (threshold)")
    for name in names:
        right = [e["scores"][name] for e in ok if e["group"] == "right" and e["scores"][name] is not None]
        wrong = [e["scores"][name] for e in ok if e["group"] == "wrong" and e["scores"][name] is not None]
        if not right or not wrong:
            continue
        line = f"{name:<11} {auc(right, wrong):>5.3f} |"
        ordered = sorted(right)
        for allowed in (0.01, 0.02, 0.05):
            threshold = ordered[min(len(ordered) - 1, int(allowed * len(ordered)))]  # at most `allowed` of the right ones score below it
            caught = sum(x < threshold for x in wrong) / len(wrong)
            line += f"  {caught:>4.0%} ({threshold:>4.1f})"
        print(line)
    print("\nread: a score that catches many WRONG matches while flagging only 1-2 % of the RIGHT ones is the one to use; "
          "AUC near 1.0 means it separates them well whatever the threshold.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=("run", "report"))
    ap.add_argument("file")
    ap.add_argument("--right", type=int, default=400, help="how many RIGHT matches to sample (all the WRONG ones are used)")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    run(args.file, args.right, args.workers) if args.mode == "run" else report(args.file)
