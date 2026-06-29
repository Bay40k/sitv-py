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

import struct

# escape codes (subset we act on)
EC_SET_LEFT_MARGIN = 0x02
EC_HIGHLIGHT1 = 0x04
EC_LINK_START = 0x05
EC_LINK_END = 0x08
EC_IMAGE = 0x0E
EC_LINKED_IMAGE = 0x0F
EC_SET_LEFT_MARGIN_NL = 0x11
EC_PANEL_NAME = 0x16
EC_FONT_CHANGE = 0x19
EC_EXTERNAL_LINK = 0x1F
EC_TABLE_DEF = 0x29   # table start (announces table in this topic)
EC_TABLE_CELL = 0x2D  # table row/cell structure

# byte 0xF6 between dict words = column separator within a table row
_EC_COL_SEP = 0xF6

# image_name() returns this sentinel for SITV's structural page-header spacer
# (a 2x2 white bitmap); the renderer emits nothing for it.
SPACER_IMAGE = "<spacer>"

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
# Map closing tags back to openers (for re-emitting inside new <td>)
_REOPEN = {"</i>": "<i>", "</b>": "<b>", "</u>": "<u>",
           "</i></b>": "<b><i>", "</b></u>": "<u><b>",
           "</i></u>": "<u><i>"}


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
        # table state
        self.in_table = False       # inside a <table> element
        self.table_colspans: List[int] = []
        self.table_cell_idx = -1    # index of currently open <td> (-1 = none)

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
        if self.in_table:
            return  # paragraph breaks are ignored inside table cells
        self.out.append("\n</p>\n<p>\n")

    # -- main -----------------------------------------------------------
    def run(self) -> str:
        self.out.append("<p>\n")
        for slot in self.inf.get_slots(self.topic):
            self.spacing = True if not self.in_charg else self.spacing
            self._run_slot(slot)
        self._close_highlights()
        if self.in_table:
            self._table_end()
        self.out.append("\n</p>\n")
        return "".join(self.out)

    # -- table helpers --------------------------------------------------
    @staticmethod
    def _colspans_from_widths(widths: List[int]) -> List[int]:
        """Convert raw pixel widths to colspan values summing to 80."""
        total = sum(widths)
        if total == 0:
            n = len(widths)
            return [80 // n] * n
        spans: List[int] = []
        cumulative = 0
        prev_rounded = 0
        for w in widths:
            cumulative += w
            rounded = round(cumulative / total * 80)
            spans.append(max(1, rounded - prev_rounded))
            prev_rounded = rounded
        return spans

    def _table_row_def(self, payload: bytes):
        """Handle the first 0x2d of a table triplet (row definition)."""
        if len(payload) < 4:
            return
        ncols = payload[3]
        if ncols == 0 or len(payload) < 4 + ncols * 2:
            return
        widths = [struct.unpack_from("<H", payload, 4 + k * 2)[0] for k in range(ncols)]
        self.table_colspans = self._colspans_from_widths(widths)

        # Close any open cell/row from previous row
        if self.in_table and self.table_cell_idx >= 0:
            self._close_highlights()
            self.out.append("</td></tr>\n")
            self.table_cell_idx = -1

        # Open <table> on first row
        if not self.in_table:
            self._close_highlights()
            self.out.append("</p>\n<table border=\"1\" width=\"100%\"><tbody>\n")
            self.in_table = True

        self.out.append("<tr>\n")
        # First cell will be opened by the first separator

    def _table_cell_sep(self):
        """Advance to the next table column (from 0xf6 or short 0x2d col-sep)."""
        if not self.in_table:
            return

        # Remember which formatting was open so we can re-emit it inside the new cell
        pending = list(self.open_tags)

        self._close_highlights()
        if self.table_cell_idx >= 0:
            self.out.append("</td>\n")

        next_idx = self.table_cell_idx + 1
        ncols = len(self.table_colspans)

        if next_idx >= ncols:
            # Implicit row wrap (sub-rows from rowspan content)
            self.out.append("</tr>\n<tr>\n")
            next_idx = 0

        colspan = self.table_colspans[next_idx]
        self.out.append(f'<td colspan="{colspan}" align="left" valign="top">')
        self.table_cell_idx = next_idx

        # Re-emit any inline formatting that was open before this cell boundary
        for close_tag in reversed(pending):
            open_tag = _REOPEN.get(close_tag)
            if open_tag:
                self.out.append(open_tag)
                self.open_tags.append(close_tag)

    def _table_end(self):
        """Close the current table."""
        if not self.in_table:
            return
        if self.table_cell_idx >= 0:
            self._close_highlights()
            self.out.append("</td>\n")
        self.out.append("</tr>\n</tbody></table>\n<p>\n")
        self.in_table = False
        self.table_colspans = []
        self.table_cell_idx = -1

    def _run_slot(self, slot):
        """Interpret one cell's byte stream.

        Every byte is one of three things, decided purely by its value:

        * ``c < len(localdict)`` — a *word*: ``localdict[c]`` is a global
          dictionary index whose string we emit. A blank dictionary word is the
          inter-word space, hence the ``spacing`` dance below (some runs, e.g.
          inside ``< >`` model tags, toggle spacing off so tokens butt together).
        * ``0xFA..0xFE`` — a one-byte layout control (paragraph, newline, space,
          spacing toggle); ``0xF6`` is our private table column separator.
        * ``0xFF`` — introduces a length-prefixed escape (see ``_escape``).

        Because dictionary indices are always ``< len(localdict) <= ~250`` and the
        controls are ``>= 0xFA``, the two ranges never collide.
        """
        data = slot.text
        i = 0
        n = len(data)
        ld = slot.localdict
        lds = len(ld)
        while i < n:
            c = data[i]
            i += 1
            if c < lds:                         # a dictionary word
                w = self.inf.dictionary[ld[c]]
                if w.strip() == "":             # blank word == a space token
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
            elif c == _EC_COL_SEP:
                self._table_cell_sep()
            elif c == IPF_ESC:
                elen = data[i]
                i = self._escape(data, i, elen)
        # end slot

    def _escape(self, data: bytes, i: int, elen: int) -> int:
        """Handle one ``0xFF`` escape and return the index just past it.

        Layout: ``data[i]`` is ``elen`` (the escape's total length, *including*
        the 0xFF and the length byte itself), ``data[i+1]`` is the ``EC_*`` code,
        and ``data[i+2:]`` are code-specific operands. The default ``nxt`` skips
        the whole escape; the linked-image handler is the only one that consumes
        a variable run and overrides it.
        """
        code = data[i + 1]
        nxt = i + elen
        if code == EC_HIGHLIGHT1:
            # data[i+2] = style: 0 clears all, 1..6 push an italic/bold/underline
            # combination (see H1). We keep a stack so it nests and so a table
            # cell boundary can re-open whatever was active.
            style = data[i + 2]
            if style == 0:
                self._close_highlights()
            elif style in H1:
                o, c = H1[style]
                self.out.append(o)
                self.open_tags.append(c)
        elif code == EC_SET_LEFT_MARGIN_NL:
            self._newline()
        elif code == EC_LINK_START:
            # data[i+2:i+4] = target topic index (u16, little-endian).
            # data[i+4] bit 0x40 marks an *automatic* link: instead of a
            # hyperlink, the target topic's content is inlined into this page
            # (that is how a node assembles itself from several cells).
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
                    self.out.append("<u>")          # known link, unknown target
                    self.link_open = "u"
        elif code == EC_LINK_END:
            if self.link_open == "u":
                self.out.append("</u>")
            elif self.link_open:
                self.out.append("</a>")
            self.link_open = False
        elif code == EC_IMAGE:
            # data[i+3:i+7] = u32 byte offset of the bitmap from imgstart.
            offset = int.from_bytes(data[i + 3:i + 7], "little")
            self._image(offset)
        elif code == EC_LINKED_IMAGE:
            nxt = self._linked_image(data, i)       # consumes a 0x0f chain
        elif code == EC_EXTERNAL_LINK:
            # data[i+3] = name length, then that many latin-1 bytes naming a
            # panel ("Refer to GROUP ...") possibly in another INF.
            slen = data[i + 3]
            name = data[i + 4:i + 4 + slen].decode("latin-1")
            href = self.ctx.link_for_external(name)
            if href:
                self.out.append(f'<a href="{escape(href, quote=True)}">')
                self.link_open = True
            else:
                self.out.append("<u>")
                self.link_open = "u"
        elif code == EC_TABLE_DEF:
            pass  # announces a table ahead; the table opens at the first row def
        elif code == EC_TABLE_CELL:
            # The 0x2d vocabulary, distinguished by length and first operand byte:
            #   05 .. 01 47 02         -> end of table
            #   >=10 .. (01|08) ncols [w0 w1 ...]  -> new row (u16 pixel widths)
            #   <=04 .. 02             -> advance to next cell (like the 0xf6 byte)
            #   .. 03 / .. 04          -> per-cell attributes we don't need
            payload = data[i + 2:i + elen]
            first = payload[0] if payload else -1
            if elen == 5 and first == 0x01:
                self._table_end()
            elif elen >= 10 and first in (0x01, 0x08):
                self._table_row_def(payload)
            elif elen <= 4 and first == 0x02:
                self._table_cell_sep()
        # all other codes (font, margin, color, char-graphics, etc.) -> ignore
        return nxt

    def _image(self, offset: int):
        name = self.ctx.image_name(offset)
        if name == SPACER_IMAGE:
            return  # structural page-header spacer, not a figure
        if name:
            self.out.append(f'<br/><img src="{escape(name, quote=True)}"/><br/>\n')
        else:
            self.out.append('<br/><span class="fig-missing">[figure]</span><br/>\n')

    def _linked_image(self, data: bytes, i: int) -> int:
        """Decode a run of ``0x0f`` linked-image escapes; return the next index.

        A figure that is also a hyperlink is described by *several* consecutive
        ``0x0f`` escapes, one per "part". Each part is ``FF elen 0f <sub> ...``:

        * ``sub == HPART_DEFINE (0)``  -> ``data[i+4:i+8]`` is the bitmap's u32
          offset (the actual picture).
        * ``sub == HPART_HDREF  (4)``  -> ``data[i+3:i+5]`` is the u16 topic the
          picture links to.

        We walk the chain, gathering the offset and (optional) link target, and
        stop when the next byte is not another ``0x0f`` part — or when we hit a
        fresh ``HPART_DEFINE``, which begins the *next* image rather than another
        part of this one. ``i`` here points at each part's length byte.
        """
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
                break                       # no more escapes
            if data[i + 2] != EC_LINKED_IMAGE:
                break                       # next escape is something else
            if data[i + 3] == HPART_DEFINE:
                break                       # next escape starts a new image
            i += 1  # advance from the 0xFF to the length byte of the next part
        if offset is not None:
            name = self.ctx.image_name(offset)
            if name == SPACER_IMAGE:
                return i  # structural page-header spacer, not a figure
            if name:
                href = self.ctx.link_for_topic(link_topic) if link_topic > -1 else None
                img = f'<img src="{escape(name, quote=True)}"/>'
                if href:
                    img = f'<a href="{escape(href, quote=True)}">{img}</a>'
                self.out.append(f'<br/>{img}<br/>\n')
            else:
                self.out.append('<br/><span class="fig-missing">[figure]</span><br/>\n')
        return i


def render_topic(ctx: RenderContext, topic: Topic) -> str:
    return _TopicRenderer(ctx, topic).run()
