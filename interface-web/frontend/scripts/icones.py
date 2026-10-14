"""Génère les icônes PNG de l'appli installable (orbe dorée sur bleu nuit), sans dépendance.

Usage : python scripts/icones.py  →  public/icons/atlas-192.png, atlas-512.png, atlas-maskable-512.png
"""

import math
import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "public" / "icons"
BG = (9, 14, 24)


def mix(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


def pixel(x, y, size, radius_ratio):
    cx = cy = size / 2
    r = size * radius_ratio
    d = math.hypot(x - cx, y - cy)
    color = BG
    # halo
    if d < r * 2.0:
        glow = max(0.0, 1 - (d - r * 0.6) / (r * 1.4)) ** 2 * 0.45
        color = mix(color, (215, 166, 75), glow)
    if d <= r:
        # sphère : reflet en haut à gauche, or au centre, bord plus sombre
        hd = math.hypot(x - (cx - r * 0.32), y - (cy - r * 0.36)) / r
        base = mix((255, 248, 228), (246, 213, 138), min(1, hd / 0.45))
        base = mix(base, (215, 166, 75), max(0, min(1, (hd - 0.45) / 0.6)))
        edge = max(0, (d / r - 0.82) / 0.18)
        base = mix(base, (110, 75, 25), edge * 0.7)
        aa = min(1, r - d)  # anticrénelage du bord
        color = mix(color, base, aa)
    return tuple(int(max(0, min(255, round(c)))) for c in color)


def png(size, radius_ratio):
    rows = bytearray()
    for y in range(size):
        rows.append(0)
        for x in range(size):
            rows.extend(pixel(x + 0.5, y + 0.5, size, radius_ratio))
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(bytes(rows), 9)) + chunk(b"IEND", b"")


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "atlas-192.png").write_bytes(png(192, 0.3))
    (OUT / "atlas-512.png").write_bytes(png(512, 0.3))
    (OUT / "atlas-maskable-512.png").write_bytes(png(512, 0.24))  # zone sûre des icônes adaptatives Android
    print("icônes écrites dans", OUT)
