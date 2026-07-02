# sitv-py

**For anyone with a late-1990s factory service manual CD/DVD that shipped with a
proprietary OS/2 viewer you can no longer run.**

`sitv-py` decodes the undocumented binary formats on the disc and rebuilds the
manual as a plain static website you can open in any browser. Each topic becomes
an HTML page, with its diagrams recovered as PNGs and its cross-references turned
into working links.

It reproduces what **SITV 1.08** used to do. SITV was an OS/2 program that
rendered these manuals into HTML, and it's no longer obtainable, so `sitv-py` was
written from the raw disc data plus a small sample of the pages SITV produced.

It has been tested on **MSCD-002B-97**, the 1994–1997 Mitsubishi Eclipse /
Eclipse Spyder factory service manual. The disc uses the generic OS/2 IPF
(Information Presentation Facility) help format, so `sitv-py` should also handle
other service-manual CD/DVDs from the same era that use that format, though the
Eclipse disc is the only one tested.

## Directory layout

The project uses three top-level data directories, all excluded from git:

```
input/       raw disc contents — the only thing needed to convert
output/      a partial HTML sample from the original SITV viewer, kept only
             for verification
recreated/   everything sitv-py generates — the finished, browsable site
```

**`input/`** is a verbatim copy of the CD. Mount or rip the disc and copy it in
as-is; this is the only directory a conversion actually needs:

```
input/
  SM00016F              top-level SM index (root of the table-of-contents tree)
  205/  220/            model codes (sales regions)
    1994/ … 1997/       per-year SM****F index files (the navigation / TOC)
  INF/
    205/  220/          per-model content — OS/2 IPF "INF" compiled help files
```

**`output/`** is a reference sample of pages the original SITV software once
produced. It is used **only to check coverage** — `verify.py` compares the
recreated text against it — and is **not required to run a conversion**. When
present it is a flat set of section folders holding the original `.htm` pages and
their figures:

```
output/
  00 -GENERAL/                            one folder per manual section
    GENERAL DATA AND SPECIFICATIONS.htm   original .htm pages
    M20500AL_00031.png                    original figures
    …
  00E-GENERAL ELECTRICAL/
  11A-ENGINE MECHANICAL 4G63/
  …
```

**`recreated/`** is where everything cleanly extracted from `input/` lands. Each
configuration (model + year) gets its own subtree mirroring the manual's
section / subsection hierarchy, plus two shared folders per config — `_images/`
for de-duplicated figures and `_refs/` for the standalone pages that
cross-references point at:

```
recreated/
  index.html                          top-level picker across all configs
  205-1994/                           one subtree per model-year configuration
    index.html                        this config's table of contents
    00 -GENERAL/                      section folder
      HOW TO USE THIS MANUAL.html     topic pages
      MAINTENANCE SERVICE/            subsections nest as sub-folders
        …
    11A-ENGINE MECHANICAL 4G63/
    _images/                          de-duplicated figures (PNG)
    _refs/                            standalone cross-reference target pages
  205-1995/  205-1996/  205-1997/     further configurations
  …
```

Open `recreated/index.html` in any browser to read the finished manual.

## Status

The following figures are all measured on the one disc tested so far,
**MSCD-002B-97** (the 1994–1997 Mitsubishi Eclipse):

- **1,428 pages per model-year** across 6 configurations (205 & 220, 1994–1997).
- **~5,650 figures per config decoded, 0 failures.**
- **92.5 % word-5-gram coverage** of the reference `output/` sample (the missing
  few percent is mostly table whitespace and a handful of dangling source links).
- Full rebuild in ~3 minutes.

39 unit tests cover the format parsers.

## How it works

The input is two kinds of file, both undocumented (see the `input/` layout
above): the `SM****F` navigation indexes and the OS/2 IPF `INF` content files.
The build is a pipeline; each stage has its own module under `src/sitv/`:

1. **`smindex.py` — navigation.** The `SM*****F` files are control-character
   delimited tables: the group/folder list, the tree of nodes under each group,
   node→title, node→topic-reference, topic→sub-pages, and so on. This module
   parses them into an `SMConfig` describing the whole table of contents.

2. **`inf.py` — the content container.** Each `INF` file is an OS/2 *Information
   Presentation Facility* compiled-help file (magic `HSP`), the same format
   NewView / fpGUI's docview reads. It holds a global **dictionary** of words, a
   set of **topics** (TOC records), and the per-topic **cells** ("slots"). Topic
   text is not stored as text — it is a byte stream of indices into a tiny
   per-cell word table interleaved with `0xFA…0xFF` formatting codes. This module
   parses the 155-byte header, the dictionary, the topics and the cells.

3. **`render.py` — the cell interpreter.** Walks each cell's byte stream and
   emits HTML: words, paragraph/line breaks, **bold/italic/underline** runs,
   **images**, intra- and inter-document **links**, and the manual's
   side-by-side **tables** (a private `0x29`/`0x2d`/`0xF6` vocabulary that encodes
   column counts and pixel widths). Auto-links are *inlined* so a page assembles
   itself from several cells the way SITV did.

4. **`bitmap.py` — the figures.** Two image families are embedded in the INF:
   CCITT Group-4 fax line-art (wrapped into a TIFF for libtiff), and OS/2 `'bM'`
   bitmaps compressed in blocks (raw, a custom byte-RLE, or LZW). The byte-RLE
   was reverse-engineered for this project; see `FORMAT.md`.

5. **`build.py` — site assembly.** Plans the page tree from the navigation index,
   renders every page, extracts and de-duplicates images, generates standalone
   **reference pages** for cross-link targets that aren't navigation nodes (so
   "Refer to GROUP 11 – Timing Belt" actually goes somewhere), and writes the
   contents index and CSS.

The exact binary layouts — headers, escape codes, the table grammar, the RLE
scheme — are documented in **[`FORMAT.md`](FORMAT.md)** and in dense comments
throughout the source.

## Usage

```bash
pip install -r requirements.txt        # just Pillow

python3 regenerate.py                  # build every config into recreated/
python3 regenerate.py --config 205/1997 --no-images   # one config, text only
python3 verify.py                      # measure text coverage vs output/

python3 -m pytest tests/               # run the test suite
```

Open `recreated/index.html` in a browser.

## A note, and an apology

Unfortunately, this reconstruction was written with heavy use of an AI coding assistant (Claude Code)

Large-scale AI and the speculative build-out of hyperscale data centers are an environmental
problem: grid strain, water draw, emissions, among other issues,
often sited next to communities that get the costs and none of the benefit.

So if this code was useful to you, please consider sending a few dollars to
people working on the other side of that ledger:

**Environment / climate**
- [350.org](https://350.org/) — grassroots climate movement.
- [Cool Earth](https://www.coolearth.org/) — rainforest protection, with people
  who live there, and unusually transparent about results.
- [Trees for the Future](https://trees.org/) — reforestation and regenerative
  farming.

**The footprint of computing & data centers**
- [The Green Web Foundation](https://www.thegreenwebfoundation.org/) — working
  toward a fossil-free internet; tools and research on data-center energy.
- [Low-Tech Magazine](https://solar.lowtechmagazine.com/) — runs on a
  solar-powered server; a working argument for digital *de*growth.
- And: support the **local campaigns** in your area resisting speculative
  data-center construction — they are usually small, volunteer, and underfunded,
  and they are where this fight is actually happening.

If you only have the energy for one click, the local one matters most.

## Licence / provenance

The decoded *content* is Mitsubishi's copyrighted service manual; this repository
is a format-recovery and preservation exercise. The **code** in `src/`, the
tests, and the format documentation are the contribution here.
