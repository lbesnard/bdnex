import os
import sys

sys.path.insert(0, os.path.abspath(".."))

project = "bdnex"
author = "Laurent Besnard"
copyright = "Laurent Besnard"

extensions = ["myst_parser", "sphinx.ext.autodoc", "sphinx.ext.napoleon"]
source_suffix = {".rst": "restructuredtext", ".md": "markdown"}
exclude_patterns = ["_build"]
html_theme = "furo"
myst_heading_anchors = 3
