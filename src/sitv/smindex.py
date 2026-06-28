"""Parsers for the SITV 'SM' index files.

These ASCII control-character delimited files hold all the navigation / table
of contents metadata (the INF files hold the actual content).  Records are
separated by 0x01; sub-fields by 0x02 and 0x03.

Layout of each SM file (reverse engineered):

  SM00016F (root, one per disc)  : title string of the whole manual
  <model>/<year>/SM00019F        : config id, e.g. "205205"
  SM00001F : ordered group list   groupkey(4)+subcode(4)+title [\x02 engcode]*
  SM00004F : group tree           groupkey(4) {\x02 nodeid(8)+kind(2)}*
  SM00005F : category names        catcode(4)+name
  SM00006F : category -> nodes     catcode(4) {\x02 nodeid(8)}*
  SM00007F : keyword dictionary    KEYWORD \x02 code(3) \x03 Keyword
  SM00008F : keyword -> nodes      code(3) {\x02 nodeid(8)}*
  SM00009F : group -> INF file     groupkey(4)+filename
  SM00010F : node -> page          nodeid(8)+topicref(12, padded 15)+category(6)
  SM00011F : page -> node          topicref(12, padded 15)+nodeid(8)
  SM00012F : node -> engines       nodeid(8) {\x02 engcode(4)}*
  SM00013F : page -> sub-pages     topicref(12, padded 15) {sub(2)}*
  SM00014F : component dictionary  code \x02 UPPER \x03 Mixed
  SM00015F : component -> nodes    code {\x02 nodeid(9) [\x03 fig(2)]}*
  SM00017F : node -> title         nodeid(8)+title
  SM00019F : config id
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

SOH = "\x01"
STX = "\x02"
ETX = "\x03"


def _read(path: str) -> str:
    with open(path, "rb") as fh:
        return fh.read().decode("cp850", "replace")


def _records(text: str) -> List[str]:
    return [r for r in text.split(SOH) if r != ""]


@dataclass
class Group:
    key: str
    subcode: str
    title: str          # raw, includes leading section letter and <..>
    engines: List[str]

    @property
    def section(self) -> str:
        # e.g. subcode 0111 -> "11", 0000 -> "00", 1011 -> "11"
        return self.subcode[2:4]

    @property
    def folder(self) -> str:
        return self.section + self.title


@dataclass
class SMConfig:
    """All SM metadata for one model/year configuration."""
    model: str
    year: str
    config_id: str
    groups: List[Group]
    tree: Dict[str, List[Tuple[str, str]]]            # groupkey -> [(nodeid, kind)]
    categories: Dict[str, str]                         # catcode -> name
    category_nodes: Dict[str, List[str]]
    inf_for_group: Dict[str, str]                      # groupkey -> filename
    node_page: Dict[str, Tuple[str, str]]              # nodeid -> (topicref, category6)
    page_node: Dict[str, str]                          # topicref -> nodeid
    node_engines: Dict[str, List[str]]                 # nodeid -> engines
    page_subpages: Dict[str, List[str]]                # topicref -> [sub]
    node_title: Dict[str, str]                         # nodeid -> title
    keywords: Dict[str, Tuple[str, str]]               # code -> (UPPER, Mixed)
    keyword_nodes: Dict[str, List[str]]

    def inf_for_node(self, nodeid: str) -> Optional[str]:
        return self.inf_for_group.get(nodeid[:4])


def _parse_sm0001(text: str) -> List[Group]:
    groups = []
    for rec in _records(text):
        parts = rec.split(STX)
        head = parts[0]
        key, subcode, title = head[:4], head[4:8], head[8:]
        groups.append(Group(key, subcode, title, parts[1:]))
    return groups


def _parse_keyed_list(text: str, keylen: int, idlen: int) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for rec in _records(text):
        parts = rec.split(STX)
        key = parts[0][:keylen]
        out[key] = [p[:idlen] for p in parts[1:]]
    return out


def _parse_sm0004(text: str) -> Dict[str, List[Tuple[str, str]]]:
    out: Dict[str, List[Tuple[str, str]]] = {}
    for rec in _records(text):
        parts = rec.split(STX)
        key = parts[0]
        nodes = []
        for p in parts[1:]:
            nodes.append((p[:8], p[8:10]))
        out[key] = nodes
    return out


def _parse_prefix_map(text: str, keylen: int) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for rec in _records(text):
        out[rec[:keylen]] = rec[keylen:]
    return out


def _parse_sm0010(text: str) -> Dict[str, Tuple[str, str]]:
    out: Dict[str, Tuple[str, str]] = {}
    for rec in _records(text):
        nodeid = rec[:8]
        topicref = rec[8:23].rstrip()
        category = rec[23:29]
        out[nodeid] = (topicref, category)
    return out


def _parse_sm0011(text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for rec in _records(text):
        topicref = rec[:15].rstrip()
        nodeid = rec[15:23]
        out[topicref] = nodeid
    return out


def _parse_sm0013(text: str) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for rec in _records(text):
        parts = rec.split(STX)
        topicref = parts[0].rstrip()
        subs = [p for p in parts[1:]]
        out[topicref] = subs
    return out


def _parse_sm0005(text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for rec in _records(text):
        out[rec[:4]] = rec[4:]
    return out


def _parse_sm0007(text: str) -> Dict[str, Tuple[str, str]]:
    out: Dict[str, Tuple[str, str]] = {}
    for rec in _records(text):
        # KEYWORD \x02 code \x03 Keyword
        a = rec.split(STX)
        upper = a[0]
        rest = a[1] if len(a) > 1 else ""
        code, _, mixed = rest.partition(ETX)
        out[code] = (upper, mixed)
    return out


def _parse_sm0012(text: str) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for rec in _records(text):
        parts = rec.split(STX)
        out[parts[0][:8]] = [p[:4] for p in parts[1:]]
    return out


def load_config(input_dir: str, model: str, year: str) -> SMConfig:
    base = os.path.join(input_dir, model, year)

    def rd(name: str) -> str:
        return _read(os.path.join(base, name))

    groups = _parse_sm0001(rd("SM00001F"))
    tree = _parse_sm0004(rd("SM00004F"))
    categories = _parse_sm0005(rd("SM00005F"))
    category_nodes = _parse_keyed_list(rd("SM00006F"), 4, 8)
    keywords = _parse_sm0007(rd("SM00007F"))
    keyword_nodes = _parse_keyed_list(rd("SM00008F"), 3, 8)
    inf_for_group = _parse_prefix_map(rd("SM00009F"), 4)
    node_page = _parse_sm0010(rd("SM00010F"))
    page_node = _parse_sm0011(rd("SM00011F"))
    node_engines = _parse_sm0012(rd("SM00012F"))
    page_subpages = _parse_sm0013(rd("SM00013F"))
    node_title = _parse_prefix_map(rd("SM00017F"), 8)
    config_id = rd("SM00019F").strip()

    return SMConfig(
        model=model, year=year, config_id=config_id, groups=groups, tree=tree,
        categories=categories, category_nodes=category_nodes,
        inf_for_group=inf_for_group, node_page=node_page, page_node=page_node,
        node_engines=node_engines, page_subpages=page_subpages,
        node_title=node_title, keywords=keywords, keyword_nodes=keyword_nodes,
    )
