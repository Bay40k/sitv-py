#!/usr/bin/env python3
"""Verify that the recreated site contains all the text in output/.

Strategy: tokenise the visible text of every reference page in output/ into
word 5-grams and measure how many of those shingles also occur somewhere in the
recreated corpus (all configs combined, since the INF text is shared). A page
counts as fully covered at ~100% shingle coverage.
"""
import os
import re
import sys
from html import unescape

N = 5
JUNK = {"previous", "next", "contents"}


def visible_text(html: str) -> str:
    html = re.sub(r"(?is)<script.*?</script>", " ", html)
    html = re.sub(r"(?is)<style.*?</style>", " ", html)
    html = re.sub(r"(?s)<!--.*?-->", " ", html)
    html = re.sub(r"(?s)<[^>]+>", " ", html)
    return unescape(html)


def tokens(text: str):
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in JUNK]


def shingles(toks, n=N):
    return {tuple(toks[i:i + n]) for i in range(len(toks) - n + 1)}


def gather_corpus(root: str):
    corpus = set()
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            if f.endswith((".htm", ".html")):
                with open(os.path.join(dirpath, f), encoding="utf-8", errors="replace") as fh:
                    corpus |= shingles(tokens(visible_text(fh.read())))
    return corpus


def main():
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "output"
    rec_dir = sys.argv[2] if len(sys.argv) > 2 else "recreated"

    print("Building recreated corpus shingles...")
    corpus = gather_corpus(rec_dir)
    print(f"  {len(corpus):,} distinct {N}-grams in recreated/")

    total_grams = 0
    found_grams = 0
    pages = 0
    worst = []
    for dirpath, _dirs, files in os.walk(out_dir):
        for f in sorted(files):
            if not f.endswith((".htm", ".html")):
                continue
            with open(os.path.join(dirpath, f), encoding="utf-8", errors="replace") as fh:
                toks = tokens(visible_text(fh.read()))
            if len(toks) < N:
                continue
            sh = shingles(toks)
            if not sh:
                continue
            hit = sum(1 for s in sh if s in corpus)
            pages += 1
            total_grams += len(sh)
            found_grams += hit
            cov = hit / len(sh)
            rel = os.path.relpath(os.path.join(dirpath, f), out_dir)
            worst.append((cov, len(sh), rel))

    worst.sort()
    print(f"\nReference pages checked: {pages}")
    print(f"Overall {N}-gram coverage: {found_grams}/{total_grams} = "
          f"{100*found_grams/max(total_grams,1):.2f}%")
    print("\nLowest-coverage reference pages:")
    for cov, n, rel in worst[:15]:
        print(f"  {cov*100:6.1f}%  ({n:5d} grams)  {rel}")


if __name__ == "__main__":
    main()
