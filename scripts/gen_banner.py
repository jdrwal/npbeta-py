"""Render a sunset city-skyline banner as a PNG using only the stdlib.

Run:  python scripts/gen_banner.py  ->  static/img/login-banner.png
Then convert to JPEG with `sips -s format jpeg`.
"""

from __future__ import annotations

import math
import random
import struct
import zlib
from pathlib import Path

W, H = 1600, 440
OUT = Path(__file__).resolve().parent.parent / "static" / "img" / "login-banner.png"


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def mix(c1: tuple, c2: tuple, t: float) -> tuple:
    return tuple(lerp(c1[i], c2[i], t) for i in range(3))


# Vertical sky gradient stops (y-fraction -> RGB).
SKY = [
    (0.00, (30, 27, 80)),    # deep indigo
    (0.45, (67, 56, 202)),   # indigo-700
    (0.70, (109, 92, 214)),  # violet
    (0.86, (224, 158, 150)),  # warm haze
    (1.00, (248, 197, 142)),  # amber horizon
]


def sky_color(t: float) -> tuple:
    for i in range(len(SKY) - 1):
        t0, c0 = SKY[i]
        t1, c1 = SKY[i + 1]
        if t <= t1:
            return mix(c0, c1, (t - t0) / (t1 - t0))
    return SKY[-1][1]


def main() -> None:
    rnd = random.Random(7)
    px = bytearray(W * H * 3)

    sun_cx, sun_cy, sun_r = W * 0.5, H * 0.42, 78.0

    def put(x: int, y: int, c: tuple) -> None:
        i = (y * W + x) * 3
        px[i] = max(0, min(255, int(c[0])))
        px[i + 1] = max(0, min(255, int(c[1])))
        px[i + 2] = max(0, min(255, int(c[2])))

    # Sky + sun glow.
    for y in range(H):
        base = sky_color(y / (H - 1))
        for x in range(W):
            c = base
            d = math.hypot(x - sun_cx, y - sun_cy)
            if d < 260:
                glow = max(0.0, 1.0 - d / 260.0) ** 2
                c = mix(c, (255, 224, 170), glow * 0.6)
            if d < sun_r:
                edge = 1.0 - max(0.0, (d - (sun_r - 10)) / 10.0)
                c = mix(c, (255, 210, 120), min(1.0, edge))
            n = rnd.uniform(-3, 3)
            put(x, y, (c[0] + n, c[1] + n, c[2] + n))

    # Building rows: (x, width, top_y, color).
    back = (46, 40, 110)
    front = (24, 20, 62)

    def draw_building(bx: int, bw: int, top: int, color: tuple, lit: float) -> None:
        for y in range(top, H):
            for x in range(bx, min(bx + bw, W)):
                if x < 0:
                    continue
                shade = 1.0 + (y - top) / (H - top) * 0.15
                put(x, y, (color[0] * shade, color[1] * shade, color[2] * shade))
        # windows
        wx, wy, gap = 16, 20, 30
        cols = max(1, (bw - 24) // gap)
        rows = max(1, (H - top - 24) // gap)
        for r in range(rows):
            for cc in range(cols):
                if rnd.random() > lit:
                    continue
                x0 = bx + 14 + cc * gap
                y0 = top + 16 + r * gap
                warm = (250, 204, 120) if rnd.random() > 0.25 else (255, 176, 120)
                for yy in range(y0, min(y0 + wy, H)):
                    for xx in range(x0, min(x0 + wx, bx + bw, W)):
                        if 0 <= xx < W:
                            put(xx, yy, warm)

    # Back row (lighter, taller-spread).
    bx = -40
    while bx < W + 40:
        bw = rnd.randint(70, 130)
        top = rnd.randint(150, 250)
        draw_building(bx, bw, top, back, 0.18)
        bx += bw + rnd.randint(6, 20)

    # Front row (darker, closer, more lit windows).
    bx = -30
    while bx < W + 30:
        bw = rnd.randint(110, 190)
        top = rnd.randint(210, 320)
        draw_building(bx, bw, top, front, 0.32)
        bx += bw + rnd.randint(2, 14)

    write_png(OUT, W, H, px)
    print(f"wrote {OUT} ({W}x{H})")


def write_png(path: Path, w: int, h: int, pixels: bytearray) -> None:
    def chunk(typ: bytes, data: bytes) -> bytes:
        body = typ + data
        return (
            struct.pack(">I", len(data))
            + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    stride = w * 3
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        raw += pixels[y * stride:(y + 1) * stride]
    idat = zlib.compress(bytes(raw), 9)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", ihdr))
        f.write(chunk(b"IDAT", idat))
        f.write(chunk(b"IEND", b""))


if __name__ == "__main__":
    main()
