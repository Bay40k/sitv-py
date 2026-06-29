"""Decode the bitmaps embedded in the SITV INF files to PIL images.

Two formats occur:

* **Figures** (the technical diagrams, the vast majority): a 1-bpp OS/2-style
  bitmap header whose ``usType`` magic is ``0x3459``, followed by a palette and a
  CCITT Group 4 (ITU T.6) fax-compressed bitstream. These are decoded by wrapping
  the stream in a minimal TIFF and letting Pillow/libtiff inflate it.
* **Inline icons / panel art**: a standard OS/2 1.x bitmap (``'bM'`` magic) with
  the IPF block-compression wrapper. Each block is raw (ctype 0), byte-RLE
  (ctype 1) or LZW (ctype 2); blocks decode to ``ublk`` bytes and are padded
  with the last emitted byte when short.
"""
from __future__ import annotations

import io
import struct
from typing import List, Optional

BFT_BMAP = 0x4D62           # 'bM' standard OS/2 bitmap
BFT_BITMAP_ARRAY = 0x4142   # 'BA'
FIG_MAGIC = 0x3459          # SITV figure (CCITT G4) magic

CLEAR_TABLE = 256
TERMINATOR = 257
FIRST_CODE = 258
INIT_BITS = 9
MAX_BITS = 12


# ----------------------------------------------------------------------------
# Public entry point
# ----------------------------------------------------------------------------
def decode_bitmap(data: bytes, base: int):
    """Decode the bitmap at file offset ``base``; return a PIL Image or None.

    Both image families share a 26-byte OS/2 ``BITMAPFILEHEADER`` +
    ``BITMAPCOREHEADER``::

        u16 usType     'bM' (0x4d62) for icons, or 0x3459 for a G4 figure
        u32 cbSize     total bytes of the bitmap
        u16 xHot,yHot  hotspot (ignored)
        u32 offBits    offset from this header to the pixel/strip data
        u32 cbFix      core-header size (12 here)
        u16 cx,cy      width, height in pixels
        u16 cPlanes    1
        u16 cBitCount  bits per pixel (1 or 4 in this data)

    A ``BFT_BITMAP_ARRAY`` ('BA') wrapper may precede it; we skip its 14-byte
    header and decode the first contained bitmap.
    """
    p = base
    image_type = struct.unpack_from("<H", data, p)[0]
    if image_type == BFT_BITMAP_ARRAY:
        p += 14  # skip BITMAPARRAYFILEHEADER, read first bitmap
        image_type = struct.unpack_from("<H", data, p)[0]

    (usType, cbSize, _xHot, _yHot, offBits, _cbFix, cx, cy,
     cPlanes, cBitCount) = struct.unpack_from("<HIHHIIHHHH", data, p)

    if usType == FIG_MAGIC and cBitCount == 1 and cPlanes == 1:
        return _decode_g4_figure(data, p, cx, cy, offBits, cbSize)
    if usType == BFT_BMAP and cPlanes == 1:
        return _decode_os2_bitmap(data, p, cBitCount, cx, cy, offBits)
    return None


# ----------------------------------------------------------------------------
# CCITT Group 4 figures
# ----------------------------------------------------------------------------
def _palette_inverted(data: bytes, p: int, ncolors: int) -> bool:
    """True if palette index 0 is the lighter colour (=> invert fax sense)."""
    pal_off = p + 26
    c0 = sum(data[pal_off:pal_off + 3])
    c1 = sum(data[pal_off + 3:pal_off + 6]) if ncolors > 1 else 0
    return c0 > c1


def _decode_g4_figure(data: bytes, p: int, cx: int, cy: int, offBits: int, cbSize: int):
    from PIL import Image, ImageOps

    start = p + offBits
    # cbSize is the *uncompressed* size; the compressed stream is always smaller
    # for line art, so it is a safe (generous) upper bound for the strip.
    end = min(start + max(cbSize, 1024), len(data))
    strip = data[start:end]
    tiff = _wrap_tiff_g4(strip, cx, cy)
    try:
        img = Image.open(io.BytesIO(tiff))
        img.load()
    except Exception:
        return None
    img = img.convert("1")
    if _palette_inverted(data, p, 2):
        img = ImageOps.invert(img.convert("L")).convert("1")
    return img


def _wrap_tiff_g4(strip: bytes, cx: int, cy: int) -> bytes:
    """Build a minimal single-strip CCITT-G4 TIFF around ``strip``."""
    tags = [
        (256, 3, cx),          # ImageWidth
        (257, 3, cy),          # ImageLength
        (258, 3, 1),           # BitsPerSample
        (259, 3, 4),           # Compression = Group 4
        (262, 3, 0),           # PhotometricInterpretation = WhiteIsZero
        (266, 3, 1),           # FillOrder = MSB-first
        (273, 4, 0),           # StripOffsets (patched below)
        (277, 3, 1),           # SamplesPerPixel
        (278, 3, cy),          # RowsPerStrip
        (279, 4, len(strip)),  # StripByteCounts
    ]
    tags.sort()
    n = len(tags)
    ifd_off = 8
    data_off = ifd_off + 2 + 12 * n + 4
    out = bytearray()
    out += b"II*\x00" + struct.pack("<I", ifd_off)
    out += struct.pack("<H", n)
    for tag, typ, val in tags:
        if tag == 273:
            val = data_off
        out += struct.pack("<HHI", tag, typ, 1)
        if typ == 3:
            out += struct.pack("<HH", val & 0xFFFF, 0)
        else:
            out += struct.pack("<I", val)
    out += struct.pack("<I", 0)
    out += strip
    return bytes(out)


# ----------------------------------------------------------------------------
# Standard OS/2 'bM' bitmaps (inline icons) — best effort
# ----------------------------------------------------------------------------
def _decode_os2_bitmap(data: bytes, p: int, bpp: int, cx: int, cy: int, offBits: int):
    """Decode a 'bM' bitmap (palette + IPF-compressed pixel blocks).

    After the 26-byte header comes the palette: ``2**bpp`` entries of 3 bytes
    each stored **BGR**, which we flip to RGB. The pixel data at ``offBits`` is a
    block stream (see the loop below) producing bottom-up, DWORD-padded rows.
    """
    from PIL import Image

    pal_count = (1 << bpp) if bpp < 24 else 0
    pal = []
    pp = p + 26
    for _ in range(pal_count):
        pal.append((data[pp + 2], data[pp + 1], data[pp]))   # BGR -> RGB
        pp += 3

    # The 1-bpp 'bM' line-art figures store their palette with the opposite
    # bit polarity from the G4 figures: index 0 is black but represents the
    # (majority) paper background, so the manual's diagrams come out inverted.
    # Swap the two entries to render them black-on-white like the originals.
    if bpp == 1 and len(pal) == 2 and sum(pal[0]) < sum(pal[1]):
        pal = [pal[1], pal[0]]

    # Rows are padded up to a 4-byte (32-bit) boundary, the BMP convention.
    words_per_line = (bpp * cx + 31) // 32
    line = words_per_line * 4
    bits_size = line * cy

    # Pixel data = a header (u32 total stream length, u16 ublk = the uncompressed
    # size each block expands to) followed by a series of blocks:
    #     u16 size      length of (ctype + payload), i.e. payload is size-1 bytes
    #     u8  ctype     0 = raw, 1 = RLE, 2 = LZW
    #     ..  payload
    dp = p + offBits
    total, ublk = struct.unpack_from("<IH", data, dp)
    dp += 6
    bits = bytearray()
    read = 2                         # the 'total' counter already covers the 2 header bytes
    last = 0
    lzw = _LZW()
    while read < total and len(bits) < bits_size:
        size = struct.unpack_from("<H", data, dp)[0]
        dp += 2
        ctype = data[dp]
        dp += 1
        read += 3
        size -= 1                    # 'size' counted the ctype byte
        block = data[dp:dp + size]
        dp += size
        read += size
        if ctype == 0:
            bits += block
        elif ctype == 1:
            w, last = _rle_decompress_block(block, bits, ublk, bits_size)
            if w < ublk and len(bits) < bits_size:
                bits += bytes([last]) * min(ublk - w, bits_size - len(bits))
        elif ctype == 2:
            w, last = lzw.decompress_block(block, bits)
            if w < ublk and len(bits) < bits_size:
                bits += bytes([last]) * min(ublk - w, bits_size - len(bits))
        else:
            return None
    if len(bits) < bits_size:
        return None
    return _bits_to_image(bytes(bits), cx, cy, bpp, line, pal)


def _rle_decompress_block(block: bytes, out: bytearray, ublk: int, cap: int):
    """SITV ctype-1 RLE: a control byte selects repeat vs. literal.

    * ``c < 0x80``      -> repeat the next byte ``c + 1`` times.
    * ``c >= 0x80``     -> copy the following ``(c & 0x7f) + 1`` bytes verbatim.

    Decoding stops at ``ublk`` bytes for this block (the caller pads any
    shortfall with the last emitted byte, exactly as the LZW path does).
    Returns ``(bytes_written, last_byte)``.
    """
    p = 0
    n = len(block)
    written = 0
    last = 0
    limit = min(ublk, cap - len(out))
    while p < n and written < limit:
        c = block[p]
        p += 1
        if c & 0x80:                       # literal run
            cnt = (c & 0x7f) + 1
            chunk = block[p:p + cnt]
            p += cnt
            if written + len(chunk) > limit:
                chunk = chunk[:limit - written]
            out += chunk
            written += len(chunk)
            if chunk:
                last = chunk[-1]
        else:                              # repeat run
            cnt = c + 1
            if p >= n:
                break
            last = block[p]
            p += 1
            cnt = min(cnt, limit - written)
            out += bytes([last]) * cnt
            written += cnt
    return written, last


def _bits_to_image(bits: bytes, cx: int, cy: int, bpp: int, line: int, palette):
    """Unpack ``cy`` bottom-up, DWORD-padded rows of packed pixels to an RGB image.

    Pixels are packed MSB-first within each byte: at 1 bpp the pixel ``x`` is bit
    ``7-(x&7)`` of byte ``x>>3``; at 4 bpp it is the high nibble of byte ``x>>1``
    for even ``x``, the low nibble for odd ``x``. Each value indexes ``palette``.
    Row ``row`` lives at ``(cy-1-row)*line`` because BMP stores rows bottom-up.
    """
    from PIL import Image

    img = Image.new("RGB", (cx, cy))
    px = img.load()
    for row in range(cy):
        src = (cy - 1 - row) * line              # bottom-up row order
        ln = bits[src:src + line]
        if bpp == 1:
            for x in range(cx):
                px[x, row] = palette[(ln[x >> 3] >> (7 - (x & 7))) & 1]
        elif bpp == 4:
            for x in range(cx):
                b = ln[x >> 1]
                px[x, row] = palette[(b >> 4) if (x & 1) == 0 else (b & 0x0F)]
        elif bpp == 8:
            for x in range(cx):
                px[x, row] = palette[ln[x]]
        elif bpp == 24:
            for x in range(cx):
                o = x * 3
                px[x, row] = (ln[o + 2], ln[o + 1], ln[o])
    return img


class _LZW:
    """IPF (DDJ-derived) LZW used by ctype-2 'bM' blocks."""

    def __init__(self):
        self.prefix = [0] * 5022
        self.append = [0] * 5022

    def decompress_block(self, data: bytes, out: bytearray):
        prefix = self.prefix
        append = self.append
        decode_stack = bytearray(10001)
        pos = 0
        nbytes = len(data)
        bits_per_code = INIT_BITS
        max_code = (1 << bits_per_code) - 1
        bit_count = 0
        bit_buffer = 0
        bytes_read = 0
        written = 0
        final = 0

        def input_code():
            nonlocal pos, bit_count, bit_buffer, bytes_read, bits_per_code
            while bit_count <= 24:
                if bytes_read <= nbytes:
                    if pos < nbytes:
                        bit_buffer |= data[pos] << (24 - bit_count)
                    pos += 1
                bytes_read += 1
                bit_count += 8
            ret = (bit_buffer >> (32 - bits_per_code)) & 0xFFFFFFFF
            bit_buffer = (bit_buffer << bits_per_code) & 0xFFFFFFFF
            bit_count -= bits_per_code
            if bytes_read > nbytes:
                return TERMINATOR
            return ret

        def decode_string(start_idx, code):
            i = start_idx
            while code > 255:
                decode_stack[i] = append[code]
                i += 1
                code = prefix[code]
            decode_stack[i] = code & 0xFF
            return i

        clear_flag = True
        next_code = FIRST_CODE
        last_code = 0
        character = 0
        code = input_code()
        while code != TERMINATOR:
            if clear_flag:
                clear_flag = False
                last_code = code
                character = code
                out.append(code & 0xFF)
                final = code & 0xFF
                written += 1
            elif code == CLEAR_TABLE:
                clear_flag = True
                next_code = FIRST_CODE
                bits_per_code = INIT_BITS
                max_code = (1 << bits_per_code) - 1
            else:
                if code >= next_code:
                    decode_stack[0] = character & 0xFF
                    top = decode_string(1, last_code)
                else:
                    top = decode_string(0, code)
                character = decode_stack[top]
                j = top
                while j >= 0:
                    final = decode_stack[j]
                    out.append(final)
                    written += 1
                    j -= 1
                if next_code <= max_code:
                    prefix[next_code] = last_code
                    append[next_code] = character & 0xFF
                    next_code += 1
                    if next_code == max_code and bits_per_code < MAX_BITS:
                        bits_per_code += 1
                        max_code = (1 << bits_per_code) - 1
                last_code = code
            code = input_code()
        return written, final
