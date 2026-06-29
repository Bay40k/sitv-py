"""Regenerate a browsable HTML manual from the SITV input data.

For each model-year configuration this walks the SM navigation tree, renders
every page from its INF source, extracts images, and writes a clean static
HTML site (one page per node, with working cross links and a contents index).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from html import escape
from typing import Dict, List, Optional, Tuple

from .inf import InfFile, Topic
from .render import RenderContext, render_topic, get_panel_name, SPACER_IMAGE
from .smindex import SMConfig, Group, load_config

CONFIGS = [
    ("205", "1994"), ("205", "1995"), ("205", "1996"), ("205", "1997"),
    ("220", "1996"), ("220", "1997"),
]

_ILLEGAL = re.compile(r'[<>:"/\\|?*()\x00-\x1f]')

# SITV emits sub-pixel-tall bitmaps as structural layout elements: a 2x2 white
# page-header anchor at the top of every topic, plus 1000x2 ruler bars. Neither
# is a real figure; every genuine diagram is >= 10px on each side, so anything
# this small or smaller is suppressed.
SPACER_MAX_DIM = 2


def sanitize(name: str) -> str:
    """Match SITV's filename rule: drop bracket/illegal chars, trim."""
    name = _ILLEGAL.sub("", name)
    name = name.rstrip(" .")
    return name or "_"


@dataclass
class PageNode:
    nodeid: str
    title: str
    rel_path: str                 # path of html file relative to config root
    topicref: str
    inf_name: str
    subpages: List[str]


class InfCache:
    def __init__(self, inf_dir: str):
        self.inf_dir = inf_dir
        self._inf: Dict[str, InfFile] = {}
        self._panels: Dict[str, Dict[str, int]] = {}   # inf_name -> {panelname: idx}
        self._names: Dict[str, Dict[int, str]] = {}     # inf_name -> {idx: panelname}
        # (inf path, offset) -> (status, png bytes). Shared across configs so a
        # bitmap living in an INF used by several model-years is decoded and
        # PNG-encoded only once — by far the largest cost of a full build.
        self._png: Dict[Tuple[str, int], Tuple[str, Optional[bytes]]] = {}

    def get(self, model: str, inf_name: str) -> InfFile:
        path = os.path.join(self.inf_dir, model, inf_name)
        key = path
        if key not in self._inf:
            self._inf[key] = InfFile(path)
            self._build_panels(key, self._inf[key])
        return self._inf[key]

    def png(self, model: str, inf_name: str, offset: int) -> Tuple[str, Optional[bytes]]:
        """Decode the bitmap at ``offset`` and return ``(status, png_bytes)``.

        ``status`` is ``"ok"`` (with bytes), ``"spacer"`` (structural, drop it)
        or ``"fail"`` (undecodable). Results are memoised for the whole build.
        """
        inf = self.get(model, inf_name)
        key = (inf.path, offset)
        cached = self._png.get(key)
        if cached is None:
            cached = self._png[key] = _encode_png(inf, offset)
        return cached

    def _build_panels(self, key: str, inf: InfFile):
        pmap: Dict[str, int] = {}
        nmap: Dict[int, str] = {}
        for t in inf.topics:
            nm = get_panel_name(inf, t)
            if nm:
                pmap.setdefault(nm, t.index)
                nmap[t.index] = nm
        self._panels[key] = pmap
        self._names[key] = nmap

    def panels(self, model: str, inf_name: str) -> Dict[str, int]:
        self.get(model, inf_name)
        return self._panels[os.path.join(self.inf_dir, model, inf_name)]

    def names(self, model: str, inf_name: str) -> Dict[int, str]:
        self.get(model, inf_name)
        return self._names[os.path.join(self.inf_dir, model, inf_name)]


def _relpath(src_rel: str, dst_rel: str) -> str:
    src_dir = os.path.dirname(src_rel)
    return os.path.relpath(dst_rel, src_dir).replace(os.sep, "/")


class ConfigBuilder:
    def __init__(self, sm: SMConfig, inf_cache: InfCache, out_root: str):
        self.sm = sm
        self.model = sm.model
        self.inf = inf_cache
        self.out_root = out_root
        self.pages: List[PageNode] = []
        self.page_by_node: Dict[str, PageNode] = {}
        self.target_for_ref: Dict[str, str] = {}     # topicref/panelname -> rel_path
        # (inf_name,offset) -> rel path  (None => tried and failed to decode)
        self.image_files: Dict[Tuple[str, int], Optional[str]] = {}
        self.with_images = True
        self.img_ok = 0
        self.img_fail = 0
        self.tree_for_index: List[Tuple[int, str, Optional[str]]] = []  # level,title,relpath

    # -- pass 1: plan the tree -----------------------------------------
    def plan(self):
        used_paths: set = set()
        for g in self.sm.groups:
            folder = sanitize(g.folder)
            inf_name = self.sm.inf_for_group.get(g.key)
            nodes = self.sm.tree.get(g.key, [])
            self.tree_for_index.append((0, g.folder, None))
            current_section: Optional[str] = None
            for nodeid, kind in nodes:
                title = self.sm.node_title.get(nodeid, nodeid)
                if kind == "10":
                    current_section = sanitize(title)
                    self.tree_for_index.append((1, title, None))
                    continue
                topicref, _cat = self.sm.node_page.get(nodeid, ("", ""))
                if kind == "01" and current_section:
                    parts = [folder, current_section, sanitize(title) + ".html"]
                    level = 2
                else:
                    parts = [folder, sanitize(title) + ".html"]
                    level = 1
                rel = "/".join(parts)
                rel = self._unique(rel, used_paths)
                if not topicref or not inf_name:
                    # navigation-only node (no content); still list it
                    self.tree_for_index.append((level, title, None))
                    continue
                subs = self.sm.page_subpages.get(topicref) or ["01"]
                page = PageNode(nodeid, title, rel, topicref, inf_name, subs)
                self.pages.append(page)
                self.page_by_node[nodeid] = page
                self.tree_for_index.append((level, title, rel))
                # register link targets
                self.target_for_ref[topicref] = rel
                for s in subs:
                    self.target_for_ref[topicref + s] = rel

    @staticmethod
    def _unique(rel: str, used: set) -> str:
        if rel.lower() not in used:
            used.add(rel.lower())
            return rel
        stem, ext = os.path.splitext(rel)
        i = 2
        while f"{stem}_{i}{ext}".lower() in used:
            i += 1
        rel = f"{stem}_{i}{ext}"
        used.add(rel.lower())
        return rel

    # -- pass 1b: standalone reference pages ---------------------------
    def plan_refs(self):
        """Materialise a page for every cross-reference target panel that is
        not already a navigation node, so ``Refer to ...`` links resolve.

        SITV emitted one HTML file per *panel*. Our navigation instead collapses
        each node's sub-panels (and auto-inlined children) into a single page, so
        a panel that is only ever reached as a link target — e.g. an air-bag
        disposal procedure referenced from a wiring page — would have no file to
        point at. We render those panels as standalone pages under ``_refs/``.

        Only targets reachable from content we actually render are emitted: we
        start from the navigation pages, follow their cross-links to find missing
        targets, then follow the new ref pages' cross-links too (a work queue to a
        fixpoint). This avoids generating files for the many panels that are only
        referenced by other non-navigated panels.
        """
        from collections import deque

        index = self._panel_index()
        used = {p.rel_path.lower() for p in self.pages}
        queue = deque(self.pages)
        while queue:
            page = queue.popleft()
            inf, topic_ids = self._rendered_topics(page)
            idx_to_name = self.inf.names(self.model, page.inf_name)
            for name in self._link_targets_in(inf, idx_to_name, topic_ids):
                if name in self.target_for_ref:
                    continue                   # already a nav page or earlier ref
                # A target may be a bare 12-char topicref or a full panel name
                # (topicref + 2-char sub); normalise to the topicref the index
                # is keyed on.
                topicref = name if name in index else name[:-2]
                if topicref in self.target_for_ref:
                    # The panel's topicref is already a page (usually a nav node);
                    # just alias this exact name form to it — no new file.
                    self.target_for_ref[name] = self.target_for_ref[topicref]
                    continue
                entry = index.get(topicref)
                if entry is None:
                    continue                   # target panel absent from this model
                inf_name, subs = entry
                rel = self._unique(f"_refs/{sanitize(topicref)}.html", used)
                page = PageNode(topicref, self._panel_title(inf_name, topicref, subs),
                                rel, topicref, inf_name, subs)
                self.pages.append(page)
                queue.append(page)             # follow its cross-links too
                self.target_for_ref[name] = rel
                self.target_for_ref[topicref] = rel
                for s in subs:
                    self.target_for_ref[topicref + s] = rel

    def _rendered_topics(self, page: PageNode):
        """The INF and the set of topic indices a page actually renders.

        That is the page's sub-panels plus the transitive closure of their
        *automatic* links (escape ``0x05`` with flag ``0x40``), which the renderer
        inlines into the same page.
        """
        inf = self.inf.get(self.model, page.inf_name)
        panels = self.inf.panels(self.model, page.inf_name)
        seen: set = set()
        stack = [panels[page.topicref + s] for s in page.subpages
                 if (page.topicref + s) in panels]
        while stack:
            idx = stack.pop()
            if idx in seen or not (0 <= idx < len(inf.topics)):
                continue
            seen.add(idx)
            stack.extend(self._auto_targets(inf, idx))
        return inf, seen

    @staticmethod
    def _auto_targets(inf: InfFile, idx: int) -> List[int]:
        """Topic indices inlined into topic ``idx`` via automatic links."""
        out: List[int] = []
        for slot in inf.get_slots(inf.topics[idx]):
            data = slot.text
            j, n = 0, len(data)
            while j < n:
                if data[j] == 0xFF and j + 2 < n:
                    elen = data[j + 1]
                    if data[j + 2] == 0x05 and elen >= 6 and (data[j + 5] & 0x40):
                        out.append(int.from_bytes(data[j + 3:j + 5], "little"))
                    j += 1 + elen
                else:
                    j += 1
        return out

    @staticmethod
    def _link_targets_in(inf: InfFile, idx_to_name: Dict[int, str],
                         topic_ids) -> set:
        """Panel names targeted by *external* cross-links from the given topics.

        External links (escape ``0x1f``) — the ``Refer to GROUP ...`` references —
        carry a panel name to a procedure in another section. Plain topic links
        (escape ``0x05``) instead point within the document, almost always to a
        panel that is already a navigation page, so they need no extra ref page.
        ``idx_to_name`` is unused here but kept for symmetry with the caller.
        """
        names: set = set()
        for idx in topic_ids:
            for slot in inf.get_slots(inf.topics[idx]):
                data = slot.text
                j, n = 0, len(data)
                while j < n:
                    if data[j] == 0xFF and j + 2 < n:
                        elen = data[j + 1]
                        if data[j + 2] == 0x1F:                 # external link
                            slen = data[j + 4]
                            names.add(data[j + 5:j + 5 + slen].decode("latin-1"))
                        j += 1 + elen
                    else:
                        j += 1
        return names

    def _panel_index(self) -> Dict[str, Tuple[str, List[str]]]:
        """Map every 12-char topicref to ``(inf_name, [sub-page suffixes])``.

        Panel names embedded in the INF are ``topicref(12) + sub(2)`` (e.g.
        ``M1310003025401`` = topicref ``M13100030254`` + sub ``01``); a single
        topicref can own several sub-panels. Built once over the INF files of
        every group in this config.
        """
        index: Dict[str, Tuple[str, List[str]]] = {}
        for inf_name in sorted(set(self.sm.inf_for_group.values())):
            names = self.inf.names(self.model, inf_name)   # topic idx -> panel name
            for panel in names.values():
                if len(panel) < 4:
                    continue
                ref, sub = panel[:-2], panel[-2:]
                slot = index.setdefault(ref, (inf_name, []))
                if sub not in slot[1]:
                    slot[1].append(sub)
        for ref, (_inf, subs) in index.items():
            subs.sort()
        return index

    def _panel_title(self, inf_name: str, topicref: str, subs: List[str]) -> str:
        """A human title for a ref page, from the panel's own topic heading."""
        inf = self.inf.get(self.model, inf_name)
        panels = self.inf.panels(self.model, inf_name)
        idx = panels.get(topicref + subs[0]) if subs else None
        if idx is not None and 0 <= idx < len(inf.topics):
            # Topic titles carry a leading "NN-" group prefix; drop it.
            return re.sub(r"^\d+-", "", inf.topics[idx].title).strip() or topicref
        return topicref

    # -- pass 2: render -------------------------------------------------
    def render_all(self):
        for page in self.pages:
            self._render_page(page)
        self._write_index()

    def _render_page(self, page: PageNode):
        inf = self.inf.get(self.model, page.inf_name)
        panels = self.inf.panels(self.model, page.inf_name)
        names = self.inf.names(self.model, page.inf_name)

        def link_for_topic(idx: int) -> Optional[str]:
            nm = names.get(idx)
            if nm and nm in self.target_for_ref:
                return _relpath(page.rel_path, self.target_for_ref[nm])
            if nm and nm[:12] in self.target_for_ref:
                return _relpath(page.rel_path, self.target_for_ref[nm[:12]])
            return None

        def link_for_external(name: str) -> Optional[str]:
            if name in self.target_for_ref:
                return _relpath(page.rel_path, self.target_for_ref[name])
            return None

        def image_name(offset: int) -> Optional[str]:
            key = (page.inf_name, offset)
            if key not in self.image_files:
                self.image_files[key] = self._extract_image(inf, page.inf_name, offset)
            rel = self.image_files[key]
            if rel == self._SPACER:
                return self._SPACER       # suppress (render emits nothing)
            return _relpath(page.rel_path, rel) if rel else None

        ctx = RenderContext(inf, link_for_topic, link_for_external, image_name)

        rendered: set = set()
        body: List[str] = []

        def render_with_auto(idx: int):
            if idx in rendered or not (0 <= idx < len(inf.topics)):
                return
            rendered.add(idx)
            ctx.auto_targets = []
            html = render_topic(ctx, inf.topics[idx])
            autos = list(ctx.auto_targets)
            body.append(html)
            for a in autos:
                render_with_auto(a)

        for s in page.subpages:
            idx = panels.get(page.topicref + s)
            if idx is not None:
                render_with_auto(idx)

        self._write_html(page.rel_path, page.title, "".join(body))

    # -- output ---------------------------------------------------------
    def _write_html(self, rel: str, title: str, body: str):
        path = os.path.join(self.out_root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        depth = rel.count("/")
        css = "../" * depth + "style.css"
        home = "../" * depth + "index.html"
        clean_title = escape(_strip_brackets(title))
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(
                f"<!DOCTYPE html>\n<html lang=\"en\"><head>\n"
                f"<meta charset=\"utf-8\"/>\n"
                f"<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"/>\n"
                f"<title>{clean_title}</title>\n"
                f"<link rel=\"stylesheet\" href=\"{escape(css, quote=True)}\"/>\n"
                f"</head>\n<body>\n"
                f"<nav class=\"top\"><a href=\"{escape(home, quote=True)}\">Contents</a></nav>\n"
                f"<article>\n<h1>{clean_title}</h1>\n{body}\n</article>\n"
                f"</body></html>\n"
            )

    def _write_index(self):
        lines = ['<!DOCTYPE html>', '<html lang="en"><head><meta charset="utf-8"/>',
                 '<meta name="viewport" content="width=device-width, initial-scale=1"/>',
                 f'<title>{escape(self.sm.config_id)} — Contents</title>',
                 '<link rel="stylesheet" href="style.css"/></head><body>',
                 f'<h1>Service Manual — {escape(self.sm.config_id)} '
                 f'({escape(self.sm.model)}/{escape(self.sm.year)})</h1>',
                 '<ul class="toc">']
        for level, title, rel in self.tree_for_index:
            t = escape(_strip_brackets(title))
            cls = f"lvl{level}"
            if rel:
                lines.append(f'<li class="{cls}"><a href="{escape(rel, quote=True)}">{t}</a></li>')
            else:
                lines.append(f'<li class="{cls} hdr">{t}</li>')
        lines.append("</ul></body></html>")
        with open(os.path.join(self.out_root, "index.html"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
        with open(os.path.join(self.out_root, "style.css"), "w", encoding="utf-8") as fh:
            fh.write(_CSS)

    _SPACER = SPACER_IMAGE

    # -- images ---------------------------------------------------------
    def _extract_image(self, inf: InfFile, inf_name: str, offset: int) -> Optional[str]:
        """Return the relative PNG path for the bitmap at ``offset``.

        ``None`` => undecodable, ``SPACER_IMAGE`` => structural spacer. Decoding
        and PNG-encoding are memoised across the whole build by ``InfCache``; here
        we only write the (already encoded) bytes into this config's tree.
        """
        if not self.with_images:
            return None
        status, png = self.inf.png(self.model, inf_name, offset)
        if status == "fail":
            self.img_fail += 1
            return None
        if status == "spacer":
            return self._SPACER
        rel = f"_images/{_doc_name(inf_name)}_{offset:08d}.png"
        out = os.path.join(self.out_root, rel)
        if not os.path.exists(out):
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, "wb") as fh:
                fh.write(png)
        self.img_ok += 1
        return rel


def _encode_png(inf: InfFile, offset: int) -> Tuple[str, Optional[bytes]]:
    """Decode one embedded bitmap and PNG-encode it once.

    Returns ``("fail", None)`` if it cannot be decoded, ``("spacer", None)`` for
    SITV's structural sub-pixel-tall layout bitmaps, or ``("ok", png_bytes)``.
    """
    import io
    from .bitmap import decode_bitmap
    try:
        img = decode_bitmap(inf.data, inf.h["imgstart"] + offset)
    except Exception:
        img = None
    if img is None:
        return ("fail", None)
    if img.width <= SPACER_MAX_DIM or img.height <= SPACER_MAX_DIM:
        return ("spacer", None)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return ("ok", buf.getvalue())


def _doc_name(inf_name: str) -> str:
    stem = os.path.splitext(inf_name)[0]
    return stem[:-2] + "AL" if len(stem) >= 2 else stem


def _strip_brackets(s: str) -> str:
    return s.strip()


_CSS = """\
body{font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:1.5;
  margin:0 auto;max-width:60rem;padding:1rem 1.2rem;color:#1a1a1a}
nav.top{font-size:13px;margin-bottom:1rem}
h1{font-size:1.5rem;border-bottom:2px solid #555;padding-bottom:.3rem}
article img{max-width:100%;height:auto;border:1px solid #ccc;margin:.5rem 0}
pre{font-family:"Courier New",monospace;white-space:pre;overflow-x:auto;
  background:#f6f6f6;padding:.5rem}
a{color:#0645ad}
table{border-collapse:collapse;width:100%;margin:.6rem 0}
table td,table th{border:1px solid #aaa;padding:.3rem .5rem;vertical-align:top;font-size:14px}
.fig-missing{display:inline-block;color:#999;font-size:12px;border:1px dashed #ccc;
  padding:0 .3rem;margin:.2rem 0}
ul.toc{list-style:none;padding-left:0}
ul.toc li{padding:1px 0}
ul.toc li.lvl0{font-weight:bold;margin-top:.6rem;font-size:1.05rem}
ul.toc li.lvl1{margin-left:1.2rem}
ul.toc li.lvl2{margin-left:2.4rem}
ul.toc li.hdr{color:#555;font-weight:bold}
"""


def build_all(input_dir: str, out_dir: str, configs=CONFIGS, with_images: bool = True):
    inf_cache = InfCache(os.path.join(input_dir, "INF"))
    os.makedirs(out_dir, exist_ok=True)
    summary = []
    root_links = []
    for model, year in configs:
        cfg_dir = os.path.join(input_dir, model, year)
        if not os.path.isdir(cfg_dir):
            continue
        sm = load_config(input_dir, model, year)
        out_root = os.path.join(out_dir, f"{model}-{year}")
        b = ConfigBuilder(sm, inf_cache, out_root)
        b.with_images = with_images
        b.plan()
        b.plan_refs()
        b.render_all()
        summary.append((model, year, len(b.pages), len(b.image_files), b.img_ok, b.img_fail))
        root_links.append(
            f'<li><a href="{model}-{year}/index.html">{escape(sm.config_id)} '
            f'({model}/{year}) — {len(b.pages)} pages</a></li>')
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as fh:
        fh.write("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                 "<title>SITV — Recreated Service Manual</title></head><body>"
                 "<h1>'94–'97 Eclipse / Eclipse Spyder — Service Manual</h1><ul>"
                 + "\n".join(root_links) + "</ul></body></html>")
    return summary
