"""Decode INF topic cell-streams into clean HTML fragments.

Mirrors the IPF interpretation done by NewView/docview's TTopic.GetText, but
emits HTML instead of RichTextView markup. The aim is faithful text + images +
working cross links, not byte-identical reproduction of SITV's framesets.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from html import escape
from typing import Callable, Dict, List, Optional, Tuple

from .inf import (InfFile, Topic, IPF_END_PARA, IPF_CENTER, IPF_INVERT_SPACING,
                  IPF_LINEBREAK, IPF_SPACE, IPF_ESC)

# escape codes (subset we act on)
EC_SET_LEFT_MARGIN = 0x02
EC_HIGHLIGHT1 = 0x04
EC_LINK_START = 0x05
EC_FOOTNOTE_LINK = 0x07
EC_LINK_END = 0x08
EC_START_CHARGRAPHICS = 0x0B
EC_END_CHARGRAPHICS = 0x0C
EC_HIGHLIGHT2 = 0x0D
EC_IMAGE = 0x0E
EC_LINKED_IMAGE = 0x0F
EC_PROGRAM_LINK = 0x10
EC_SET_LEFT_MARGIN_NL = 0x11
EC_SET_LEFT_MARGIN_FIT = 0x12
EC_FG_COLOR = 0x13
EC_BG_COLOR = 0x14
EC_PANEL_NAME = 0x16
EC_FONT_CHANGE = 0x19
EC_START_LINES = 0x1A
EC_END_LINES = 0x1B
EC_SET_LEFT_MARGIN_HERE = 0x1C
EC_LINK_BY_RESID = 0x1D
EC_EXTERNAL_LINK = 0x1F

HPART_DEFINE = 0
HPART_HDREF = 4

# highlight1 style -> (open, close) html
H1 = {
    1: ("<i>", "</i>"),
    2: ("<b>", "</b>"),
    3: ("<b><i>", "</i></b>"),
    4: ("<u>", "</u>"),
    5: ("<u><i>", "</i></u>"),
    6: ("<u><b>", "</b></u>"),
}


def get_panel_name(inf: InfFile, topic: Topic) -> Optional[str]:
    """Return the M-panel name embedded as escape 0x16, if any."""
    for slot in inf.get_slots(topic):
        data = slot.text
        i = 0
        n = len(data)
        lds = len(slot.localdict)
        while i < n:
            c = data[i]
            i += 1
            if c < lds:
                continue
            if c == IPF_ESC:
                elen = data[i]
                code = data[i + 1]
                if code == EC_PANEL_NAME:
                    slen = data[i + 6]
                    return data[i + 7:i + 7 + slen].decode("latin-1")
                i += elen
        # only look in the first slot's leading bytes; names always lead a topic
    return None


@dataclass
class RenderContext:
    inf: InfFile
    # topic index -> href (".htm" target) ; None => not linkable
    link_for_topic: Callable[[int], Optional[str]]
    # external panel name -> href
    link_for_external: Callable[[str], Optional[str]]
    # image byte offset -> emitted filename (also records that it is used)
    image_name: Callable[[int], str]
    auto_targets: List[int] = field(default_factory=list)


class _TopicRenderer:
    def __init__(self, ctx: RenderContext, topic: Topic):
        self.ctx = ctx
        self.inf = ctx.inf
        self.topic = topic
        self.out: List[str] = []
        self.open_tags: List[str] = []      # currently open highlight tags
        self.spacing = True
        self.in_charg = False
        self.link_open = False
        self.words_on_line = 0

    # -- helpers --------------------------------------------------------
    def _close_highlights(self):
        while self.open_tags:
            self.out.append(self.open_tags.pop())

    def _newline(self):
        if self.in_charg:
            self.out.append("\n")
        else:
            self.out.append("<br/>\n")

    def _para(self):
        self._close_highlights()
        self.out.append("\n</p>\n<p>\n")

    # -- main -----------------------------------------------------------
    def run(self) -> str:
        self.out.append("<p>\n")
        for slot in self.inf.get_slots(self.topic):
            self.spacing = True if not self.in_charg else self.spacing
            self._run_slot(slot)
        self._close_highlights()
        self.out.append("\n</p>\n")
        return "".join(self.out)

    def _run_slot(self, slot):
        data = slot.text
        i = 0
        n = len(data)
        ld = slot.localdict
        lds = len(ld)
        while i < n:
            c = data[i]
            i += 1
            if c < lds:
                w = self.inf.dictionary[ld[c]]
                if w.strip() == "":
                    if self.spacing:
                        self.out.append(" ")
                else:
                    self.out.append(escape(w))
                    if self.spacing:
                        self.out.append(" ")
                    self.words_on_line += 1
            elif c == IPF_END_PARA:
                self._para()
                if not self.in_charg:
                    self.spacing = True
                self.words_on_line = 0
            elif c == IPF_CENTER:
                self._newline()
            elif c == IPF_INVERT_SPACING:
                if not self.in_charg:
                    self.spacing = not self.spacing
            elif c == IPF_LINEBREAK:
                self._newline()
                if not self.in_charg:
                    self.spacing = True
                self.words_on_line = 0
            elif c == IPF_SPACE:
                self.out.append(" ")
            elif c == IPF_ESC:
                elen = data[i]
                i = self._escape(data, i, elen)
        # end slot

    def _escape(self, data: bytes, i: int, elen: int) -> int:
        """i points at the length byte; return new index after the escape."""
        code = data[i + 1]
        nxt = i + elen
        if code == EC_HIGHLIGHT1:
            style = data[i + 2]
            if style == 0:
                self._close_highlights()
            elif style in H1:
                o, c = H1[style]
                self.out.append(o)
                self.open_tags.append(c)
        elif code == EC_HIGHLIGHT2:
            style = data[i + 2]
            if style == 0:
                self._close_highlights()
            else:
                colors = {1: "blue", 2: "red", 3: "purple"}
                col = colors.get(style)
                if col:
                    self.out.append(f'<span style="color:{col}">')
                    self.open_tags.append("</span>")
        elif code == EC_FG_COLOR:
            pass  # ignore precise colours for cleanliness
        elif code == EC_BG_COLOR:
            pass
        elif code in (EC_SET_LEFT_MARGIN, EC_SET_LEFT_MARGIN_FIT, EC_SET_LEFT_MARGIN_HERE):
            pass
        elif code == EC_SET_LEFT_MARGIN_NL:
            self._newline()
        elif code == EC_START_CHARGRAPHICS:
            self._close_highlights()
            self.out.append("<pre>")
            self.in_charg = True
            self.spacing = False
        elif code == EC_END_CHARGRAPHICS:
            self.out.append("</pre>")
            self.in_charg = False
            self.spacing = True
        elif code == EC_START_LINES:
            pass
        elif code == EC_END_LINES:
            pass
        elif code == EC_FONT_CHANGE:
            pass
        elif code == EC_LINK_START:
            topic_index = int.from_bytes(data[i + 2:i + 4], "little")
            automatic = False
            if elen >= 6:
                flags1 = data[i + 4]
                automatic = bool(flags1 & 0x40)
            if automatic:
                self.ctx.auto_targets.append(topic_index)
            else:
                href = self.ctx.link_for_topic(topic_index)
                if href:
                    self.out.append(f'<a href="{escape(href, quote=True)}">')
                    self.link_open = True
                else:
                    self.out.append("<u>")
                    self.link_open = "u"
        elif code == EC_FOOTNOTE_LINK:
            topic_index = int.from_bytes(data[i + 2:i + 4], "little")
            href = self.ctx.link_for_topic(topic_index)
            if href:
                self.out.append(f'<a href="{escape(href, quote=True)}">')
                self.link_open = True
            else:
                self.out.append("<u>")
                self.link_open = "u"
        elif code == EC_LINK_BY_RESID:
            self.out.append("<u>")
            self.link_open = "u"
        elif code == EC_EXTERNAL_LINK:
            slen = data[i + 3]
            name = data[i + 4:i + 4 + slen].decode("latin-1")
            href = self.ctx.link_for_external(name)
            if href:
                self.out.append(f'<a href="{escape(href, quote=True)}">')
                self.link_open = True
            else:
                self.out.append("<u>")
                self.link_open = "u"
        elif code == EC_PROGRAM_LINK:
            self.out.append("<u>")
            self.link_open = "u"
        elif code == EC_LINK_END:
            if self.link_open == "u":
                self.out.append("</u>")
            elif self.link_open:
                self.out.append("</a>")
            self.link_open = False
        elif code == EC_IMAGE:
            offset = int.from_bytes(data[i + 3:i + 7], "little")
            self._image(offset)
        elif code == EC_LINKED_IMAGE:
            nxt = self._linked_image(data, i)
        elif code == EC_PANEL_NAME:
            pass  # used for naming, not content
        return nxt

    def _image(self, offset: int):
        name = self.ctx.image_name(offset)
        self.out.append(f'<br/><img src="{escape(name, quote=True)}"/><br/>\n')

    def _linked_image(self, data: bytes, i: int) -> int:
        """Handle a chain of 0x0f linked-image escapes; return next index."""
        offset = None
        link_topic = -1
        while True:
            elen = data[i]
            sub = data[i + 2]
            if sub == HPART_DEFINE:
                offset = int.from_bytes(data[i + 4:i + 8], "little")
            elif sub == HPART_HDREF:
                link_topic = int.from_bytes(data[i + 3:i + 5], "little")
            i += elen
            if i >= len(data) or data[i] != IPF_ESC:
                break
            if data[i + 2] != EC_LINKED_IMAGE:
                break
            if data[i + 3] == HPART_DEFINE:
                break
            i += 1  # move to length byte of the next linked-image escape
        if offset is not None:
            name = self.ctx.image_name(offset)
            href = self.ctx.link_for_topic(link_topic) if link_topic > -1 else None
            img = f'<img src="{escape(name, quote=True)}"/>'
            if href:
                img = f'<a href="{escape(href, quote=True)}">{img}</a>'
            self.out.append(f'<br/>{img}<br/>\n')
        return i


def render_topic(ctx: RenderContext, topic: Topic) -> str:
    return _TopicRenderer(ctx, topic).run()
