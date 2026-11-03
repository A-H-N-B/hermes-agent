#!/usr/bin/env python3
"""Generates the game icon (512x512) and a thumbnail (1920x1080) with the
game's own original slime characters. Requires Pillow.

Usage: python3 tools/make_art.py   (writes marketing/icon.png, marketing/thumbnail.png)
"""
from __future__ import annotations

import math
import os
import random

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "marketing")
FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/inter/InterDisplay-Black.otf",
    "/usr/share/fonts/opentype/inter/InterDisplay-ExtraBold.otf",
    "/usr/share/fonts/opentype/inter/InterDisplay-Bold.otf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def font(size: int):
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def gradient(w, h, top, bottom):
    img = Image.new("RGB", (w, h), top)
    d = ImageDraw.Draw(img)
    for y in range(h):
        d.line([(0, y), (w, y)], fill=lerp(top, bottom, y / max(h - 1, 1)))
    return img


def sparkles(img, count, seed):
    rnd = random.Random(seed)
    d = ImageDraw.Draw(img, "RGBA")
    w, h = img.size
    for _ in range(count):
        x, y = rnd.randrange(w), rnd.randrange(h)
        r = rnd.randrange(2, max(3, w // 90))
        a = rnd.randrange(90, 220)
        d.ellipse([x - r, y - r, x + r, y + r], fill=(255, 255, 255, a))


def slime(img, cx, cy, size, body, accent, accessory="none", mood="happy"):
    """Draws an original slime: jelly ellipsoid, glossy highlight, big eyes."""
    w, h = size * 1.25, size * 0.95
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    # shadow
    d.ellipse([cx - w * 0.5, cy + h * 0.38, cx + w * 0.5, cy + h * 0.58], fill=(0, 0, 0, 70))
    # body with simple radial shading
    steps = 18
    dark = tuple(max(0, c - 70) for c in body)
    for i in range(steps):
        t = i / (steps - 1)
        col = lerp(dark, body, t)
        k = 1 - t * 0.35
        ox, oy = -w * 0.06 * t, -h * 0.08 * t
        d.ellipse([cx - w / 2 * k + ox, cy - h / 2 * k + oy, cx + w / 2 * k + ox, cy + h / 2 * k + oy], fill=col + (255,))
    # outline
    d.ellipse([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], outline=(30, 20, 45, 255), width=max(3, int(size * 0.035)))
    # gloss
    d.ellipse([cx - w * 0.33, cy - h * 0.36, cx - w * 0.12, cy - h * 0.2], fill=(255, 255, 255, 170))
    # eyes
    for side in (-1, 1):
        ex, ey = cx + side * w * 0.17, cy - h * 0.02
        er = size * 0.13
        d.ellipse([ex - er, ey - er * 1.15, ex + er, ey + er * 1.15], fill=(255, 255, 255, 255), outline=(30, 20, 45, 255),
                  width=max(2, int(size * 0.02)))
        pr = er * 0.6
        px = ex + side * er * 0.1 if mood != "sneaky" else ex + er * 0.3
        d.ellipse([px - pr, ey - pr * 0.9, px + pr, ey + pr * 1.1], fill=(25, 18, 35, 255))
        d.ellipse([px - pr * 0.55, ey - pr * 0.7, px - pr * 0.05, ey - pr * 0.2], fill=(255, 255, 255, 255))
        # cheeks
        d.ellipse([ex + side * er * 0.9 - er * 0.5, ey + er * 1.2, ex + side * er * 0.9 + er * 0.5, ey + er * 1.6],
                  fill=(255, 130, 170, 150))
    # mouth
    mw = size * 0.16
    if mood == "sneaky":
        d.arc([cx - mw, cy + h * 0.08, cx + mw, cy + h * 0.24], 200, 340, fill=(30, 20, 45, 255), width=max(3, int(size * 0.03)))
    else:
        d.chord([cx - mw * 0.7, cy + h * 0.1, cx + mw * 0.7, cy + h * 0.26], 0, 180, fill=(60, 20, 40, 255))
    # accessories
    if accessory == "crown":
        top = cy - h * 0.48
        cw = w * 0.36
        pts = [(cx - cw, top), (cx - cw, top - size * 0.22), (cx - cw * 0.5, top - size * 0.1), (cx, top - size * 0.3),
               (cx + cw * 0.5, top - size * 0.1), (cx + cw, top - size * 0.22), (cx + cw, top)]
        d.polygon(pts, fill=(255, 205, 50, 255), outline=(120, 70, 0, 255))
        d.ellipse([cx - size * 0.05, top - size * 0.16, cx + size * 0.05, top - size * 0.06], fill=(255, 60, 100, 255))
    elif accessory == "halo":
        d.ellipse([cx - w * 0.3, cy - h * 0.78, cx + w * 0.3, cy - h * 0.6], outline=(255, 240, 140, 255),
                  width=max(4, int(size * 0.05)))
    elif accessory == "antenna":
        d.line([(cx, cy - h * 0.48), (cx, cy - h * 0.8)], fill=accent + (255,), width=max(3, int(size * 0.04)))
        r = size * 0.08
        d.ellipse([cx - r, cy - h * 0.8 - r, cx + r, cy - h * 0.8 + r], fill=accent + (255,))
    elif accessory == "bow":
        for side in (-1, 1):
            d.polygon([(cx, cy - h * 0.42), (cx + side * w * 0.22, cy - h * 0.55), (cx + side * w * 0.22, cy - h * 0.3)],
                      fill=accent + (255,))
        d.ellipse([cx - size * 0.05, cy - h * 0.47, cx + size * 0.05, cy - h * 0.37], fill=accent + (255,))
    glow = layer.filter(ImageFilter.GaussianBlur(radius=max(2, size // 40)))
    img.paste(glow, (0, 0), glow)
    img.paste(layer, (0, 0), layer)


def outlined_text(img, xy, text, size, fill, stroke=(30, 20, 45), anchor="mm", stroke_width=None):
    d = ImageDraw.Draw(img)
    f = font(size)
    d.text(xy, text, font=f, fill=fill, anchor=anchor, stroke_width=stroke_width or max(3, size // 9), stroke_fill=stroke)


def make_icon(path):
    img = gradient(512, 512, (120, 60, 220), (40, 20, 90)).convert("RGBA")
    sparkles(img, 60, 1)
    d = ImageDraw.Draw(img, "RGBA")
    d.ellipse([56, 40, 456, 440], fill=(255, 255, 255, 30))
    slime(img, 140, 330, 120, (255, 150, 210), (230, 90, 170), "bow")
    slime(img, 380, 320, 130, (110, 190, 255), (60, 130, 220), "antenna", mood="sneaky")
    slime(img, 256, 250, 230, (110, 225, 110), (60, 160, 60), "crown")
    outlined_text(img, (256, 62), "SLIME", 96, (150, 255, 160))
    outlined_text(img, (256, 462), "SNATCHERS", 64, (255, 220, 80))
    img.convert("RGB").save(path, optimize=True)


def make_thumbnail(path):
    W, H = 1920, 1080
    img = gradient(W, H, (90, 40, 190), (25, 15, 60)).convert("RGBA")
    sparkles(img, 220, 2)
    d = ImageDraw.Draw(img, "RGBA")
    # conveyor
    d.rectangle([0, 760, W, 860], fill=(55, 50, 70, 255))
    d.rectangle([0, 750, W, 766], fill=(90, 255, 140, 255))
    d.rectangle([0, 854, W, 870], fill=(90, 255, 140, 255))
    lineup = [
        (230, (196, 204, 214), "none", "COMMON", (200, 205, 215)),
        (560, (180, 95, 255), "antenna", "EPIC", (180, 95, 255)),
        (900, (255, 196, 45), "crown", "LEGENDARY", (255, 196, 45)),
        (1260, (255, 72, 112), "bow", "MYTHIC", (255, 72, 112)),
        (1640, (30, 200, 90), "halo", "SECRET", (255, 255, 255)),
    ]
    for i, (x, color, acc, label, lcol) in enumerate(lineup):
        size = 150 + i * 28
        slime(img, x, 760 - size * 0.42, size, color, tuple(max(0, c - 60) for c in color), acc)
        outlined_text(img, (x, 760 - size * 1.05 - (40 if acc in ("halo", "crown", "antenna") else 0)), label, 44, lcol)
    outlined_text(img, (W // 2, 150), "SLIME SNATCHERS", 170, (150, 255, 160), stroke_width=14)
    outlined_text(img, (W // 2, 300), "STEAL • COLLECT • FUSE", 74, (255, 220, 80), stroke_width=9)
    # "STEAL!" burst
    bx, by = 215, 410
    pts = []
    for k in range(24):
        r = 150 if k % 2 == 0 else 105
        a = math.pi * 2 * k / 24
        pts.append((bx + math.cos(a) * r, by + math.sin(a) * r))
    d.polygon(pts, fill=(255, 70, 90, 255), outline=(30, 20, 45, 255))
    outlined_text(img, (bx, by), "STEAL!", 70, (255, 255, 255))
    img.convert("RGB").save(path, optimize=True)


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    make_icon(os.path.join(OUT, "icon.png"))
    make_thumbnail(os.path.join(OUT, "thumbnail.png"))
    print("wrote", os.path.join(OUT, "icon.png"), "and", os.path.join(OUT, "thumbnail.png"))
