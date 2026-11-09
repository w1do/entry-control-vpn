"""pytest configuration for backend/tests/compiler."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

COMPILER_TESTS_DIR = Path(__file__).resolve().parent
if str(COMPILER_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(COMPILER_TESTS_DIR))


@pytest.fixture()
def entry_base_dir(tmp_path: Path) -> str:
    """Absolute node base_dir; xray run -test opens <base_dir>/log files."""
    base = tmp_path / "node"
    (base / "log").mkdir(parents=True, exist_ok=True)
    return str(base)
