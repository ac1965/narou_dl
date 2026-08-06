"""Sphinx configuration for narou-dl.

ビルド方法::

    pip install sphinx sphinx-rtd-theme
    pip install -e ..            # narou_dl 本体をインポート可能にする
    sphinx-build -b html . _build
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(".."))

project = "narou-dl"
copyright = "2026, narou-dl contributors"
author = "narou-dl contributors"

try:
    from narou_dl import __version__ as release
except ImportError:  # narou_dl 未インストールでも設定自体は読めるようにする
    release = "unknown"
version = release

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
]

napoleon_google_docstring = True
napoleon_numpy_docstring = False
napoleon_include_init_with_doc = True
napoleon_use_ivar = False

autodoc_member_order = "bysource"
autodoc_typehints = "description"

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
}

language = "ja"

html_theme = "alabaster"
try:
    import sphinx_rtd_theme  # noqa: F401

    html_theme = "sphinx_rtd_theme"
except ImportError:
    pass
