"""Diagnose bdnex thumbnails: python thumbcheck.py FILE.cbz"""
import os, shutil, sys, time
from bdnex.ui import thumbnail as t
from bdnex.importer import covers
path = sys.argv[1]
print("stdout tty:", sys.stdout.isatty(), "columns:", shutil.get_terminal_size().columns, "(need >= 100)")
print("TMUX:", bool(os.environ.get("TMUX")), "passthrough:", t.passthrough(), "protocol:", t.protocol("auto"))
img = covers.front_cover(path, os.path.splitext(path)[1].lstrip(".").lower())
print("cover bytes:", len(img) if img else img)
small = t.small_png(img) if img else None
print("png:", None if small is None else (len(small[0]), small[1], small[2]))
kind = t.protocol("auto") or t.KITTY
print("drawing with", kind)
rows = t.show(img, kind)
print("rows:", rows)
time.sleep(0.2)
print("\n" * 12 + "done: a picture should be at the far right, 12 lines above")
