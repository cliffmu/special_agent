"""Integration-wide constants."""

from __future__ import annotations

import os


PACKAGE_DIR = os.path.dirname(os.path.dirname(__file__))
DATA_DIR = os.path.join(PACKAGE_DIR, "data")
VECTOR_INDEX_DIR = os.path.join(DATA_DIR, "vector_index")

