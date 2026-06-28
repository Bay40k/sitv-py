"""Parser for OS/2 IPF compiled help files (INF/HLP, magic 'HSP').

Format reverse-engineered from the fpGUI 'docview' source (an open-source
descendant of NewView), cross-checked byte-for-byte against the SITV input.

Only the parts needed to recover topic text, titles, links and embedded
bitmaps are implemented.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import List, Optional

HEADER_FMT = "<3sBHBBHIIIHIHIHIIHIIIIHIIHIIBIII12s48s"
HEADER_SIZE = struct.calcsize(HEADER_FMT)
assert HEADER_SIZE == 155

HEADER_FIELDS = [
    "ID", "flags", "hdrsize", "version_hi", "version_lo", "ntoc", "tocstart",
    "toclen", "tocoffsetsstart", "nres", "resstart", "nname", "namestart",
    "nindex", "indexstart", "indexlen", "icmdCount", "icmdOffset", "icmdSize",
    "searchstart", "searchlen", "nslots", "slotsstart", "dictlen", "ndict",
    "dictstart", "imgstart", "maxCVTIndex", "nlsstart", "nlslen", "extstart",
    "reserved", "title",
]

EXT_FMT = "<HIHIIHIIHIIIII16s"
EXT_SIZE = struct.calcsize(EXT_FMT)
EXT_FIELDS = [
    "NumFontEntry", "FontTableOffset", "NumDataBase", "DataBaseOffset",
    "DataBaseSize", "EntryInGNameTable", "HelpPanelGNameTblOffset",
    "StringsOffset", "StringsSize", "ChildPagesOffset", "ChildPagesSize",
    "NumGIndexEntry", "CtrlOffset", "CtrlSize", "reserved",
]

# TOC entry flags
TOC_EXTENDED = 0x20
TOC_HIDDEN = 0x40
TOC_HASCHILDREN = 0x80
TOC_LEVELMASK = 0x0F

# Basic byte codes
IPF_END_PARA = 0xFA
IPF_CENTER = 0xFB
IPF_INVERT_SPACING = 0xFC
IPF_LINEBREAK = 0xFD
IPF_SPACE = 0xFE
IPF_ESC = 0xFF

# Font face name length inside THelpFontSpec
FONTSPEC_FMT = "<33sHHH"
FONTSPEC_SIZE = struct.calcsize(FONTSPEC_FMT)


@dataclass
class FontSpec:
    facename: str
    height: int
    width: int
    codepage: int


@dataclass
class Slot:
    localdict: List[int]       # local index -> global dict index
    text: bytes                # raw cell bytes


@dataclass
class Topic:
    index: int
    flags: int
    level: int
    show_in_contents: bool
    title: str
    slot_numbers: List[int]
    group_index: int = 0
    # filled lazily / by reader
    _slots: Optional[List[Slot]] = None


class InfFile:
    def __init__(self, path: str):
        self.path = path
        with open(path, "rb") as fh:
            self.data = fh.read()
        self._parse_header()
        self._read_dictionary()
        self._read_slot_offsets()
        self._read_ext_header()
        self._read_fonts()
        self._read_topics()

    # ---- header -------------------------------------------------------
    def _parse_header(self):
        vals = struct.unpack(HEADER_FMT, self.data[:HEADER_SIZE])
        self.h = dict(zip(HEADER_FIELDS, vals))
        if self.h["ID"] != b"HSP":
            raise ValueError(f"{self.path}: not an OS/2 INF file (magic {self.h['ID']!r})")
        self.title = self.h["title"].split(b"\0")[0].decode("latin-1")

    def _read_dictionary(self):
        start = self.h["dictstart"]
        end = start + self.h["dictlen"]
        p = start
        words: List[str] = []
        data = self.data
        for _ in range(self.h["ndict"]):
            ln = data[p] - 1
            p += 1
            words.append(data[p:p + ln].decode("cp850", "replace"))
            p += ln
        self.dictionary = words

    def _read_slot_offsets(self):
        n = self.h["nslots"]
        start = self.h["slotsstart"]
        self.slot_offsets = list(struct.unpack_from(f"<{n}I", self.data, start))

    def _read_ext_header(self):
        self.ext = None
        if self.h["extstart"] > 0:
            vals = struct.unpack_from(EXT_FMT, self.data, self.h["extstart"])
            self.ext = dict(zip(EXT_FIELDS, vals))

    def _read_fonts(self):
        self.fonts: List[FontSpec] = []
        if not self.ext or self.ext["NumFontEntry"] == 0:
            return
        off = self.ext["FontTableOffset"]
        for i in range(self.ext["NumFontEntry"]):
            face, h, w, cp = struct.unpack_from(FONTSPEC_FMT, self.data, off + i * FONTSPEC_SIZE)
            self.fonts.append(FontSpec(face.split(b"\0")[0].decode("latin-1"), h, w, cp))

    # ---- table of contents (topics) ----------------------------------
    def _read_topics(self):
        data = self.data
        p = self.h["tocstart"]
        end = p + self.h["toclen"]
        self.topics: List[Topic] = []
        for idx in range(self.h["ntoc"]):
            if p >= end:
                break
            start = p
            length = data[p]
            flags = data[p + 1]
            numslots = data[p + 2]
            q = p + 3
            group_index = 0
            if flags & TOC_EXTENDED:
                w1 = data[q]
                w2 = data[q + 1]
                q += 2
                if w1 & 1:
                    q += 5      # position (THelpXYPair = flags u8 + x u16 + y u16)
                if w1 & 2:
                    q += 5      # size
                if w1 & 8:
                    q += 2      # window controls
                if w1 & 0x40:
                    q += 2      # unknown / style
                if w2 & 4:
                    group_index = struct.unpack_from("<H", data, q)[0]
                    q += 2
            slot_numbers = list(struct.unpack_from(f"<{numslots}H", data, q))
            q += numslots * 2
            title_len = length - (q - start)
            title = data[q:q + title_len].decode("cp850", "replace") if title_len > 0 else ""
            level = flags & TOC_LEVELMASK
            show = (flags & TOC_HIDDEN) == 0 and level != 0
            self.topics.append(Topic(idx, flags, level, show, title, slot_numbers, group_index))
            p = start + length

    # ---- slot text ----------------------------------------------------
    def get_slots(self, topic: Topic) -> List[Slot]:
        if topic._slots is not None:
            return topic._slots
        slots: List[Slot] = []
        data = self.data
        for sn in topic.slot_numbers:
            off = self.slot_offsets[sn]
            _stuff, localdictpos, nlocaldict, ntext = struct.unpack_from("<BIBH", data, off)
            localdict = list(struct.unpack_from(f"<{nlocaldict}H", data, localdictpos))
            text = data[off + 8: off + 8 + ntext]
            slots.append(Slot(localdict, text))
        topic._slots = slots
        return slots
