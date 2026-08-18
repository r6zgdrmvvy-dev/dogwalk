#!/usr/bin/env python3
"""Generate assets/city.png -- the hand-authored pixel-art tileset for the game.

Everything is drawn from a single cohesive palette: five values per material,
hue-shifted so shadows drift blue and highlights drift warm. Texture comes from
ordered dithering between neighbouring ramp steps rather than random noise, which
is what keeps a small palette from looking muddy when the tiles repeat.

Light is fixed to the north-west throughout, so cast shadows all fall south-east.

Run:  python3 scripts/make_tiles.py
Writes assets/city.png and assets/city-index.json.
"""

import json
import os
import random

from PIL import Image, ImageDraw

T = 16            # tile size in pixels
COLS = 8          # sheet width in tiles

# ---------------------------------------------------------------- palette ----
# Each ramp runs darkest -> lightest. Shadow ends are pulled toward blue,
# highlight ends toward yellow, which reads as daylight rather than grey wash.

TARMAC = [(38, 40, 48), (52, 55, 64), (66, 70, 80), (80, 85, 95), (98, 103, 113)]
PAVING = [(92, 88, 84), (116, 112, 106), (140, 136, 128), (162, 158, 150), (184, 180, 171)]
GRASS = [(44, 68, 48), (58, 88, 58), (74, 108, 68), (92, 128, 80), (116, 150, 96)]
HEDGE = [(30, 50, 36), (40, 66, 44), (52, 84, 54), (66, 100, 64), (84, 118, 78)]
EARTH = [(52, 42, 34), (70, 57, 45), (90, 74, 58), (110, 92, 72), (132, 112, 90)]

SLATE = [(44, 48, 60), (58, 64, 78), (74, 80, 96), (92, 98, 114), (112, 118, 134)]
CHARCOAL = [(34, 34, 38), (46, 47, 52), (60, 62, 68), (76, 78, 85), (94, 96, 104)]
PANTILE = [(92, 48, 36), (120, 64, 44), (148, 84, 56), (174, 106, 74), (198, 132, 98)]
WEATHERED = [(48, 56, 56), (64, 74, 74), (82, 94, 92), (100, 114, 110), (122, 136, 130)]

RED_SANDSTONE = [(86, 52, 44), (112, 68, 54), (138, 86, 66), (162, 108, 84), (186, 134, 106)]
BLOND_SANDSTONE = [(104, 86, 60), (132, 112, 78), (158, 138, 100), (182, 162, 124), (204, 186, 150)]
HARLING = [(86, 84, 80), (110, 108, 102), (134, 132, 124), (156, 154, 146), (178, 176, 167)]
RENDER = [(120, 116, 110), (148, 144, 136), (174, 170, 162), (196, 193, 185), (218, 215, 208)]

WHITE = (232, 230, 224)
YELLOW = (206, 170, 62)
GLASS = [(46, 58, 74), (62, 80, 100), (96, 122, 146), (150, 178, 198)]
WOOD = [(48, 34, 26), (70, 50, 36), (96, 70, 50), (124, 94, 68)]
SHADOW = (0, 0, 0, 70)

# A flat felted roof, for the parade of shops. Nothing pitched about it.
FELT = [(46, 50, 50), (60, 65, 64), (76, 82, 80), (92, 98, 95), (114, 120, 116)]

ROOF_RAMPS = [SLATE, CHARCOAL, PANTILE, WEATHERED, FELT]
WALL_RAMPS = [RED_SANDSTONE, BLOND_SANDSTONE, HARLING, RENDER]

# Fascia colours for shopfronts — the painted board above the window.
FASCIA = [(58, 76, 62), (74, 44, 44), (40, 56, 78), (72, 60, 36)]

rng = random.Random(20260816)


# ----------------------------------------------------------------- helpers ---
def new(bg=(0, 0, 0, 0)):
    """Blank tile. Accepts a 3- or 4-tuple; RGB is treated as fully opaque."""
    c = tuple(bg)
    if len(c) == 3:
        c = c + (255,)
    im = Image.new("RGBA", (T, T), c)
    return im, ImageDraw.Draw(im)


def put(im, x, y, c):
    if 0 <= x < T and 0 <= y < T:
        if len(c) == 3:
            c = c + (255,)
        im.putpixel((x, y), c)


def rect(d, x0, y0, x1, y1, c):
    """Filled rect that tolerates being pushed off-tile by staggering."""
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(T - 1, x1), min(T - 1, y1)
    if x1 < x0 or y1 < y0:
        return
    d.rectangle([x0, y0, x1, y1], fill=c)


def dither(im, x0, y0, x1, y1, c_lo, c_hi, density=0.5, phase=0):
    """Ordered checkerboard between two ramp steps.

    The classic way to pull an implied mid-tone -- and visible texture -- out of
    a palette that only has five steps. `density` biases which of the two wins.
    """
    for y in range(max(0, y0), min(T, y1 + 1)):
        for x in range(max(0, x0), min(T, x1 + 1)):
            on = ((x + y + phase) % 2) == 0
            if density < 0.5:
                on = on and ((x * 2 + y) % 4 == 0 or (x + y * 2) % 4 == 0)
            elif density > 0.5:
                on = on or ((x + y + phase) % 4 == 1)
            put(im, x, y, c_hi if on else c_lo)


def speckle(im, x0, y0, x1, y1, c, chance, r=None):
    r = r or rng
    for y in range(max(0, y0), min(T, y1 + 1)):
        for x in range(max(0, x0), min(T, x1 + 1)):
            if r.random() < chance:
                put(im, x, y, c)


def shade(im, x0, y0, x1, y1, alpha=70):
    """Multiply-ish shadow wash, used for cast shadows so they keep hue."""
    for y in range(max(0, y0), min(T, y1 + 1)):
        for x in range(max(0, x0), min(T, x1 + 1)):
            p = im.getpixel((x, y))
            if p[3] == 0:
                continue
            f = 1.0 - alpha / 255.0
            put(im, x, y, (int(p[0] * f), int(p[1] * f * 0.99), int(p[2] * f + 6), p[3]))


# ------------------------------------------------------------------- roads ---
def tarmac(variant=0):
    """Worn Glasgow tarmac: dithered base with aggregate speckle and patches."""
    im, d = new(TARMAC[2])
    r = random.Random(400 + variant)
    dither(im, 0, 0, T - 1, T - 1, TARMAC[2], TARMAC[3], phase=variant)
    speckle(im, 0, 0, T - 1, T - 1, TARMAC[1], 0.10, r)
    speckle(im, 0, 0, T - 1, T - 1, TARMAC[4], 0.07, r)
    if variant == 1:
        # a resurfaced patch -- slightly darker, with a ragged seam
        px, py = r.randrange(0, 7), r.randrange(0, 7)
        dither(im, px, py, px + 8, py + 7, TARMAC[1], TARMAC[2], phase=1)
        for i in range(9):
            put(im, px + i, py - 1 + (i % 2), TARMAC[1])
    return im


def drain():
    im = tarmac(0)
    d = ImageDraw.Draw(im)
    rect(d, 5, 6, 10, 10, TARMAC[0])
    for y in range(7, 10):
        rect(d, 6, y, 9, y, TARMAC[2] if y % 2 else TARMAC[0])
    rect(d, 5, 5, 10, 5, TARMAC[3])       # lit lip, NW light
    rect(d, 5, 11, 10, 11, TARMAC[0])
    return im


def dash(vertical=False):
    im = tarmac(0)
    d = ImageDraw.Draw(im)
    if vertical:
        rect(d, 7, 2, 8, 13, WHITE)
        rect(d, 9, 3, 9, 13, (140, 138, 132))   # slight wear on the shadow side
    else:
        rect(d, 2, 7, 13, 8, WHITE)
        rect(d, 3, 9, 13, 9, (140, 138, 132))
    return im


def double_yellow(vertical=False):
    im = tarmac(0)
    d = ImageDraw.Draw(im)
    if vertical:
        rect(d, 2, 0, 2, T - 1, YELLOW)
        rect(d, 4, 0, 4, T - 1, YELLOW)
    else:
        rect(d, 0, 2, T - 1, 2, YELLOW)
        rect(d, 0, 4, T - 1, 4, YELLOW)
    speckle(im, 0, 0, T - 1, T - 1, TARMAC[3], 0.05, random.Random(9))
    return im


def zebra(vertical=False):
    im = tarmac(0)
    d = ImageDraw.Draw(im)
    for i in range(0, T, 4):
        if vertical:
            rect(d, 0, i, T - 1, i + 1, WHITE)
        else:
            rect(d, i, 0, i + 1, T - 1, WHITE)
    speckle(im, 0, 0, T - 1, T - 1, TARMAC[3], 0.06, random.Random(11))
    return im


# --------------------------------------------------------------- pavements ---
def paving(variant=0):
    """Concrete slabs, 8x8, with recessed joints and per-slab tonal variation."""
    im, d = new(PAVING[2])
    r = random.Random(700 + variant)
    for sy in (0, 8):
        for sx in (0, 8):
            tone = r.choice([PAVING[1], PAVING[2], PAVING[2], PAVING[3]])
            hi = PAVING[min(4, PAVING.index(tone) + 1)]
            rect(d, sx, sy, sx + 7, sy + 7, tone)
            dither(im, sx, sy, sx + 7, sy + 7, tone, hi, density=0.3, phase=sx + sy)
            rect(d, sx, sy, sx + 7, sy, hi)              # lit top edge
            rect(d, sx, sy, sx, sy + 7, hi)              # lit left edge
            rect(d, sx, sy + 7, sx + 7, sy + 7, PAVING[0])   # joint
            rect(d, sx + 7, sy, sx + 7, sy + 7, PAVING[0])
    speckle(im, 0, 0, T - 1, T - 1, PAVING[1], 0.05, r)
    return im


def driveway():
    """Block paving -- brick-bond setts, the standard suburban driveway."""
    im, d = new(PAVING[1])
    r = random.Random(731)
    for row in range(4):
        y = row * 4
        off = 2 if row % 2 else 0
        for x in range(-2, T, 4):
            tone = r.choice([EARTH[2], EARTH[3], PAVING[1], EARTH[2]])
            rect(d, x + off, y, x + off + 2, y + 2, tone)
            rect(d, x + off, y, x + off + 2, y, EARTH[4] if tone[0] > 80 else PAVING[2])
    return im


def kerb(side):
    """Directional kerb: pavement on the inside, tarmac beyond, stone lip between."""
    im = paving(0)
    d = ImageDraw.Draw(im)
    if side == "n":
        dither(im, 0, 0, T - 1, 2, TARMAC[2], TARMAC[3])
        rect(d, 0, 3, T - 1, 3, PAVING[4])
        rect(d, 0, 4, T - 1, 4, PAVING[1])
    elif side == "s":
        dither(im, 0, T - 3, T - 1, T - 1, TARMAC[2], TARMAC[3])
        rect(d, 0, T - 4, T - 1, T - 4, PAVING[4])
        rect(d, 0, T - 5, T - 1, T - 5, PAVING[1])
    elif side == "w":
        dither(im, 0, 0, 2, T - 1, TARMAC[2], TARMAC[3])
        rect(d, 3, 0, 3, T - 1, PAVING[4])
        rect(d, 4, 0, 4, T - 1, PAVING[1])
    else:
        dither(im, T - 3, 0, T - 1, T - 1, TARMAC[2], TARMAC[3])
        rect(d, T - 4, 0, T - 4, T - 1, PAVING[4])
        rect(d, T - 5, 0, T - 5, T - 1, PAVING[1])
    return im


# ----------------------------------------------------------------- growing ---
def grass(flowers=False):
    im, d = new(GRASS[2])
    r = random.Random(900 + int(flowers))
    dither(im, 0, 0, T - 1, T - 1, GRASS[1], GRASS[2], density=0.6)
    speckle(im, 0, 0, T - 1, T - 1, GRASS[3], 0.16, r)
    speckle(im, 0, 0, T - 1, T - 1, GRASS[0], 0.08, r)
    # tufts: short vertical blades catching the light
    for _ in range(9):
        x, y = r.randrange(T), r.randrange(1, T)
        put(im, x, y, GRASS[4])
        put(im, x, y - 1, GRASS[3])
    if flowers:
        for c in [(226, 220, 196), (216, 196, 96), (214, 208, 220)]:
            x, y = r.randrange(1, T - 1), r.randrange(1, T - 1)
            put(im, x, y, c)
            put(im, x + 1, y, c)
            put(im, x, y + 1, GRASS[0])
    return im


def scrub():
    """Rough waste ground -- patchy grass over bare earth."""
    im, d = new(EARTH[2])
    r = random.Random(921)
    dither(im, 0, 0, T - 1, T - 1, EARTH[1], EARTH[2], density=0.55)
    speckle(im, 0, 0, T - 1, T - 1, EARTH[3], 0.12, r)
    for _ in range(14):
        x, y = r.randrange(T), r.randrange(T)
        put(im, x, y, GRASS[1])
        put(im, x, y - 1, GRASS[2])
        put(im, x + 1, y, GRASS[1])
    speckle(im, 0, 0, T - 1, T - 1, EARTH[0], 0.07, r)
    return im


def hedge():
    """Clipped privet -- the boundary of every suburban Glasgow front garden.

    Built from overlapping leaf clumps rather than a flat wash, so it reads as
    dense foliage with a lit crown instead of a green rectangle.
    """
    im, d = new(HEDGE[1])
    r = random.Random(940)
    dither(im, 0, 0, T - 1, T - 1, HEDGE[1], HEDGE[2], density=0.5)
    # clumps, each shaded by height: crown catches light, skirt falls into shade
    for _ in range(70):
        cx, cy = r.randrange(-1, T + 1), r.randrange(-1, T + 1)
        size = r.choice([1, 1, 2])
        lit = 1.0 - (cy / float(T))
        idx = 0 if lit < 0.18 else 1 if lit < 0.38 else 2 if lit < 0.60 else 3 if lit < 0.82 else 4
        idx = max(0, min(4, idx + r.choice([-1, 0, 0, 0])))
        for yy in range(cy, cy + size + 1):
            for xx in range(cx, cx + size + 1):
                put(im, xx, yy, HEDGE[idx])
    # trimmed top edge and the shadow the hedge throws on itself at the base
    for x in range(T):
        put(im, x, 0, HEDGE[4] if (x + r.randrange(2)) % 2 else HEDGE[3])
    dither(im, 0, T - 2, T - 1, T - 1, HEDGE[0], HEDGE[1], density=0.3)
    return im


def tree(variant=0):
    """Canopy with volume: lit NW crown, core mid-tone, SE shadow side, plus a
    cast shadow on the ground so it sits in the world instead of floating."""
    ramps = [HEDGE, GRASS, [(70, 58, 34), (96, 80, 44), (124, 106, 58), (152, 132, 76), (180, 162, 100)], HEDGE]
    ramp = ramps[variant % len(ramps)]
    r = random.Random(1000 + variant)
    im, d = new((0, 0, 0, 0))

    # cast shadow, south-east, soft-edged by dropping every other pixel
    for y in range(8, 16):
        for x in range(6, 16):
            dx, dy = (x - 10) / 5.5, (y - 11) / 4.0
            if dx * dx + dy * dy <= 1.0 and (x + y) % 3 != 2:
                put(im, x, y, (18, 22, 30, 90))

    cx, cy = 7, 7
    rad = 6.2 if variant != 3 else 5.0
    for y in range(T):
        for x in range(T):
            dx, dy = x - cx, y - cy
            dist = (dx * dx + dy * dy) ** 0.5
            if dist > rad:
                continue
            # lambert-ish: north-west is lit
            lit = (-dx - dy) / (rad * 1.6) + 0.45
            lit += (r.random() - 0.5) * 0.22          # leaf break-up
            idx = 0 if lit < 0.18 else 1 if lit < 0.38 else 2 if lit < 0.62 else 3 if lit < 0.84 else 4
            if dist > rad - 1.1 and idx > 1:
                idx -= 1                               # darker silhouette edge
            put(im, x, y, ramp[idx])

    # trunk peeking out at the base
    rect(d, cx, cy + 5, cx + 1, cy + 7, WOOD[1])
    put(im, cx, cy + 5, WOOD[2])
    return im


def dirt_path():
    im, d = new(EARTH[3])
    r = random.Random(960)
    dither(im, 0, 0, T - 1, T - 1, EARTH[2], EARTH[3], density=0.55)
    speckle(im, 0, 0, T - 1, T - 1, EARTH[4], 0.10, r)
    speckle(im, 0, 0, T - 1, T - 1, EARTH[1], 0.10, r)
    return im


def water():
    im, d = new((44, 72, 96))
    r = random.Random(970)
    dither(im, 0, 0, T - 1, T - 1, (40, 66, 90), (52, 84, 110), density=0.55)
    for _ in range(5):
        x, y = r.randrange(1, T - 3), r.randrange(T)
        rect(d, x, y, x + 2, y, (96, 134, 158))
    return im


# ------------------------------------------------------------------- roofs ---
def roof(ramp, edge=None):
    """Slate roof seen from above. `edge` adds the eaves/gutter on one side."""
    im, d = new(ramp[2])
    r = random.Random(1100 + ramp[0][0] + (0 if edge is None else ord(edge)))
    # Individual slates in staggered courses. Each slate gets its own tone, a lit
    # top lip and a dark joint down its right side -- the vertical joints are what
    # stop the courses collapsing into horizontal siding.
    for row in range(-1, T, 4):
        off = 3 if ((row + 1) // 4) % 2 else 0
        for x in range(-5, T, 5):
            i = r.choice([1, 2, 2, 2, 3])
            rect(d, x + off, row, x + off + 3, row + 3, ramp[i])
            rect(d, x + off, row, x + off + 3, row, ramp[min(4, i + 1)])
            rect(d, x + off + 4, row, x + off + 4, row + 3, ramp[0])
            if r.random() < 0.25:                      # a chipped / weathered slate
                put(im, x + off + r.randrange(4), row + 3, ramp[0])
        for x in range(0, T):                          # broken overlap shadow
            if (x + row) % 5 != 3:
                put(im, x, row + 3, ramp[max(0, 1)])
    speckle(im, 0, 0, T - 1, T - 1, ramp[0], 0.04, r)

    if edge == "n":
        rect(d, 0, 0, T - 1, 0, ramp[4])               # lit ridge tile
        rect(d, 0, 1, T - 1, 1, ramp[3])
        rect(d, 0, 2, T - 1, 2, ramp[0])
    elif edge == "s":
        rect(d, 0, T - 3, T - 1, T - 3, ramp[0])       # eaves shadow
        rect(d, 0, T - 2, T - 1, T - 2, ramp[3])       # gutter, catching light
        rect(d, 0, T - 1, T - 1, T - 1, ramp[0])
    elif edge == "w":
        rect(d, 0, 0, 0, T - 1, ramp[4])
        rect(d, 1, 0, 1, T - 1, ramp[3])
        rect(d, 2, 0, 2, T - 1, ramp[0])
    elif edge == "e":
        rect(d, T - 3, 0, T - 3, T - 1, ramp[0])
        rect(d, T - 2, 0, T - 2, T - 1, ramp[2])
        rect(d, T - 1, 0, T - 1, T - 1, ramp[0])
    return im


def flat_roof(ramp, edge=None):
    """The felted flat roof of a shop unit or a block of flats.

    Drawn as its own thing rather than slates in grey, because that is the whole
    point: from above, a parade of shops has to be legible as *not houses* at a
    glance. So: a bitumen membrane in overlapping strips, a rooflight or a vent
    box here and there, and a coping-stone parapet where the roof ends.
    """
    im, d = new(ramp[2])
    r = random.Random(1700 + ramp[0][0] + (0 if edge is None else ord(edge)))
    # Membrane laid in strips, each seam a dark line with a lit lap above it.
    dither(im, 0, 0, T - 1, T - 1, ramp[1], ramp[2], 0.5)
    for row in range(1, T, 5):
        rect(d, 0, row, T - 1, row, ramp[0])
        rect(d, 0, row - 1, T - 1, row - 1, ramp[3])
    # Ponding: felt never lies flat, and the puddles are what give it depth.
    for _ in range(2):
        px, py = r.randrange(0, T - 5), r.randrange(0, T - 4)
        dither(im, px, py, px + 4, py + 3, ramp[0], ramp[1], 0.6)
    if r.random() < 0.35:                              # rooflight or plant box
        bx, by = r.randrange(2, T - 6), r.randrange(2, T - 6)
        rect(d, bx, by, bx + 3, by + 3, ramp[4])
        rect(d, bx, by, bx + 3, by, ramp[3])
        rect(d, bx + 4, by + 1, bx + 4, by + 4, ramp[0])   # cast shadow, NW light
        rect(d, bx + 1, by + 4, bx + 4, by + 4, ramp[0])
    speckle(im, 0, 0, T - 1, T - 1, ramp[0], 0.05, r)

    # Parapet: a raised upstand with a pale coping along the top, so the edge of
    # a flat roof reads as a wall rather than as a cut.
    if edge == "n":
        rect(d, 0, 0, T - 1, 0, ramp[4])
        rect(d, 0, 1, T - 1, 1, ramp[3])
        rect(d, 0, 2, T - 1, 2, ramp[0])
    elif edge == "s":
        rect(d, 0, T - 3, T - 1, T - 3, ramp[0])
        rect(d, 0, T - 2, T - 1, T - 2, ramp[4])
        rect(d, 0, T - 1, T - 1, T - 1, ramp[1])
    elif edge == "w":
        rect(d, 0, 0, 0, T - 1, ramp[4])
        rect(d, 1, 0, 1, T - 1, ramp[3])
        rect(d, 2, 0, 2, T - 1, ramp[0])
    elif edge == "e":
        rect(d, T - 3, 0, T - 3, T - 1, ramp[0])
        rect(d, T - 2, 0, T - 2, T - 1, ramp[4])
        rect(d, T - 1, 0, T - 1, T - 1, ramp[1])
    return im


# ------------------------------------------------------------------- walls ---
def wall_base(ramp, seed):
    """Masonry ground: staggered courses with per-block tonal variation."""
    im, d = new(ramp[2])
    r = random.Random(seed)
    for row in range(0, T, 4):
        off = 3 if (row // 4) % 2 else 0
        for x in range(-6, T, 6):
            tone = r.choice([ramp[1], ramp[2], ramp[2], ramp[3]])
            rect(d, x + off, row, x + off + 5, row + 3, tone)
            rect(d, x + off, row, x + off + 5, row, ramp[min(4, ramp.index(tone) + 1)])
            rect(d, x + off + 5, row, x + off + 5, row + 3, ramp[0])
        rect(d, 0, row + 3, T - 1, row + 3, ramp[0])
    speckle(im, 0, 0, T - 1, T - 1, ramp[1], 0.05, r)
    return im


def wall_window(ramp):
    im = wall_base(ramp, 1200 + ramp[0][0])
    d = ImageDraw.Draw(im)
    x0, y0, x1, y1 = 4, 3, 11, 11
    rect(d, x0 - 1, y0 - 1, x1 + 1, y1 + 1, ramp[4])        # stone surround
    rect(d, x0, y0, x1, y1, GLASS[1])
    dither(im, x0, y0, x1, y0 + 3, GLASS[2], GLASS[3], density=0.35)   # sky reflection
    dither(im, x0, y0 + 4, x1, y1, GLASS[0], GLASS[1], density=0.4)
    rect(d, 7, y0, 8, y1, RENDER[4])                        # sash bar
    rect(d, x0, 7, x1, 7, RENDER[4])
    rect(d, x0 - 1, y1 + 1, x1 + 1, y1 + 2, PAVING[4])      # sill
    rect(d, x0 - 1, y1 + 3, x1 + 1, y1 + 3, ramp[0])        # sill shadow
    return im


def wall_bay(ramp):
    """Bay window -- projects forward, so it gets its own lit face and shadow."""
    im = wall_base(ramp, 1250 + ramp[0][0])
    d = ImageDraw.Draw(im)
    rect(d, 13, 2, 15, T - 1, ramp[0])                      # cast shadow to the east
    rect(d, 2, 1, 12, T - 1, ramp[3])                       # projecting face, lit
    rect(d, 2, 1, 12, 1, ramp[4])
    rect(d, 3, 3, 11, 10, GLASS[1])
    dither(im, 3, 3, 11, 5, GLASS[2], GLASS[3], density=0.35)
    dither(im, 3, 6, 11, 10, GLASS[0], GLASS[1], density=0.4)
    for bx in (5, 8):
        rect(d, bx, 3, bx, 10, RENDER[3])
    rect(d, 2, 11, 12, 12, PAVING[4])
    rect(d, 2, 13, 12, 13, ramp[0])
    return im


def shopfront(kind):
    """A parade shop seen face-on: painted fascia board, big plate window with a
    stallriser under it, and a recessed door. Reads as commercial at a glance
    because the glass runs the full width, which a house's never does."""
    im, d = new(HARLING[2])
    r = random.Random(1400 + kind)
    fascia = FASCIA[kind % len(FASCIA)]
    # rendered surround
    for row in range(0, T, 4):
        rect(d, 0, row, T - 1, row, HARLING[1])
    rect(d, 0, 0, T - 1, 3, fascia)                      # fascia board
    rect(d, 0, 0, T - 1, 0, tuple(min(255, c + 26) for c in fascia))
    rect(d, 0, 3, T - 1, 3, tuple(max(0, c - 18) for c in fascia))
    for x in range(1, T - 1, 3):                         # lettering, suggested
        put(im, x, 1, (206, 198, 176))
        put(im, x + 1, 1, (206, 198, 176))

    if kind % 4 == 3:
        rect(d, 1, 5, 14, 12, WOOD[1])                   # a shuttered unit
        for x in range(1, 15):
            put(im, x, 6, WOOD[2]); put(im, x, 9, WOOD[2])
    else:
        rect(d, 1, 5, 14, 11, GLASS[1])                  # plate glass
        dither(im, 1, 5, 14, 7, GLASS[2], GLASS[3], density=0.4)
        dither(im, 1, 8, 14, 11, GLASS[0], GLASS[1], density=0.45)
        rect(d, 7, 5, 7, 11, HARLING[3])                 # mullion
        if kind % 4 == 1:
            rect(d, 10, 5, 14, 11, WOOD[2])              # door into the shop
            put(im, 11, 8, (206, 180, 96))
    rect(d, 0, 12, T - 1, 13, PAVING[2])                 # stallriser
    rect(d, 0, 14, T - 1, T - 1, PAVING[1])              # pavement at the foot
    return im


def wall_door(ramp):
    im = wall_base(ramp, 1300 + ramp[0][0])
    d = ImageDraw.Draw(im)
    rect(d, 4, 2, 11, T - 1, ramp[4])                       # surround
    rect(d, 5, 3, 10, T - 1, WOOD[1])
    rect(d, 5, 3, 10, 3, WOOD[3])                           # lit head
    rect(d, 6, 5, 9, 8, WOOD[2])                            # upper panel
    rect(d, 6, 10, 9, 13, WOOD[2])                          # lower panel
    put(im, 9, 9, (206, 180, 96))                           # brass handle
    rect(d, 5, 4, 10, 4, GLASS[2])                          # fanlight
    rect(d, 4, T - 2, 11, T - 1, PAVING[3])                 # step
    return im


# ------------------------------------------------------------------- props ---
# Props are authored on a 32px frame -- two tiles across -- so that at native
# scale a car comes out about 4m long and the pixels still line up exactly with
# the ground. Scaling a 16px sprite up by some fraction would land its pixels
# off the tile grid and the whole thing would stop reading as pixel art.
PT = 32

CAR_COLOURS = [
    [(58, 22, 24), (92, 34, 36), (132, 48, 50), (176, 74, 74)],       # red
    [(24, 38, 58), (36, 58, 90), (52, 84, 128), (86, 122, 168)],      # blue
    [(46, 46, 50), (68, 70, 76), (96, 100, 108), (134, 140, 150)],    # silver
    [(30, 44, 34), (44, 66, 48), (62, 92, 66), (94, 126, 96)],        # green
]


def pnew():
    im = Image.new("RGBA", (PT, PT), (0, 0, 0, 0))
    return im, ImageDraw.Draw(im)


def pput(im, x, y, c):
    if 0 <= x < PT and 0 <= y < PT:
        im.putpixel((x, y), c if len(c) == 4 else c + (255,))


def pshadow(body, dx=2, dy=2, alpha=96):
    """Cast shadow taken from the prop's own silhouette, offset south-east and
    dithered at the edge. Far better than a blocked-in rectangle -- the shadow
    is the right shape, and it costs nothing to keep it consistent."""
    im, _ = pnew()
    for y in range(PT):
        for x in range(PT):
            sx, sy = x - dx, y - dy
            if not (0 <= sx < PT and 0 <= sy < PT):
                continue
            if body.getpixel((sx, sy))[3] < 128:
                continue
            if (x + y) % 4 == 3:
                continue
            pput(im, x, y, (16, 18, 26, alpha))
    im.alpha_composite(body)
    return im


def prect(d, x0, y0, x1, y1, c):
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(PT - 1, x1), min(PT - 1, y1)
    if x1 < x0 or y1 < y0:
        return
    d.rectangle([x0, y0, x1, y1], fill=c)


def car(ramp, vertical=False):
    """Top-down car, lit from the north-west: roof brightest, flanks stepping
    down, glass dark, and a shadow thrown south-east so it sits on the road.

    Drawn pointing east, then rotated. The shadow goes on afterwards so it
    keeps falling south-east whichever way the car ends up facing.
    """
    body, d = pnew()
    prect(d, 2, 10, 29, 21, ramp[2])                     # body shell
    prect(d, 2, 10, 29, 11, ramp[3])                     # lit north flank
    prect(d, 2, 20, 29, 21, ramp[0])                     # shadowed south flank
    prect(d, 2, 12, 3, 19, ramp[1])                      # boot
    prect(d, 28, 12, 29, 19, ramp[1])                    # bonnet
    prect(d, 9, 11, 21, 20, ramp[3])                     # roof
    prect(d, 9, 11, 21, 12, ramp[3])
    prect(d, 22, 12, 25, 19, GLASS[1])                   # windscreen
    prect(d, 22, 12, 25, 14, GLASS[2])
    prect(d, 5, 12, 8, 19, GLASS[0])                     # rear screen
    prect(d, 10, 13, 11, 18, GLASS[1])                   # side glass
    prect(d, 19, 13, 20, 18, GLASS[1])
    prect(d, 28, 11, 29, 12, (238, 226, 170))            # headlights
    prect(d, 28, 19, 29, 20, (238, 226, 170))
    prect(d, 2, 11, 3, 12, (150, 40, 40))                # tail lights
    prect(d, 2, 19, 3, 20, (150, 40, 40))
    for wx in (6, 22):                                   # tyres
        prect(d, wx, 8, wx + 3, 9, (24, 24, 28))
        prect(d, wx, 22, wx + 3, 23, (24, 24, 28))
    prect(d, 21, 9, 22, 9, (40, 42, 48))                 # wing mirrors
    prect(d, 21, 22, 22, 22, (40, 42, 48))

    if vertical:
        body = body.rotate(90, expand=False)
    return pshadow(body, 2, 2)


def lamp_post():
    """Column seen from above, arm reaching east over the carriageway."""
    im, d = pnew()
    prect(d, 7, 16, 24, 17, (58, 60, 66))                # arm, shaded underside
    prect(d, 7, 15, 24, 15, (116, 120, 130))             # arm, lit along the top
    prect(d, 23, 13, 28, 19, (52, 54, 60))               # lantern housing
    prect(d, 24, 14, 27, 18, (214, 208, 178))            # diffuser
    prect(d, 24, 14, 27, 14, (240, 234, 206))
    for y in range(12, 20):                              # column, seen end-on
        for x in range(3, 11):
            dx, dy = (x - 6.5) / 3.2, (y - 15.5) / 3.2
            if dx * dx + dy * dy <= 1.0:
                lit = (-dx - dy) * 0.5 + 0.5
                pput(im, x, y, (132, 136, 146) if lit > 0.66 else
                               ((84, 87, 94) if lit > 0.36 else (46, 48, 54)))
    return pshadow(im, 2, 3, 80)


def wheelie_bin():
    im, d = pnew()
    prect(d, 11, 11, 20, 20, HEDGE[1])                   # body
    prect(d, 11, 11, 20, 12, HEDGE[3])                   # lid, catching light
    prect(d, 11, 15, 20, 15, HEDGE[0])                   # lid seam
    prect(d, 11, 20, 20, 20, HEDGE[0])
    prect(d, 11, 11, 11, 20, HEDGE[2])
    return pshadow(im, 2, 2, 80)


def post_box():
    """A red pillar box. Nothing says British street quite as fast."""
    im, d = pnew()
    for y in range(9, 22):
        for x in range(10, 22):
            dx, dy = (x - 15.5) / 5.5, (y - 15.5) / 6.0
            if dx * dx + dy * dy <= 1.0:
                lit = (-dx - dy) * 0.5 + 0.5
                pput(im, x, y, (168, 40, 40) if lit > 0.62 else
                               ((132, 30, 32) if lit > 0.34 else (94, 22, 24)))
    prect(d, 12, 10, 19, 10, (196, 60, 58))              # domed top
    prect(d, 13, 13, 18, 14, (36, 16, 18))               # posting slot
    return pshadow(im, 2, 2, 90)


def bench():
    im, d = pnew()
    for sy in (11, 15, 19):
        prect(d, 6, sy, 25, sy + 1, WOOD[2])
        prect(d, 6, sy + 2, 25, sy + 2, WOOD[1])
    prect(d, 6, 11, 7, 21, (56, 58, 62))                 # cast-iron ends
    prect(d, 24, 11, 25, 21, (56, 58, 62))
    return pshadow(im, 2, 2, 80)


# ------------------------------------------------------------------ people ---
WT = 16           # walker frame, same size as the dog's

# Two people to pick from. Seen from directly above you get hair, shoulders,
# a coat and the tips of two shoes, so that is what has to carry the read:
# hair colour and length, and the colour of the coat.
WALKERS = [
    {"key": "her",
     "hair": [(58, 34, 22), (86, 52, 30), (118, 74, 42)],
     "coat": [(96, 38, 52), (132, 56, 72), (170, 84, 100)],
     "skin": (214, 168, 134), "long": True},
    {"key": "him",
     "hair": [(38, 32, 28), (58, 50, 44), (82, 72, 64)],
     "coat": [(38, 62, 88), (54, 84, 116), (78, 112, 148)],
     "skin": (222, 180, 146), "long": False},
    # The rest are other people, out walking their own dogs. Same drawing, a
    # different coat and head — two figures repeated down a street reads as a
    # bug rather than as a neighbourhood.
    {"key": "npc1",
     "hair": [(48, 44, 40), (68, 62, 56), (92, 84, 76)],
     "coat": [(46, 66, 50), (64, 92, 68), (88, 122, 92)],
     "skin": (176, 130, 96), "long": True},
    {"key": "npc2",
     "hair": [(96, 88, 78), (132, 124, 112), (170, 162, 150)],
     "coat": [(58, 52, 62), (80, 72, 86), (108, 98, 116)],
     "skin": (238, 202, 170), "long": False},
    {"key": "npc3",
     "hair": [(28, 24, 22), (44, 38, 34), (64, 56, 50)],
     "coat": [(112, 70, 34), (146, 96, 48), (180, 128, 70)],
     "skin": (150, 108, 78), "long": True},
    {"key": "npc4",
     "hair": [(78, 46, 26), (108, 68, 38), (140, 96, 56)],
     "coat": [(62, 62, 66), (86, 86, 92), (114, 114, 122)],
     "skin": (228, 190, 158), "long": False},
]


def walker(spec, frame):
    """One frame of a person walking, seen from above and facing east.

    Rotated to the heading at runtime, exactly like the dog — which is what
    keeps the two of them looking like they belong in the same world, and means
    one four-frame cycle covers every direction.

    The cycle is a contra-swing: arms and legs opposite, mid-stride on the odd
    frames and passing on the even ones, so it reads as walking rather than
    shuffling. Frame 0 doubles as standing still.
    """
    im = Image.new("RGBA", (WT, WT), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    hair, coat, skin = spec["hair"], spec["coat"], spec["skin"]
    EDGE = (28, 26, 32)

    def r(x0, y0, x1, y1, c):
        d.rectangle([max(0, x0), max(0, y0), min(WT - 1, x1), min(WT - 1, y1)], fill=c)

    # swing: -1 trailing, +1 leading. Legs oppose arms.
    swing = [0, 1, 0, -1][frame % 4]

    # Shoes, stepping out from under the hem at the back of the coat.
    r(4 + swing, 5, 5 + swing, 6, (44, 40, 38))
    r(4 - swing, 9, 5 - swing, 10, (44, 40, 38))

    # Arms swinging clear of the shoulders, opposite the legs. Seen from above
    # they are the only moving parts that carry the walk, so they sit outside
    # the coat rather than being lost against it.
    r(6 - swing, 3, 8 - swing, 4, coat[0])
    r(6 + swing, 11, 8 + swing, 12, coat[0])

    # Coat. A person from above is wide across the shoulders and narrow front
    # to back — the opposite way round from a car, which is what an earlier
    # version drew and which is exactly what it looked like.
    r(5, 5, 10, 10, coat[1])
    r(6, 4, 10, 4, coat[1])                                 # shoulder line
    r(6, 11, 10, 11, coat[0])                               # and its shadow
    r(6, 4, 9, 4, coat[2])                                  # lit north edge
    r(5, 10, 10, 10, coat[0])
    r(4, 6, 4, 9, coat[0])                                  # hem, trailing west
    for cx, cy in ((5, 4), (5, 11), (11, 5), (11, 10)):     # knock the corners
        im.putpixel((cx, cy), (0, 0, 0, 0))

    # Head, drawn over the shoulders. Light is fixed north-west, so the north
    # of the crown catches it and the south falls away.
    r(8, 5, 11, 9, hair[1])
    r(8, 5, 11, 5, hair[2])
    r(9, 9, 11, 9, hair[0])
    r(12, 6, 12, 8, skin)                                   # face, looking east
    r(12, 6, 12, 6, (skin[0] - 30, skin[1] - 30, skin[2] - 30))
    if spec["long"]:
        r(7, 4, 8, 10, hair[1])                             # hair past the collar
        r(7, 4, 8, 4, hair[2])
        r(7, 10, 8, 10, hair[0])

    # A dark edge along the south and east, where the ground behind is lit.
    # Without it the figure dissolves into the pavement at walking zoom. Read
    # off a snapshot, not off the image being written to — growing the edge from
    # its own output floods the whole frame.
    solid = [[im.getpixel((x, y))[3] >= 128 for y in range(WT)] for x in range(WT)]
    for y in range(WT):
        for x in range(WT):
            if solid[x][y]:
                continue
            if (x > 0 and solid[x - 1][y]) or (y > 0 and solid[x][y - 1]):
                im.putpixel((x, y), EDGE + (190,))

    # Cast shadow, south-east and dithered at its edge, matching the props.
    sh = Image.new("RGBA", (WT, WT), (0, 0, 0, 0))
    for y in range(WT):
        for x in range(WT):
            sx, sy = x - 1, y - 1
            if not (0 <= sx < WT and 0 <= sy < WT):
                continue
            if im.getpixel((sx, sy))[3] < 128 or (x + y) % 4 == 3:
                continue
            sh.putpixel((x, y), (16, 18, 26, 92))
    sh.alpha_composite(im)
    return sh


SQUIRREL = [(74, 56, 38), (104, 78, 52), (134, 104, 72), (162, 132, 96), (196, 170, 132)]


def squirrel(frame):
    """A grey squirrel from above, facing east: a small body and an enormous
    tail, which is the whole silhouette at this size — nobody would read the
    body alone, but everyone reads the tail.

    Four frames of a bounding run, since a squirrel does not walk. The body
    stretches and gathers and the tail whips over behind it.
    """
    im = Image.new("RGBA", (WT, WT), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    s = SQUIRREL

    def r(x0, y0, x1, y1, c):
        d.rectangle([max(0, x0), max(0, y0), min(WT - 1, x1), min(WT - 1, y1)], fill=c)

    stretch = [0, 1, 2, 1][frame % 4]       # gathered, mid, extended, mid
    lift = [1, 0, -1, 0][frame % 4]         # tail whipping over

    # Tail first, and big: a broad curl sweeping west and up over the back,
    # roughly as much of the sprite as the animal is. It is drawn in the light
    # end of the ramp so it separates from the darker body in front of it.
    ty = 8 + lift
    r(4, ty - 4, 7, ty - 2, s[3])           # the curl coming over the back
    r(3, ty - 3, 5, ty + 1, s[3])           # the fat of it, trailing west
    r(4, ty - 4, 7, ty - 4, s[4])           # lit crown of the curl
    r(2, ty - 2, 3, ty, s[2])               # the tip, curling under
    r(4, ty + 1, 6, ty + 1, s[1])           # underside, in shadow

    # Body: compact, gathering and extending as it bounds.
    r(6, 6, 9 + stretch, 9, s[1])
    r(7, 6, 9 + stretch, 6, s[2])           # lit along the north flank
    r(6, 9, 9 + stretch, 9, s[0])           # shadow along the south

    # Head and one dark eye.
    r(10 + stretch, 6, 12 + stretch, 9, s[2])
    r(10 + stretch, 6, 12 + stretch, 6, s[3])
    im.putpixel((min(WT - 1, 12 + stretch), 8), (26, 22, 18, 255))
    r(10 + stretch, 5, 11 + stretch, 5, s[2])   # ear

    solid = [[im.getpixel((x, y))[3] >= 128 for y in range(WT)] for x in range(WT)]
    for y in range(WT):
        for x in range(WT):
            if solid[x][y]:
                continue
            if (x > 0 and solid[x - 1][y]) or (y > 0 and solid[x][y - 1]):
                im.putpixel((x, y), (28, 24, 20, 180))
    return im


def build_critters():
    return [squirrel(f) for f in range(4)]


# -------------------------------------------------------------------- dogs ---
# Seen from above a dog is a body, a head and a tail, and what tells two breeds
# apart at this size is the proportion between them plus what the ears and tail
# are doing. Colour does the rest of the work.
DOG_TYPES = [
    {"key": "labrador",  "len": 8, "wide": 2, "ears": "drop", "tail": "thick"},
    {"key": "terrier",   "len": 5, "wide": 2, "ears": "perk", "tail": "stub"},
    {"key": "spaniel",   "len": 6, "wide": 2, "ears": "long", "tail": "plume"},
    {"key": "greyhound", "len": 9, "wide": 1, "ears": "back", "tail": "whip"},
    {"key": "collie",    "len": 7, "wide": 2, "ears": "perk", "tail": "plume"},
]

DOG_COATS = [
    {"key": "black",     "ramp": [(18, 18, 22), (30, 30, 36), (44, 44, 52), (60, 60, 70), (80, 80, 92)]},
    {"key": "golden",    "ramp": [(120, 84, 38), (152, 112, 56), (184, 142, 78), (210, 172, 108), (232, 202, 146)]},
    {"key": "chocolate", "ramp": [(52, 32, 22), (74, 48, 32), (98, 66, 44), (124, 88, 60), (152, 114, 82)]},
    {"key": "cream",     "ramp": [(152, 132, 104), (182, 162, 132), (208, 190, 160), (228, 214, 188), (244, 234, 214)]},
    {"key": "grey",      "ramp": [(58, 60, 64), (80, 82, 88), (104, 106, 112), (130, 132, 138), (158, 160, 166)]},
    {"key": "patched",   "ramp": [(26, 26, 30), (44, 44, 50), (64, 64, 72), (196, 192, 186), (232, 230, 226)]},
]

def collar_sprite(t):
    """Just the collar, on its own transparent frame, drawn white so it can be
    tinted to any colour at runtime.

    Baking the collar into the dog would mean a sheet of every type times every
    coat times every collar — seven hundred odd frames to offer six colours.
    One overlay per type costs five.
    """
    im = Image.new("RGBA", (WT, WT), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    cy, W, L = 8, t["wide"], t["len"]
    bx1 = 13 - 3
    d.rectangle([bx1 - 1, cy - W, bx1 - 1, cy + W], fill=(255, 255, 255, 255))
    # A darker pixel on the shadow side so the band still reads as a band once
    # it is tinted a flat colour.
    im.putpixel((bx1 - 1, cy + W), (188, 188, 188, 255))
    return im


def dog_sprite(t, coat, frame):
    """One frame of a dog trotting, from above, facing east.

    Rotated to the heading at runtime like everything else that moves, so a
    four-frame cycle covers every direction. The gait is in the legs and a
    slight swing of the tail; the body stays put, because a dog seen from
    directly above does not bob about the way a person does.
    """
    im = Image.new("RGBA", (WT, WT), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    r = coat["ramp"]
    patchy = coat["key"] == "patched"

    def rect_(x0, y0, x1, y1, c):
        d.rectangle([max(0, x0), max(0, y0), min(WT - 1, x1), min(WT - 1, y1)], fill=c)

    gait = [0, 1, 0, -1][frame % 4]
    swing = [0, -1, 0, 1][frame % 4]
    cy, W, L = 8, t["wide"], t["len"]

    hx1 = 13
    hx0 = hx1 - 2
    bx1 = hx0 - 1
    bx0 = bx1 - L + 1

    # Legs first, so the body sits over them. Front and back pairs step opposite.
    for lx, dirn in ((bx1 - 1, gait), (bx0 + 1, -gait)):
        rect_(lx + dirn, cy - W - 1, lx + dirn, cy - W, r[0])
        rect_(lx - dirn, cy + W, lx - dirn, cy + W + 1, r[0])

    # Tail.
    tail = t["tail"]
    ty = cy + swing
    if tail == "thick":
        rect_(bx0 - 2, ty - 1, bx0 - 1, ty + 1, r[1])
        rect_(bx0 - 2, ty - 1, bx0 - 1, ty - 1, r[2])
    elif tail == "stub":
        rect_(bx0 - 1, ty - 1, bx0 - 1, ty, r[1])
    elif tail == "plume":
        rect_(bx0 - 3, ty - 2, bx0 - 1, ty + 1, r[2])
        rect_(bx0 - 3, ty - 2, bx0 - 1, ty - 2, r[3])
    else:                                            # whip
        rect_(bx0 - 3, ty, bx0 - 1, ty, r[1])

    # Body, lit along its northern flank.
    rect_(bx0, cy - W, bx1, cy + W, r[1])
    rect_(bx0, cy - W, bx1, cy - W, r[2])
    rect_(bx0, cy + W, bx1, cy + W, r[0])
    if patchy:                                       # a black-and-white dog
        rect_(bx0 + 1, cy - W, bx0 + 3, cy + W, r[3])
        rect_(bx0 + 1, cy - W, bx0 + 3, cy - W, r[4])

    # Head, and ears.
    rect_(hx0, cy - W + 1, hx1, cy + W - 1, r[2])
    rect_(hx0, cy - W + 1, hx1, cy - W + 1, r[3])
    ears = t["ears"]
    if ears == "drop":
        rect_(hx0, cy - W, hx0 + 1, cy - W, r[1])
        rect_(hx0, cy + W, hx0 + 1, cy + W, r[0])
    elif ears == "perk":
        rect_(hx0 + 1, cy - W - 1, hx0 + 1, cy - W, r[2])
        rect_(hx0 + 1, cy + W, hx0 + 1, cy + W + 1, r[1])
    elif ears == "long":
        rect_(hx0 - 1, cy - W, hx0 + 1, cy - W + 1, r[1])
        rect_(hx0 - 1, cy + W - 1, hx0 + 1, cy + W, r[0])
    else:                                            # back, flat to the skull
        rect_(hx0 + 1, cy - W + 1, hx0 + 2, cy - W + 1, r[1])
    if patchy:
        rect_(hx1 - 1, cy - W + 1, hx1, cy + W - 1, r[4])   # a white blaze

    im.putpixel((min(WT - 1, hx1), cy), (20, 18, 18, 255))  # nose

    solid = [[im.getpixel((x, y))[3] >= 128 for y in range(WT)] for x in range(WT)]
    for y in range(WT):
        for x in range(WT):
            if solid[x][y]:
                continue
            if (x > 0 and solid[x - 1][y]) or (y > 0 and solid[x][y - 1]):
                im.putpixel((x, y), (22, 20, 24, 175))
    return im


def build_dogs():
    frames, index = [], {"types": [], "coats": [], "stride": 4}
    index["types"] = [t["key"] for t in DOG_TYPES]
    index["coats"] = [c["key"] for c in DOG_COATS]
    for t in DOG_TYPES:
        for c in DOG_COATS:
            for f in range(4):
                frames.append(dog_sprite(t, c, f))
    index["collarBase"] = len(frames)
    for t in DOG_TYPES:
        frames.append(collar_sprite(t))
    return frames, index


def build_people():
    frames, index = [], {}
    for spec in WALKERS:
        index[spec["key"]] = len(frames)
        for f in range(4):
            frames.append(walker(spec, f))
    return frames, index


def build_props():
    props, index = [], {}
    index["carEW"] = []
    index["carNS"] = []
    for ramp in CAR_COLOURS:
        index["carEW"].append(len(props))
        props.append(car(ramp, False))
    for ramp in CAR_COLOURS:
        index["carNS"].append(len(props))
        props.append(car(ramp, True))
    for name, fn in (("lamp", lamp_post), ("bin", wheelie_bin),
                     ("postbox", post_box), ("bench", bench)):
        index[name] = len(props)
        props.append(fn())
    return props, index


# ------------------------------------------------------------------ layout ---
def build():
    tiles = []
    index = {}

    def add(name, im):
        index[name] = len(tiles)
        tiles.append(im)

    add("tarmac", tarmac(0))
    add("tarmacB", tarmac(1))
    add("drain", drain())
    add("dashH", dash(False))
    add("dashV", dash(True))
    add("yellowH", double_yellow(False))
    add("yellowV", double_yellow(True))
    add("zebraH", zebra(False))
    add("zebraV", zebra(True))

    add("pave", paving(0))
    add("paveB", paving(1))
    add("drive", driveway())
    for s in ("n", "s", "w", "e"):
        add("kerb" + s.upper(), kerb(s))

    add("grass", grass(False))
    add("grassFlower", grass(True))
    add("scrub", scrub())
    add("hedge", hedge())
    add("dirt", dirt_path())
    add("water", water())
    index["trees"] = []
    for v in range(4):
        index["trees"].append(len(tiles))
        tiles.append(tree(v))

    index["roofBase"] = len(tiles)
    index["roofStride"] = 5
    index["roofCount"] = len(ROOF_RAMPS)
    for ramp in ROOF_RAMPS:
        for edge in (None, "n", "s", "w", "e"):
            tiles.append(flat_roof(ramp, edge) if ramp is FELT else roof(ramp, edge))

    index["wallBase"] = len(tiles)
    index["wallStride"] = 3
    index["wallCount"] = len(WALL_RAMPS) + 1        # the shopfront is the last one
    for ramp in WALL_RAMPS:
        tiles.append(wall_window(ramp))
        tiles.append(wall_bay(ramp))
        tiles.append(wall_door(ramp))
    index["shopWall"] = len(tiles)
    for k in range(3):
        tiles.append(shopfront(k))

    rows = (len(tiles) + COLS - 1) // COLS
    sheet = Image.new("RGBA", (COLS * T, rows * T), (0, 0, 0, 0))
    for i, im in enumerate(tiles):
        sheet.paste(im, ((i % COLS) * T, (i // COLS) * T))

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sheet.save(os.path.join(here, "assets", "city.png"))
    props, pindex = build_props()
    prows = (len(props) + COLS - 1) // COLS
    psheet = Image.new("RGBA", (COLS * PT, prows * PT), (0, 0, 0, 0))
    for i, im in enumerate(props):
        psheet.paste(im, ((i % COLS) * PT, (i // COLS) * PT))
    psheet.save(os.path.join(here, "assets", "props.png"))

    people, plindex = build_people()
    plsheet = Image.new("RGBA", (len(people) * WT, WT), (0, 0, 0, 0))
    for i, im in enumerate(people):
        plsheet.paste(im, (i * WT, 0))
    plsheet.save(os.path.join(here, "assets", "people.png"))
    index["people"] = plindex

    critters = build_critters()
    csheet = Image.new("RGBA", (len(critters) * WT, WT), (0, 0, 0, 0))
    for i, im in enumerate(critters):
        csheet.paste(im, (i * WT, 0))
    csheet.save(os.path.join(here, "assets", "critters.png"))
    index["critters"] = {"squirrel": 0}

    dogs, dindex = build_dogs()
    DCOLS = 16
    drows = (len(dogs) + DCOLS - 1) // DCOLS
    dsheet = Image.new("RGBA", (DCOLS * WT, drows * WT), (0, 0, 0, 0))
    for i, im in enumerate(dogs):
        dsheet.paste(im, ((i % DCOLS) * WT, (i // DCOLS) * WT))
    dsheet.save(os.path.join(here, "assets", "dogs.png"))
    dindex["cols"] = DCOLS
    index["dogs"] = dindex

    index["_count"] = len(tiles)
    index["_cols"] = COLS
    index["_size"] = T
    index["props"] = pindex
    with open(os.path.join(here, "assets", "city-index.json"), "w") as f:
        json.dump(index, f, indent=2, sort_keys=True)
    pindex["_size"] = PT
    print("wrote %d tiles (%dx%d), %d props (%dx%d), %d walker frames and %d dog frames"
          % (len(tiles), COLS * T, rows * T, len(props), COLS * PT, prows * PT,
             len(people), len(dogs)))
    print(json.dumps(index, sort_keys=True))


if __name__ == "__main__":
    build()
