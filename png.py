"""Tiny grayscale raster canvas that writes PNGs with the standard library only.

Enough drawing for the Kindle charts and icons: rectangles, ellipses, triangles,
dashed lines, a checkerboard "dither" fill and a 5x7 bitmap font for the few
labels the charts need.
"""
import math
import struct
import zlib

BLACK, WHITE = 0, 255
DITHER = "dither"  # checkerboard fill that reads as mid-gray on e-ink

_FONT = {
    "M": "X...X XX.XX X.X.X X.X.X X...X X...X X...X",
    "T": "XXXXX ..X.. ..X.. ..X.. ..X.. ..X.. ..X..",
    "W": "X...X X...X X...X X.X.X X.X.X XX.XX X...X",
    "F": "XXXXX X.... X.... XXXX. X.... X.... X....",
    "S": ".XXXX X.... X.... .XXX. ....X ....X XXXX.",
    "0": ".XXX. X...X X..XX X.X.X XX..X X...X .XXX.",
    "1": "..X.. .XX.. ..X.. ..X.. ..X.. ..X.. .XXX.",
    "2": ".XXX. X...X ....X ...X. ..X.. .X... XXXXX",
    "3": "XXXXX ...X. ..X.. ...X. ....X X...X .XXX.",
    "4": "...X. ..XX. .X.X. X..X. XXXXX ...X. ...X.",
    "5": "XXXXX X.... XXXX. ....X ....X X...X .XXX.",
    "6": "..XX. .X... X.... XXXX. X...X X...X .XXX.",
    "7": "XXXXX ....X ...X. ..X.. .X... .X... .X...",
    "8": ".XXX. X...X X...X .XXX. X...X X...X .XXX.",
    "9": ".XXX. X...X X...X .XXXX ....X ...X. .XX..",
    "g": "..... ..... .XXXX X...X .XXXX ....X .XXX.",
    " ": "..... ..... ..... ..... ..... ..... .....",
}


class Canvas:
    def __init__(self, width, height, background=WHITE):
        self.w, self.h = width, height
        self.buf = bytearray([background]) * (width * height)

    def _value(self, fill, x, y):
        if fill == DITHER:
            return 85 if ((x // 2) + (y // 2)) % 2 == 0 else 220
        return fill

    def px(self, x, y, fill):
        if 0 <= x < self.w and 0 <= y < self.h:
            self.buf[y * self.w + x] = self._value(fill, x, y)

    def rect(self, x0, y0, x1, y1, fill):
        """Fill the half-open box [x0, x1) x [y0, y1)."""
        for y in range(max(0, int(y0)), min(self.h, int(y1))):
            for x in range(max(0, int(x0)), min(self.w, int(x1))):
                self.buf[y * self.w + x] = self._value(fill, x, y)

    def frame(self, x0, y0, x1, y1, width=2, fill=BLACK):
        self.rect(x0, y0, x1, y0 + width, fill)
        self.rect(x0, y1 - width, x1, y1, fill)
        self.rect(x0, y0, x0 + width, y1, fill)
        self.rect(x1 - width, y0, x1, y1, fill)

    def hdash(self, x0, x1, y, width=2, dash=8, gap=6, fill=BLACK):
        x = x0
        while x < x1:
            self.rect(x, y, min(x + dash, x1), y + width, fill)
            x += dash + gap

    def vdash(self, x, y0, y1, width=2, dash=6, gap=4, fill=BLACK):
        y = y0
        while y < y1:
            self.rect(x, y, x + width, min(y + dash, y1), fill)
            y += dash + gap

    def ellipse(self, cx, cy, rx, ry, fill, rot=0.0, clip=None):
        """Fill an ellipse rotated by `rot` degrees; `clip(x, y)` can mask it."""
        if rx <= 0 or ry <= 0:
            return
        c, s = math.cos(math.radians(rot)), math.sin(math.radians(rot))
        r = int(max(rx, ry)) + 1
        for y in range(int(cy) - r, int(cy) + r + 1):
            for x in range(int(cx) - r, int(cx) + r + 1):
                dx, dy = x - cx, y - cy
                u, v = dx * c + dy * s, -dx * s + dy * c
                if (u / rx) ** 2 + (v / ry) ** 2 <= 1 and (clip is None or clip(x, y)):
                    self.px(x, y, fill)

    def outlined_ellipse(self, cx, cy, rx, ry, fill, stroke, rot=0.0, clip=None):
        self.ellipse(cx, cy, rx, ry, BLACK, rot, clip)
        self.ellipse(cx, cy, rx - stroke, ry - stroke, fill, rot, clip)

    def triangle(self, a, b, c, fill):
        def side(p, q, x, y):
            return (q[0] - p[0]) * (y - p[1]) - (q[1] - p[1]) * (x - p[0])

        xs, ys = (a[0], b[0], c[0]), (a[1], b[1], c[1])
        for y in range(int(min(ys)), int(max(ys)) + 1):
            for x in range(int(min(xs)), int(max(xs)) + 1):
                d = side(a, b, x, y), side(b, c, x, y), side(c, a, x, y)
                if all(v >= 0 for v in d) or all(v <= 0 for v in d):
                    self.px(x, y, fill)

    def text(self, x, y, s, scale=2, fill=BLACK):
        for ch in s:
            rows = _FONT.get(ch, _FONT[" "]).split()
            for ry, row in enumerate(rows):
                for rx, bit in enumerate(row):
                    if bit == "X":
                        self.rect(x + rx * scale, y + ry * scale,
                                  x + (rx + 1) * scale, y + (ry + 1) * scale, fill)
            x += 6 * scale

    @staticmethod
    def text_width(s, scale=2):
        return len(s) * 6 * scale - scale

    def save(self, path):
        raw = bytearray()
        for y in range(self.h):
            raw.append(0)
            raw += self.buf[y * self.w:(y + 1) * self.w]

        def chunk(kind, data):
            body = kind + data
            return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

        with open(path, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n")
            f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", self.w, self.h, 8, 0, 0, 0, 0)))
            f.write(chunk(b"IDAT", zlib.compress(bytes(raw), 9)))
            f.write(chunk(b"IEND", b""))
