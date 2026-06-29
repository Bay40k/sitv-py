"""Tests for site assembly: planning, spacer suppression and cross-ref pages."""
import os

import pytest

from conftest import ROOT
from sitv.build import ConfigBuilder, InfCache, sanitize
from sitv.render import SPACER_IMAGE
from sitv.smindex import load_config


@pytest.fixture(scope="session")
def cache():
    if not os.path.isdir(os.path.join(ROOT, "input", "205", "1997")):
        pytest.skip("config data not present")
    return InfCache(os.path.join(ROOT, "input", "INF"))


@pytest.fixture
def builder(cache, tmp_path):
    """A freshly planned builder per test (cheap; the INF cache is shared)."""
    sm = load_config(os.path.join(ROOT, "input"), "205", "1997")
    b = ConfigBuilder(sm, cache, str(tmp_path))
    b.with_images = False
    b.plan()
    return b


# ---------------------------------------------------------------------------
# Filename sanitisation
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw, clean", [
    ("A-ENGINE <4G63>", "A-ENGINE 4G63"),
    ("FUEL/TANK", "FUELTANK"),
    ("TRAILING .  ", "TRAILING"),
    ("", "_"),
])
def test_sanitize(raw, clean):
    assert sanitize(raw) == clean


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------
def test_plan_creates_pages_and_targets(builder):
    assert len(builder.pages) > 1000
    # Each page registered its topicref as a link target.
    p = builder.pages[0]
    assert builder.target_for_ref[p.topicref] == p.rel_path


def test_unique_paths(builder):
    rels = [p.rel_path for p in builder.pages]
    assert len(rels) == len(set(r.lower() for r in rels)), "page paths collide"


# ---------------------------------------------------------------------------
# Cross-reference (ref) pages
# ---------------------------------------------------------------------------
def test_plan_refs_resolves_cross_links(builder):
    before = len(builder.pages)
    builder.plan_refs()
    # New standalone reference pages were added under _refs/.
    ref_pages = [p for p in builder.pages if p.rel_path.startswith("_refs/")]
    assert ref_pages, "expected reference pages to be generated"
    assert len(builder.pages) > before
    # Every generated ref page is registered so links can reach it.
    for p in ref_pages:
        assert builder.target_for_ref[p.topicref] == p.rel_path
    # A concrete cross-reference (intake manifold, linked from the proportioning
    # valve page) is now resolvable to a real reference page.
    assert "M15100300120" in builder.target_for_ref


def test_panel_index_keys_are_topicrefs(builder):
    index = builder._panel_index()
    inf_name, subs = index["M13100030254"]      # SERVICE SPECIFICATIONS
    assert inf_name.endswith(".INF")
    assert "01" in subs


# ---------------------------------------------------------------------------
# Spacer suppression
# ---------------------------------------------------------------------------
def test_spacer_dimensions_suppressed(builder):
    builder.with_images = True
    inf = builder.inf.get("205", "M2050000.INF")
    # offset 18144 is the 2x2 page-header spacer.
    assert builder._extract_image(inf, "M2050000.INF", 18144) == SPACER_IMAGE


def test_real_figure_not_suppressed(builder):
    builder.with_images = True
    inf = builder.inf.get("205", "M2052300.INF")
    rel = builder._extract_image(inf, "M2052300.INF", 159101)   # 736x532 figure
    assert rel and rel != SPACER_IMAGE and rel.endswith(".png")
    assert os.path.exists(os.path.join(builder.out_root, rel))
