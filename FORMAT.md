# SITV 1.08 — reverse-engineered data formats

SITV ("Service Information ... Viewer") was an OS/2 application that rendered a
Mitsubishi factory service manual (here: '94–'97 Eclipse / Eclipse Spyder,
Pub. MSCD-002B-97) from two kinds of source data into a static HTML web of
framesets. This project recreates that rendering from the original inputs.

## Inputs

```
input/
  SM00016F                 root manual title string
  205/ 220/                model codes (sales region)
    1994/ 1995/ 1996/ 1997/   model-year configurations, each holding SMxxxxxF
  INF/205/  INF/220/       the compiled content (OS/2 IPF) — identical 205==220
```

The **INF files** hold all the actual content. The **SM files** are the
navigation / table-of-contents metadata, and differ per model-year.

## INF — OS/2 IPF compiled help (`HSP` magic)

Standard OS/2 "Information Presentation Facility" INF format (same as NewView /
fpGUI docview reads). See `src/sitv/inf.py` for the exact 155-byte header.
Highlights:

* **Dictionary**: list of length-prefixed strings (the "words"). Topic text is a
  stream of byte indices into a per-slot *local* dictionary, which maps to the
  global dictionary.
* **Slots** (cells): each holds a `localdict` + a byte stream of word indices and
  escape codes (`0xFA`..`0xFF`). `0xFF` introduces a length-prefixed escape.
* **TOC entries** = "topics": variable-length records giving flags, nesting
  level, slot numbers and a plain-text title.
* **Bitmaps**: OS/2 BMP (`bM`), optionally LZW-compressed in blocks; at
  `imgstart + offset`.

### Escape codes used by the data (FF len code …)
Standard IPF codes (margins `02/11/12/1c`, highlight `04/0d`, link `05`,
footnote `07`, link-end `08`, char-graphics `0b/0c`, image `0e`, linked image
`0f`, colors `13/14`, font `19`, lines `1a/1b`, external link `1f`) plus:

* **`0x16` = panel name** (SITV/IPF hidden name): `FF len 16 ?? ?? ?? slen <name>`.
  This `M…` name is what SITV turns into the output filename. Topics that are
  auto-link children of another panel have no `0x16`.
* **`0x05` automatic link** (flag byte `& 0x40`): the target topic's content is
  *inlined* here rather than shown as a hyperlink (used to assemble a page from
  several cells).
* **`0x1f` external link**: file index + target panel name in another INF, e.g.
  `M42100280048` → `M42100280048.htm`.

### Tables
Specification/parts tables are encoded with a small private vocabulary:

* **`0x29` = table announce** — appears once near the top of a topic that
  contains a table; carries no geometry.
* **`0x2d` = table cell stream** — three sub-shapes, distinguished by length and
  the byte after the code:
  * `FF len 2d 01|08 ?? ncols [w0 w1 …]` (len ≥ 10) — **row definition**: a row
    of `ncols` cells whose `u16` pixel widths set the HTML `colspan`s (each
    width is rounded to a share of 80 total columns).
  * `FF 04 2d 02` (and the bare byte **`0xF6`** between words) — **next-cell**
    separator within the current row.
  * `FF 05 2d 01 47 02` — **table end**.
  * sub-shapes beginning `03`/`04` are per-cell attributes and are ignored.

### Bitmaps
* **G4 figures** (`usType 0x3459`): CCITT Group 4, wrapped in a TIFF.
* **`bM` (`0x4d62`) bitmaps**: OS/2 1.x core header + IPF block stream
  (`total u32, ublk u16, [size u16, ctype u8, data]*`). Per-block `ctype`:
  `0` raw, `1` byte-RLE, `2` LZW. Each block decodes to `ublk` bytes; a short
  block is padded with its last byte. **ctype 1 RLE**: control byte `c` — if
  `c < 0x80` repeat the next byte `c+1` times, else copy the next
  `(c & 0x7f) + 1` bytes literally. 1-bpp `bM` line art stores its palette with
  reversed polarity (index 0 = black = paper) and is rendered inverted to match
  the originals' black-on-white diagrams.
* **Page-header spacer**: every topic opens with a 2×2 white `bM` bitmap
  (offset `18144`) used as a scroll anchor; it is not a figure and is dropped.

## SM index files (per model-year)  — see `src/sitv/smindex.py`

Records separated by `0x01`, sub-fields by `0x02`/`0x03`.

| file | meaning |
|------|---------|
| SM00001F | ordered **group** list = folders: `groupkey(4)+subcode(4)+title [\x02 eng]*` |
| SM00004F | **group → node tree**: `groupkey {\x02 nodeid(8)+kind(2)}*` |
| SM00005F | category code(4) → name |
| SM00006F | category → nodes |
| SM00007F | keyword dictionary |
| SM00008F | keyword → nodes |
| SM00009F | groupkey → INF filename |
| SM00010F | nodeid(8) → topicref(12) + category(6) |
| SM00011F | topicref → nodeid (reverse) |
| SM00012F | nodeid → engine codes |
| SM00013F | topicref → sub-page list (`01`,`02`,…) |
| SM00014F | component dictionary (electrical) |
| SM00015F | component → nodes |
| SM00017F | nodeid(8) → human title (the page/file name) |
| SM00019F | config id, e.g. `205205` |

### How it fits together
* A **folder** = SM00001F group; its name = `subcode[2:4] + title` (with
  `<>(){}/` stripped), e.g. subcode `0111`, title `A-ENGINE MECHANICAL <4G63>`
  → `11A-ENGINE MECHANICAL 4G63`.
* Within a folder, SM00004F lists nodes with a **kind**: `00`=standalone doc
  (file directly in folder), `10`=section header (sub-folder), `01`=procedure
  (file inside the preceding section's sub-folder).
* Each node → title (SM00017F) and → page topicref (SM00010F). The topicref's
  sub-pages (SM00013F) concatenate with the topicref to give INF **panel names**
  (`topicref + sub`), which resolve to INF topics via the `0x16` name.
* Group keys in SM00001F and SM00004F are aligned **within a single config**
  (earlier confusion came from comparing different model-years).

## Output (reference, partial)
`output/<FOLDER>/[<SECTION>/]<TITLE>.htm` + `M<doc>AL_NNNNN.png` images, where
`<doc>` is the INF filename with the trailing two digits replaced by `AL`
(`M2051100` → `M20511AL`). SITV split each page into a 75%/25% frameset; the
recreation collapses that into a single clean HTML page per node.
