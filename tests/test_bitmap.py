"""Tests for the OS/2 bitmap decoders, especially the reverse-engineered RLE."""
import hashlib

import pytest

from sitv.bitmap import decode_bitmap, _rle_decompress_block


# ---------------------------------------------------------------------------
# ctype-1 RLE: the control byte selects repeat (high bit clear) vs literal.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("block_hex, expected_hex", [
    # c < 0x80  -> repeat next byte (c + 1) times
    ("02aa", "aaaaaa"),          # 0x02 -> 3 copies of 0xAA
    ("00ff", "ff"),             # 0x00 -> 1 copy of 0xFF
    ("047f", "7f7f7f7f7f"),     # 0x04 -> 5 copies
    # c >= 0x80 -> copy (c & 0x7f) + 1 following bytes verbatim
    ("8001", "01"),             # 0x80 -> 1 literal byte
    ("82112233", "112233"),     # 0x82 -> 3 literal bytes
    # mixed
    ("02aa8005", "aaaaaa05"),
])
def test_rle_basic_shapes(block_hex, expected_hex):
    out = bytearray()
    _rle_decompress_block(bytes.fromhex(block_hex), out, 100, 100)
    assert out.hex() == expected_hex


def test_rle_respects_ublk_limit():
    """Decoding stops at the per-block ``ublk`` budget."""
    out = bytearray()
    written, last = _rle_decompress_block(bytes([0x10, 0xAB]), out, ublk=4, cap=100)
    assert written == 4 and bytes(out) == b"\xab" * 4 and last == 0xAB


def test_rle_respects_destination_cap():
    out = bytearray(b"xy")          # already 2 bytes toward a cap of 3
    written, _ = _rle_decompress_block(bytes([0x10, 0xAB]), out, ublk=100, cap=3)
    assert written == 1 and bytes(out) == b"xy\xab"


def test_rle_truncated_repeat_is_safe():
    """A repeat opcode with no value byte must not raise."""
    out = bytearray()
    _rle_decompress_block(bytes([0x05]), out, 100, 100)   # dangling repeat
    assert out == b""


# ---------------------------------------------------------------------------
# Whole-image decode (pins exact pixels so optimisation can't drift).
# ---------------------------------------------------------------------------
def _hash(img):
    return hashlib.sha256(img.convert("RGB").tobytes()).hexdigest()[:16]


def test_decode_rle_line_art_figure(inf_trans):
    img = decode_bitmap(inf_trans.data, inf_trans.h["imgstart"] + 159101)
    assert img is not None
    assert img.size == (736, 532)
    # Black-on-white line art: corner pixel is white paper, not black.
    assert img.convert("RGB").getpixel((400, 250)) == (255, 255, 255)
    assert _hash(img) == "d8497712065d0ccf"


def test_decode_rle_icon(inf_root):
    img = decode_bitmap(inf_root.data, inf_root.h["imgstart"] + 909956)
    assert img is not None and img.size == (25, 20)
    assert _hash(img) == "d658d3205995b3a9"


def test_one_bpp_palette_is_inverted_to_black_on_white(inf_trans):
    """The 1-bpp figures store paper as index 0 = black; we must flip them."""
    img = decode_bitmap(inf_trans.data, inf_trans.h["imgstart"] + 159101).convert("L")
    px = img.tobytes()
    # Majority of pixels are the white paper background.
    white = sum(1 for p in px if p > 200)
    assert white > len(px) * 0.6
