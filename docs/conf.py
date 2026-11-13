import os
import sys

sys.path.insert(0, os.path.abspath(".."))

project = "bdnex"
author = "Laurent Besnard"
copyright = "Laurent Besnard"

# The version of the package being documented: Read the Docs installs it for each tag it builds, so
# the page title says which release a page belongs to. Without the package installed, no version.
try:
    from importlib.metadata import PackageNotFoundError, version as _version

    release = _version("bdnex")
    version = ".".join(release.split(".")[:2])
except PackageNotFoundError:
    release = version = ""

extensions = ["myst_parser", "sphinx.ext.autodoc", "sphinx.ext.napoleon"]
source_suffix = {".rst": "restructuredtext", ".md": "markdown"}
exclude_patterns = ["_build"]
html_theme = "furo"
html_static_path = ["_static"]
html_logo = "_static/bdnex-logo.png"  # transparent: works in the light and the dark theme
myst_heading_anchors = 3
