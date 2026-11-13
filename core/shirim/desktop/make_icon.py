"""Render the Shirim icon (gradient tile with a music note) to .ico and .png files, no image libraries needed.

The shapes mirror web/icon.svg, drawn in a 64x64 unit space.
"""

import math
import os
import struct
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Gradient start and end colours; the filtered edition gets its own colours so the two apps are easy to tell apart
_PALETTES = {
    'standard': ((0x7B, 0x61, 0xFF), (0xFF, 0x4F, 0x8B)),
    'filtered': ((0x10, 0xB9, 0x81), (0x0E, 0x6F, 0xC4)),
}
_STEM = [((26, 44), (26, 20)), ((26, 20), (44, 16)), ((44, 16), (44, 38))]
_HEADS = [((21, 44), 6), ((39, 40), 6)]


def _in_tile(x, y, radius):
    cx = min(max(x, radius), 64 - radius)
    cy = min(max(y, radius), 64 - radius)
    return (x - cx) ** 2 + (y - cy) ** 2 <= radius * radius


def _segment_distance(px, py, a, b):
    (ax, ay), (bx, by) = a, b
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _in_note(x, y):
    if any((x - cx) ** 2 + (y - cy) ** 2 <= r * r for (cx, cy), r in _HEADS):
        return True
    return any(_segment_distance(x, y, a, b) <= 2 for a, b in _STEM)


def render(size, corner_radius=16, palette='standard'):
    start, end = _PALETTES[palette]
    samples = 3 if size >= 128 else 4
    step = 64 / (size * samples)
    pixels = bytearray(size * size * 4)
    for py in range(size):
        for px in range(size):
            tile = white = 0
            for sy in range(samples):
                y = (py * samples + sy + 0.5) * step
                for sx in range(samples):
                    x = (px * samples + sx + 0.5) * step
                    if _in_tile(x, y, corner_radius):
                        tile += 1
                        if _in_note(x, y):
                            white += 1
            if not tile:
                continue
            t = (px + py) / (2 * size)
            base = [s + (e - s) * t for s, e in zip(start, end)]
            mix = white / tile
            i = (py * size + px) * 4
            pixels[i:i + 3] = bytes(round(c + (255 - c) * mix) for c in base)
            pixels[i + 3] = round(255 * tile / samples ** 2)
    return pixels


def to_png(size, pixels):
    rows = b''.join(b'\x00' + bytes(pixels[y * size * 4:(y + 1) * size * 4]) for y in range(size))

    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', size, size, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(rows, 9))
            + chunk(b'IEND', b''))


def to_ico(pngs):
    """pngs: list of (size, png_bytes). Windows Vista+ accepts PNG-compressed icon entries."""
    header = struct.pack('<HHH', 0, 1, len(pngs))
    offset = 6 + 16 * len(pngs)
    entries, data = b'', b''
    for size, blob in pngs:
        dim = 0 if size >= 256 else size
        entries += struct.pack('<BBBBHHII', dim, dim, 0, 0, 1, 32, len(blob), offset + len(data))
        data += blob
    return header + entries + data


def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(content)


def main():
    cache = {}

    def png(size, palette='standard'):
        key = (size, palette)
        if key not in cache:
            cache[key] = to_png(size, render(size, palette=palette))
        return cache[key]

    sizes = (256, 48, 32, 16)
    _write(os.path.join(ROOT, 'desktop', 'shirim.ico'), to_ico([(s, png(s)) for s in sizes]))
    _write(os.path.join(ROOT, 'desktop', 'shirim-filtered.ico'), to_ico([(s, png(s, 'filtered')) for s in sizes]))
    # iOS ignores SVG touch icons, so the web app also gets PNGs
    _write(os.path.join(ROOT, 'web', 'icon-192.png'), png(192))
    _write(os.path.join(ROOT, 'web', 'icon-512.png'), png(512))

    # Android: the main icon, and an override for the filtered flavor (src/filtered/res wins over src/main/res)
    for source_set, palette in (('main', 'standard'), ('filtered', 'filtered')):
        android_res = os.path.join(ROOT, 'android', 'app', 'src', source_set, 'res')
        for density, size in (('mdpi', 48), ('hdpi', 72), ('xhdpi', 96), ('xxhdpi', 144), ('xxxhdpi', 192)):
            _write(os.path.join(android_res, f'mipmap-{density}', 'ic_launcher.png'), png(size, palette))
    print('Icons written')


if __name__ == '__main__':
    main()
