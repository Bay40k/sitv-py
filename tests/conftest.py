"""Shared pytest fixtures.

The tests run against the real input data shipped in ``input/`` (the whole point
of the project is to decode *these* bytes), but they are scoped to a couple of
small INF files and never write a full site, so the suite stays fast.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

INF_DIR = os.path.join(ROOT, "input", "INF", "205")


def _require(path):
    if not os.path.exists(path):
        pytest.skip(f"input data not present: {path}")
    return path


@pytest.fixture(scope="session")
def inf_general():
    """M2051300.INF — contains tables, links and a panel name we assert on."""
    from sitv.inf import InfFile
    return InfFile(_require(os.path.join(INF_DIR, "M2051300.INF")))


@pytest.fixture(scope="session")
def inf_root():
    """M2050000.INF — holds the 25x20 RLE icon and 2x2 spacer."""
    from sitv.inf import InfFile
    return InfFile(_require(os.path.join(INF_DIR, "M2050000.INF")))


@pytest.fixture(scope="session")
def inf_trans():
    """M2052300.INF — holds the 736x532 RLE line-art figure."""
    from sitv.inf import InfFile
    return InfFile(_require(os.path.join(INF_DIR, "M2052300.INF")))
