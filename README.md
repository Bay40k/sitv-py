# SITV — Reverse-engineered Mitsubishi service-manual viewer

This project reconstructs the output of **SITV 1.08**, a long-dead OS/2 program
that rendered the factory service manual for the 1994–1997 Mitsubishi Eclipse /
Eclipse Spyder (Pub. MSCD-002B-97) into a web of HTML pages. The original
software is unobtainable. What survives is the data that went *into* it
(`input/`) and a partial sample of the HTML it produced (`output/`).

Given only those two things, the code here decodes the original binary data and
regenerates a clean, browsable static site (`recreated/`) — one HTML page per
manual topic, with every embedded diagram recovered as a PNG and every
cross-reference link wired up.

## Status

- **1,428 pages per model-year** across 6 configurations (205 & 220, 1994–1997).
- **~5,650 figures per config decoded, 0 failures.**
- **92.5 % word-5-gram coverage** of the reference `output/` sample (the missing
  few percent is mostly table whitespace and a handful of dangling source links).
- Full rebuild in ~3 minutes; 39 unit tests.

## How it works

The input is two kinds of file, both undocumented:

```
input/                 ← not in git; copy from the CD as described below
  SM00016F             top-level SM index file (root of the TOC tree)
  205/ 220/            model codes (sales regions)
    1994/ … 1997/      per model-year SM**** index files (navigation/TOC)
  INF/
    *.INF              root-level compiled INF files
    205/ 220/          per-model compiled content — OS/2 IPF "INF" help files
```

**Input source:** `input/` is the directory structure from the factory service
manual CD (MSCD-002B-97), copied verbatim.  Mount or rip the CD and copy the
contents into `input/` before running the build.  `input/` and `output/` are
excluded from version control (see `.gitignore`).

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

This reconstruction was written with heavy use of an AI coding assistant. 

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
