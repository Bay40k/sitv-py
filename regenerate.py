#!/usr/bin/env python3
"""Regenerate the HTML service manual from input/ into recreated/.

Usage:
  python3 regenerate.py [--input DIR] [--out DIR] [--no-images]
                        [--config MODEL/YEAR ...]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from sitv.build import build_all, CONFIGS


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", default="input")
    ap.add_argument("--out", default="recreated")
    ap.add_argument("--no-images", action="store_true")
    ap.add_argument("--config", action="append", default=None,
                    help="MODEL/YEAR, e.g. 205/1997 (repeatable)")
    args = ap.parse_args()

    configs = CONFIGS
    if args.config:
        configs = [tuple(c.split("/")) for c in args.config]

    summary = build_all(args.input, args.out, configs=configs,
                        with_images=not args.no_images)
    print(f"{'config':12} {'pages':>6} {'images':>7} {'img ok':>7} {'img fail':>9}")
    for model, year, pages, imgs, ok, fail in summary:
        print(f"{model}/{year:8} {pages:6d} {imgs:7d} {ok:7d} {fail:9d}")


if __name__ == "__main__":
    main()
