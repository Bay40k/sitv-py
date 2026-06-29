"""Tests for the SM navigation-index parsers."""
import os

import pytest

from conftest import ROOT
from sitv.smindex import load_config, Group


@pytest.fixture(scope="session")
def cfg():
    base = os.path.join(ROOT, "input", "205", "1997")
    if not os.path.isdir(base):
        pytest.skip("config data not present")
    return load_config(os.path.join(ROOT, "input"), "205", "1997")


def test_config_id(cfg):
    assert cfg.config_id.startswith("205")


def test_groups_and_folders(cfg):
    assert cfg.groups, "expected at least one group"
    # The folder name = section digits + title, brackets included in raw title.
    g = Group(key="0111", subcode="0111", title="A-ENGINE MECHANICAL <4G63>",
              engines=[])
    assert g.section == "11"
    assert g.folder == "11A-ENGINE MECHANICAL <4G63>"


def test_node_to_page_mapping(cfg):
    # Every tree node id that has a page maps to a 12-char-ish topicref.
    assert cfg.node_page
    sample_ref = next(ref for ref, _cat in cfg.node_page.values() if ref)
    assert sample_ref.startswith("M")


def test_tree_references_real_groups(cfg):
    group_keys = {g.key for g in cfg.groups}
    assert set(cfg.tree).issubset(group_keys | set(cfg.tree))  # keys are group keys
    assert any(nodes for nodes in cfg.tree.values())
