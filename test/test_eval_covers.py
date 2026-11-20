"""`tools/eval_covers.py`: its scores tell the same cover (rescaled, recompressed, with a border) from another one."""
import importlib.util
import os

import pytest

pytest.importorskip("cv2")

HERE = os.path.dirname(__file__)
spec = importlib.util.spec_from_file_location("eval_covers", os.path.join(HERE, "..", "tools", "eval_covers.py"))
eval_covers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(eval_covers)


def cover(seed: int, width: int = 400, height: int = 600, border: int = 0, fmt: str = "png") -> bytes:
    """A made-up cover: multi-scale blobs of colour (nothing repeats, so SIFT has distinctive features to find), cut to a size."""
    import cv2
    import numpy

    rng = numpy.random.default_rng(seed)
    base = numpy.zeros((600, 400, 3), numpy.float32)
    for sigma in (2, 5, 12, 30):
        layer = rng.standard_normal((600, 400, 3)).astype(numpy.float32)
        base += cv2.GaussianBlur(layer, (0, 0), sigma) * sigma
    base = (255 * (base - base.min()) / (base.max() - base.min())).astype(numpy.uint8)
    picture = cv2.resize(base, (width, height), interpolation=cv2.INTER_AREA)
    if border:
        picture[:border], picture[-border:], picture[:, :border], picture[:, -border:] = 230, 230, 230, 230
    ok, data = cv2.imencode("." + ("jpg" if fmt == "jpeg" else fmt), picture)
    return data.tobytes()


def test_the_same_cover_scores_high_whatever_its_size_compression_or_border_and_another_one_low():
    same = eval_covers.scores(cover(1), cover(1, width=300, height=450, border=6, fmt="jpeg"))
    other = eval_covers.scores(cover(1), cover(2))
    for name in ("current", "ratio75", "geometry", "trimmed"):
        assert same[name] > 40 and other[name] < 5, name
    assert same["geometry_n"] >= 50 and other["geometry_n"] == 0  # many matches agree on one transform, or none do
    assert same["trimmed"] >= same["current"]  # cutting the border off does not hurt


def test_auc_is_one_for_a_perfect_split_and_a_half_for_a_coin_toss():
    assert eval_covers.auc([50, 60, 70], [0, 5, 10]) == 1.0
    assert eval_covers.auc([5, 5], [5, 5]) == 0.5


def test_a_pair_is_read_from_a_file_and_a_saved_cover_and_the_report_says_which_score_separates(tmp_path, capsys):
    import json
    import zipfile

    items, results = [], []
    for number, (page, saved, group) in enumerate([(1, 1, "right"), (2, 2, "right"), (3, 3, "right"), (4, 5, "wrong"), (6, 7, "wrong")]):
        archive, picture = tmp_path / f"{number}.cbz", tmp_path / f"{number}.png"
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr("001.png", cover(page))
        picture.write_bytes(cover(saved, width=300, height=450, border=5))
        results.append(eval_covers.one((str(archive), "cbz", str(picture), group)))
    assert all("scores" in r for r in results), results
    path = tmp_path / "covers.json"
    path.write_text(json.dumps(results))
    eval_covers.report(str(path))
    shown = capsys.readouterr().out
    assert "5 pairs compared (3 right, 2 wrong)" in shown and "geometry" in shown and "AUC" in shown
    line = next(line for line in shown.splitlines() if line.startswith("geometry "))
    assert "1.000" in line  # the same covers score above the different ones
