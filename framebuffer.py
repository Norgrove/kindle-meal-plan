"""Turn a rendered grayscale PNG into a raw Kindle 4 framebuffer image.

The Kindle 4's screen memory is 600x800, one byte per pixel, inverted (0 is
white). Writing that straight to /dev/fb0 and asking FBInk to refresh takes
about 25 ms, against about 120 ms for FBInk to decode the PNG on every key
press. Checked on the device: each byte is 255 minus the PNG's gray value.
Standard library only.
"""
import struct
import zlib


def read_gray(path):
    """Return (width, height, pixel bytes) for an 8-bit grayscale PNG."""
    data = open(path, "rb").read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path} is not a PNG")
    pos, idat, header = 8, b"", None
    while pos < len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat += body
    width, height, depth, colour, _, _, interlace = header
    if depth != 8 or colour != 0 or interlace:
        raise ValueError(f"{path}: expected an 8-bit non-interlaced grayscale PNG")

    raw, out, prev = zlib.decompress(idat), bytearray(), bytearray(width)
    for y in range(height):
        start = y * (width + 1)
        kind, line = raw[start], bytearray(raw[start + 1:start + 1 + width])
        if kind == 1:      # Sub
            for x in range(1, width):
                line[x] = (line[x] + line[x - 1]) & 255
        elif kind == 2:    # Up
            line = bytearray((a + b) & 255 for a, b in zip(line, prev))
        elif kind == 3:    # Average
            for x in range(width):
                line[x] = (line[x] + ((line[x - 1] if x else 0) + prev[x]) // 2) & 255
        elif kind == 4:    # Paeth
            for x in range(width):
                a, b, c = (line[x - 1] if x else 0), prev[x], (prev[x - 1] if x else 0)
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        out += line
        prev = line
    return width, height, bytes(out)


INVERT = bytes(255 - v for v in range(256))


def write_raw(png, raw):
    width, height, pixels = read_gray(png)
    if (width, height) != (600, 800):
        raise ValueError(f"{png} is {width}x{height}, not the Kindle's 600x800")
    raw.write_bytes(pixels.translate(INVERT))
