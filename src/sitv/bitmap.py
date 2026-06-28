"""Decode OS/2 IPF embedded bitmaps to PNG.

Ported from fpGUI docview's HelpBitmap.pas + lzwdecompress.pas. Bitmaps are
OS/2 1.x BMPs ('bM'), stored in blocks that are either raw or LZW compressed.
"""
from __future__ import annotations

import struct
from typing import List, Optional

BFT_BMAP = 0x4D62        # 'bM'
BFT_BITMAP_ARRAY = 0x4142  # 'BA'

CLEAR_TABLE = 256
TERMINATOR = 257
FIRST_CODE = 258
INIT_BITS = 9
MAX_BITS = 12


class _LZW:
    """Stateful LZW decoder matching the IPF (DDJ-derived) variant."""

    def __init__(self):
        self.prefix = [0] * 5022
        self.append = [0] * 5022

    def decompress_block(self, data: bytes, out: bytearray):
        """Decompress one block, appending to `out`. Returns (n_written, final_byte)."""
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
            nonlocal pos, bit_count, bit_buffer, bytes_read
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
            decode_stack[i] = code
            return i  # top index

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
                    decode_stack[0] = character
                    top = decode_string(1, last_code)
                else:
                    top = decode_string(0, code)
                character = decode_stack[top]
                # emit decode_stack[top..0]
                j = top
                while j >= 0:
                    final = decode_stack[j]
                    out.append(final)
                    written += 1
                    j -= 1
                if next_code <= max_code:
                    prefix[next_code] = last_code
                    append[next_code] = character
                    next_code += 1
                    if next_code == max_code and bits_per_code < MAX_BITS:
                        bits_per_code += 1
                        max_code = (1 << bits_per_code) - 1
                last_code = code
            code = input_code()
        return written, final


def _read_bits_size(cbitcount: int, cx: int, cy: int) -> int:
    words_per_line = (cbitcount * cx + 31) // 32
    return words_per_line * 4 * cy, words_per_line * 4


def decode_bitmap(data: bytes, base: int):
    """Decode the bitmap at file offset `base`. Returns a PIL Image or None."""
    from PIL import Image

    p = base
    image_type = struct.unpack_from("<H", data, p)[0]
    if image_type == BFT_BITMAP_ARRAY:
        p += 14  # sizeof INFBITMAPARRAYHEADER
    # bitmap file+info header (26 bytes)
    (usType, cbSize, xHot, yHot, offBits, cbFix, cx, cy,
     cPlanes, cBitCount) = struct.unpack_from("<HIHHIIHHHH", data, p)
    p += 26
    if usType != BFT_BMAP or cPlanes != 1:
        return None

    palette_count = (1 << cBitCount) if cBitCount < 24 else 0
    palette = []
    if cBitCount <= 8:
        for _ in range(palette_count):
            b, g, r = data[p], data[p + 1], data[p + 2]
            palette.append((r, g, b))
            p += 3

    bits_size, line_size = _read_bits_size(cBitCount, cx, cy)

    # data header
    total_size, uncompressed_block = struct.unpack_from("<IH", data, p)
    p += 6

    bits = bytearray()
    bytes_read = 2  # uncompressed_block field already counted
    lzw = _LZW()
    last_byte = 0
    while bytes_read < total_size:
        size = struct.unpack_from("<H", data, p)[0]
        p += 2
        ctype = data[p]
        p += 1
        bytes_read += 3
        size -= 1
        block = data[p:p + size]
        p += size
        bytes_read += size

        if ctype in (0, 1):
            bits += block
        elif ctype == 2:
            before = len(bits)
            written, last_byte = lzw.decompress_block(block, bits)
            # pad short blocks with the final byte
            if written < uncompressed_block and len(bits) < bits_size:
                pad = min(uncompressed_block - written, bits_size - len(bits))
                bits += bytes([last_byte]) * pad
        else:
            return None
        if len(bits) >= bits_size:
            break

    if len(bits) < bits_size:
        bits += bytes(bits_size - len(bits))

    return _bits_to_image(bytes(bits), cx, cy, cBitCount, line_size, palette)


def _bits_to_image(bits: bytes, cx: int, cy: int, bpp: int, line_size: int, palette):
    from PIL import Image

    img = Image.new("RGB", (cx, cy))
    px = img.load()
    for row in range(cy):
        # OS/2 BMP rows are bottom-up
        src = (cy - 1 - row) * line_size
        line = bits[src:src + line_size]
        if bpp == 1:
            for x in range(cx):
                bit = (line[x >> 3] >> (7 - (x & 7))) & 1
                px[x, row] = palette[bit]
        elif bpp == 4:
            for x in range(cx):
                byte = line[x >> 1]
                idx = (byte >> 4) if (x & 1) == 0 else (byte & 0x0F)
                px[x, row] = palette[idx]
        elif bpp == 8:
            for x in range(cx):
                px[x, row] = palette[line[x]]
        elif bpp == 24:
            for x in range(cx):
                o = x * 3
                px[x, row] = (line[o + 2], line[o + 1], line[o])
    return img
