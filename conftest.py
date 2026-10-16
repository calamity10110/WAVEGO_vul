"""Make all repo test packages importable regardless of how pytest is
invoked: wavego_agent (repo root) and wavego_pet (under pet/)."""

import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
for _p in (_ROOT, os.path.join(_ROOT, "pet")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
