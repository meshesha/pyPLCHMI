"""Generate the built-in animated GIF library of pyPLCHMI.

All animations are original drawings made with Pillow, so they can be
shipped and used freely with the application.  Each frame is drawn at four
times the target size and scaled down (anti-aliasing); all frames share one
palette whose last entry is transparent, so the GIFs look right on light and
dark screens.

Usage (from the project folder, inside the venv)::

    python tools/make_animations.py            # writes app/library/animations/
    python tools/make_animations.py --size 160 --out some/folder

The output folder gets one sub-folder per category (``HVAC``, ``Electrical``,
``Water``, ``Generators``, ``Fuel``, ``Status``) and a ``manifest.json``
listing every file with its display name.  ``flask library animations`` (or
the *Add built-in animations* button of the image library) imports them into
the image library folder ``Animations/<Category>``.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random

from PIL import Image, ImageDraw

#: Supersampling factor (frames are drawn this much larger, then reduced).
SS = 4

# Colour palette shared by all drawings.
OUTLINE = (31, 41, 51, 255)
STEEL = (154, 165, 177, 255)
STEEL_D = (96, 108, 122, 255)
STEEL_L = (205, 213, 222, 255)
DARK = (59, 70, 82, 255)
WATER = (47, 143, 224, 255)
WATER_L = (143, 211, 255, 255)
GREEN = (46, 204, 113, 255)
GREEN_D = (30, 132, 73, 255)
RED = (231, 76, 60, 255)
RED_D = (150, 40, 27, 255)
ORANGE = (243, 156, 18, 255)
YELLOW = (241, 196, 15, 255)
YELLOW_L = (255, 236, 140, 255)
FUEL = (217, 142, 4, 255)
FUEL_L = (250, 200, 90, 255)
WHITE = (250, 252, 255, 255)
SMOKE = (120, 125, 130, 255)
SKY = (120, 190, 240, 255)


def rgba(color: tuple, alpha: float) -> tuple:
    """Return ``color`` with its alpha multiplied by ``alpha`` (0..1).

    Args:
        color: RGBA tuple.
        alpha: Opacity factor.

    Returns:
        New RGBA tuple.
    """
    return color[:3] + (max(0, min(255, int(color[3] * alpha))),)


def rot(points, cx: float, cy: float, deg: float) -> list:
    """Rotate points around a centre.

    Args:
        points: Iterable of ``(x, y)``.
        cx: Centre x.
        cy: Centre y.
        deg: Angle in degrees (clockwise on screen).

    Returns:
        List of rotated ``(x, y)`` tuples.
    """
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return [(cx + (x - cx) * c - (y - cy) * s, cy + (x - cx) * s + (y - cy) * c) for x, y in points]


class Pen:
    """Small drawing helper working in logical 0..size coordinates.

    Every coordinate and line width is multiplied by :data:`SS` before
    drawing on the supersampled RGBA image.
    """

    def __init__(self, size: int):
        """Create an empty transparent frame.

        Args:
            size: Logical frame size in pixels (the final GIF size).
        """
        self.size = size
        self.img = Image.new("RGBA", (size * SS, size * SS), (0, 0, 0, 0))
        self.d = ImageDraw.Draw(self.img)

    @staticmethod
    def _pts(points) -> list:
        """Scale a list of points to the supersampled image."""
        return [(x * SS, y * SS) for x, y in points]

    def poly(self, points, fill=None, outline=OUTLINE, width: float = 1.5) -> None:
        """Draw a filled polygon.

        Args:
            points: Corner points.
            fill: Fill colour or ``None``.
            outline: Outline colour or ``None``.
            width: Outline width.
        """
        pts = self._pts(points)
        self.d.polygon(pts, fill=fill)
        if outline is not None and width > 0:
            self.d.line(pts + [pts[0]], fill=outline, width=max(1, round(width * SS)), joint="curve")

    def circle(self, cx, cy, r, fill=None, outline=OUTLINE, width: float = 1.5) -> None:
        """Draw a circle.

        Args:
            cx: Centre x.
            cy: Centre y.
            r: Radius.
            fill: Fill colour or ``None``.
            outline: Outline colour or ``None``.
            width: Outline width.
        """
        box = [(cx - r) * SS, (cy - r) * SS, (cx + r) * SS, (cy + r) * SS]
        w = max(1, round(width * SS)) if outline is not None and width > 0 else 0
        self.d.ellipse(box, fill=fill, outline=outline if w else None, width=w)

    def ellipse(self, x0, y0, x1, y1, fill=None, outline=OUTLINE, width: float = 1.5) -> None:
        """Draw an ellipse inside a bounding box.

        Args:
            x0: Left.
            y0: Top.
            x1: Right.
            y1: Bottom.
            fill: Fill colour or ``None``.
            outline: Outline colour or ``None``.
            width: Outline width.
        """
        w = max(1, round(width * SS)) if outline is not None and width > 0 else 0
        self.d.ellipse([x0 * SS, y0 * SS, x1 * SS, y1 * SS], fill=fill, outline=outline if w else None, width=w)

    def rect(self, x0, y0, x1, y1, fill=None, outline=OUTLINE, width: float = 1.5, radius: float = 0) -> None:
        """Draw a (rounded) rectangle.

        Args:
            x0: Left.
            y0: Top.
            x1: Right.
            y1: Bottom.
            fill: Fill colour or ``None``.
            outline: Outline colour or ``None``.
            width: Outline width.
            radius: Corner radius.
        """
        w = max(1, round(width * SS)) if outline is not None and width > 0 else 0
        self.d.rounded_rectangle([x0 * SS, y0 * SS, x1 * SS, y1 * SS], radius=radius * SS, fill=fill,
                                 outline=outline if w else None, width=w)

    def line(self, points, fill=OUTLINE, width: float = 1.5) -> None:
        """Draw a poly-line with round joints.

        Args:
            points: Points of the line.
            fill: Line colour.
            width: Line width.
        """
        self.d.line(self._pts(points), fill=fill, width=max(1, round(width * SS)), joint="curve")
        r = width * SS / 2
        for x, y in self._pts([points[0], points[-1]]):
            self.d.ellipse([x - r, y - r, x + r, y + r], fill=fill)

    def arc(self, cx, cy, r, start, end, fill=OUTLINE, width: float = 1.5) -> None:
        """Draw a circular arc (angles in degrees, 0 = east, clockwise).

        Args:
            cx: Centre x.
            cy: Centre y.
            r: Radius.
            start: Start angle.
            end: End angle.
            fill: Colour.
            width: Line width.
        """
        self.d.arc([(cx - r) * SS, (cy - r) * SS, (cx + r) * SS, (cy + r) * SS], start, end,
                   fill=fill, width=max(1, round(width * SS)))

    def pie(self, cx, cy, r, start, end, fill, outline=None, width: float = 0) -> None:
        """Draw a pie slice (angles in degrees, 0 = east, clockwise).

        Args:
            cx: Centre x.
            cy: Centre y.
            r: Radius.
            start: Start angle.
            end: End angle.
            fill: Fill colour.
            outline: Outline colour or ``None``.
            width: Outline width.
        """
        w = max(1, round(width * SS)) if outline is not None and width > 0 else 0
        self.d.pieslice([(cx - r) * SS, (cy - r) * SS, (cx + r) * SS, (cy + r) * SS], start, end, fill=fill,
                        outline=outline if w else None, width=w)

    def frame(self) -> Image.Image:
        """Return the finished frame reduced to its logical size."""
        return self.img.resize((self.size, self.size), Image.LANCZOS)


# ---------------------------------------------------------------------------
# Shared building blocks (all drawn on a 128 x 128 logical grid and scaled)
# ---------------------------------------------------------------------------
def blade_fan(p: Pen, cx, cy, r, angle, blades=3, color=STEEL_L) -> None:
    """Draw a propeller-style fan rotor.

    Args:
        p: Pen.
        cx: Centre x.
        cy: Centre y.
        r: Rotor radius.
        angle: Rotation in degrees.
        blades: Number of blades.
        color: Blade colour.
    """
    hub = r * 0.22
    shape = [(cx + hub * 0.6, cy - hub * 0.8), (cx + r * 0.55, cy - r * 0.42), (cx + r * 0.95, cy - r * 0.22),
             (cx + r * 0.98, cy + r * 0.05), (cx + r * 0.7, cy + r * 0.2), (cx + hub * 0.9, cy + hub * 0.5)]
    for i in range(blades):
        p.poly(rot(shape, cx, cy, angle + i * 360 / blades), fill=color, width=1.4)
    p.circle(cx, cy, hub, fill=DARK, width=1.4)
    p.circle(cx, cy, hub * 0.35, fill=STEEL_L, outline=None)


def flow_dashes(p: Pen, x0, x1, y, t, color, length=8, gap=8, width=3.0) -> None:
    """Draw dashes moving from ``x0`` towards ``x1`` (loop-safe).

    Args:
        p: Pen.
        x0: Start x.
        x1: End x.
        y: Line y.
        t: Animation phase 0..1.
        color: Dash colour.
        length: Dash length.
        gap: Gap between dashes.
        width: Dash thickness.
    """
    step = length + gap
    x = x0 - step + t * step
    while x < x1:
        a, b = max(x, x0), min(x + length, x1)
        if b > a:
            p.line([(a, y), (b, y)], fill=color, width=width)
        x += step


def chevrons(p: Pen, x0, x1, y, t, color, size=7, spacing=20) -> None:
    """Draw ``>`` chevrons moving to the right between ``x0`` and ``x1``.

    Args:
        p: Pen.
        x0: Start x.
        x1: End x.
        y: Centre line.
        t: Phase 0..1.
        color: Colour.
        size: Chevron half-height.
        spacing: Distance between chevrons.
    """
    x = x0 - spacing + t * spacing
    while x < x1 + spacing:
        if x0 <= x - size and x <= x1:
            p.line([(x - size, y - size), (x, y), (x - size, y + size)], fill=color, width=3)
        x += spacing


def puff(p: Pen, cx, cy, r, alpha, color=SMOKE) -> None:
    """Draw a soft smoke/steam puff made of three circles.

    Args:
        p: Pen.
        cx: Centre x.
        cy: Centre y.
        r: Size.
        alpha: Opacity 0..1.
        color: Base colour.
    """
    if alpha <= 0.02 or r <= 0:
        return
    c = rgba(color, alpha)
    for dx, dy, k in ((-0.5, 0.15, 0.7), (0.5, 0.1, 0.75), (0, -0.25, 0.85)):
        p.circle(cx + dx * r, cy + dy * r, r * k, fill=c, outline=None)


def wave(p: Pen, x0, x1, y, amp, phase, color, width=2.5, periods=1.5) -> None:
    """Draw a horizontal sine wave.

    Args:
        p: Pen.
        x0: Start x.
        x1: End x.
        y: Centre line.
        amp: Amplitude.
        phase: Phase in turns (0..1 moves one period).
        color: Colour.
        width: Line width.
        periods: Periods between ``x0`` and ``x1``.
    """
    pts = []
    n = 60
    for i in range(n + 1):
        x = x0 + (x1 - x0) * i / n
        pts.append((x, y + amp * math.sin(2 * math.pi * (periods * i / n - phase))))
    p.line(pts, fill=color, width=width)


def tank_body(p: Pen, x0, y0, x1, y1, level, liquid, light, t, outline=OUTLINE) -> None:
    """Draw a vertical tank with a liquid level and a moving surface.

    Args:
        p: Pen.
        x0: Left.
        y0: Top.
        x1: Right.
        y1: Bottom.
        level: Fill level 0..1.
        liquid: Liquid colour.
        light: Surface highlight colour.
        t: Phase for the surface ripple.
        outline: Outline colour.
    """
    p.rect(x0, y0, x1, y1, fill=STEEL_L, outline=None, radius=8)
    top = y1 - (y1 - y0 - 6) * level - 3
    pts = [(x0 + 2, y1 - 3)]
    n = 24
    for i in range(n + 1):
        x = x0 + 2 + (x1 - x0 - 4) * i / n
        pts.append((x, top + 1.4 * math.sin(2 * math.pi * (2 * i / n - t))))
    pts.append((x1 - 2, y1 - 3))
    p.poly(pts, fill=liquid, outline=None)
    p.line(pts[1:-1], fill=light, width=1.6)
    p.rect(x0, y0, x1, y1, fill=None, outline=outline, width=2.2, radius=8)
    for i in range(1, 4):
        y = y1 - (y1 - y0) * i / 4
        p.line([(x1 - 7, y), (x1 - 2, y)], fill=outline, width=1.2)


# ---------------------------------------------------------------------------
# HVAC
# ---------------------------------------------------------------------------
def hvac_fan(p: Pen, t: float) -> None:
    """Axial fan in a round housing, rotating."""
    p.circle(64, 64, 56, fill=DARK, width=2.5)
    p.circle(64, 64, 49, fill=(40, 48, 58, 255), outline=STEEL, width=1.5)
    blade_fan(p, 64, 64, 46, t * 120)
    for a in (45, 135, 225, 315):
        x, y = rot([(64 + 53, 64)], 64, 64, a)[0]
        p.circle(x, y, 2.4, fill=STEEL_L, width=1)


def hvac_blower(p: Pen, t: float) -> None:
    """Centrifugal blower: scroll housing with a rotating impeller."""
    pts = []
    for i in range(0, 361, 10):
        r = 38 + 16 * i / 360
        a = math.radians(i - 90)
        pts.append((60 + r * math.cos(a), 68 + r * math.sin(a)))
    pts += [(60 + 54, 68 - 30), (60 + 54, 68 - 54), (60, 68 - 54)]
    p.poly(pts, fill=STEEL, width=2.2)
    p.rect(100, 8, 122, 36, fill=STEEL_D, width=2)
    for k in range(3):
        y = 32 - ((t + k / 3) % 1) * 24
        p.line([(106, y + 5), (111, y), (116, y + 5)], fill=WHITE, width=2.2)
    p.circle(60, 68, 33, fill=DARK, width=1.8)
    for i in range(12):
        a = t * 30 + i * 30
        p.line(rot([(60 + 14, 68), (60 + 22, 68 - 7), (60 + 30, 68 - 9)], 60, 68, a), fill=STEEL_L, width=2.6)
    p.circle(60, 68, 13, fill=STEEL_D, width=1.6)
    p.circle(60, 68, 5, fill=STEEL_L, width=1.2)


def hvac_cooling_tower(p: Pen, t: float) -> None:
    """Hyperbolic cooling tower with rising steam."""
    for k in range(4):
        ph = (t + k / 4) % 1
        puff(p, 64 + 10 * math.sin(ph * 5 + k), 40 - ph * 36, 10 + 12 * ph, (1 - ph) * 0.9, WHITE)
    left = [(30, 122), (40, 80), (36, 44)]
    right = [(92, 44), (88, 80), (98, 122)]
    p.poly(left + right, fill=STEEL_L, width=2.4)
    for y in (60, 78, 96, 112):
        w = 26 + (abs(y - 80) / 42) ** 2 * 18
        p.line([(64 - w, y), (64 + w, y)], fill=STEEL, width=1.2)
    p.ellipse(36, 40, 92, 50, fill=DARK, width=2)
    for i in range(5):
        x = 38 + i * 13 + 4
        p.line([(x, 114), (x, 122)], fill=WATER, width=2)


def hvac_chiller(p: Pen, t: float) -> None:
    """Chiller unit with a rotating snowflake and cold air."""
    p.rect(10, 22, 118, 112, fill=STEEL_L, width=2.4, radius=6)
    p.rect(10, 22, 118, 36, fill=STEEL, width=2.4, radius=6)
    for x in (22, 38, 54):
        p.circle(x + 50, 29, 4, fill=DARK, width=1)
    p.circle(48, 72, 30, fill=(214, 236, 252, 255), outline=WATER, width=2)
    a0 = t * 60
    for i in range(6):
        a = a0 + i * 60
        arm = rot([(48, 72), (48 + 24, 72)], 48, 72, a)
        p.line(arm, fill=WATER, width=3)
        for d in (11, 17):
            base = rot([(48 + d, 72)], 48, 72, a)[0]
            for s in (-1, 1):
                tip = rot([(48 + d + 6, 72 + 6 * s)], 48, 72, a)[0]
                p.line([base, tip], fill=WATER, width=2.2)
    p.circle(48, 72, 4, fill=WATER, outline=None)
    for k in range(3):
        y = 54 + k * 18
        for j in range(2):
            ph = (t + j / 2 + k * 0.17) % 1
            x = 86 + ph * 26
            p.line([(x, y), (x + 8, y)], fill=rgba(WATER, 1 - ph), width=2.4)


def hvac_damper(p: Pen, t: float) -> None:
    """Motorised air damper, blades opening and closing."""
    ang = 80 * (0.5 - 0.5 * math.cos(2 * math.pi * t))
    p.rect(14, 14, 114, 114, fill=(40, 48, 58, 255), width=2.6, radius=3)
    p.rect(20, 20, 108, 108, fill=None, outline=STEEL, width=1.5)
    for i in range(4):
        cy = 31 + i * 22
        blade = [(22, cy - 3), (106, cy - 3), (106, cy + 3), (22, cy + 3)]
        h = 10 * math.cos(math.radians(ang)) + 3 * math.sin(math.radians(ang))
        blade = [(22, cy - h), (106, cy - h), (106, cy + h), (22, cy + h)]
        p.poly(blade, fill=STEEL_L, width=1.4)
        p.circle(64, cy, 2.4, fill=DARK, outline=None)
    p.rect(108, 54, 124, 74, fill=ORANGE, width=1.8, radius=2)
    if ang > 40:
        for i in range(4):
            cy = 42 + i * 22 - 11
            ph = t * 2 % 1
            p.line([(30 + ph * 60, cy), (40 + ph * 60, cy)], fill=rgba(WHITE, (ang - 40) / 40), width=2)


def hvac_airflow(p: Pen, t: float) -> None:
    """Rectangular air duct with air moving through it."""
    p.poly([(6, 38), (106, 38), (122, 26), (122, 102), (106, 90), (6, 90)], fill=STEEL_L, width=2.4)
    p.line([(106, 38), (106, 90)], fill=STEEL, width=1.6)
    for y in (52, 64, 76):
        chevrons(p, 12, 100, y, t, SKY, size=6, spacing=22)


def hvac_heater(p: Pen, t: float) -> None:
    """Radiator / heating coil with rising heat waves."""
    for i in range(3):
        x = 36 + i * 28
        pts = []
        for k in range(25):
            y = 64 - k * 2.4
            amp = 4
            pts.append((x + amp * math.sin(2 * math.pi * (k / 10 + t)), y))
        alpha = 1.0
        p.line(pts, fill=rgba(ORANGE, alpha), width=3)
    for i in range(6):
        x = 18 + i * 16
        p.rect(x, 66, x + 12, 118, fill=RED if i % 2 == 0 else (214, 64, 50, 255), width=1.8, radius=5)
    p.rect(12, 74, 116, 80, fill=RED_D, width=1.6)
    p.rect(12, 106, 116, 112, fill=RED_D, width=1.6)


def hvac_thermometer(p: Pen, t: float) -> None:
    """Thermometer with the temperature rising and falling."""
    level = 0.5 - 0.42 * math.cos(2 * math.pi * t)
    p.rect(52, 8, 76, 98, fill=WHITE, width=2.2, radius=12)
    p.circle(64, 104, 18, fill=WHITE, width=2.2)
    col = (int(60 + 180 * level), int(140 - 80 * level), int(220 - 170 * level), 255)
    top = 92 - 78 * level
    p.rect(59, top, 69, 104, fill=col, outline=None, radius=5)
    p.circle(64, 104, 13, fill=col, outline=None)
    for i in range(8):
        y = 22 + i * 10
        p.line([(76, y), (84 if i % 2 == 0 else 81, y)], fill=OUTLINE, width=1.5)


# ---------------------------------------------------------------------------
# Electrical
# ---------------------------------------------------------------------------
BOLT = [(72, 6), (34, 70), (60, 70), (48, 122), (96, 50), (68, 50), (84, 6)]


def el_lightning(p: Pen, t: float) -> None:
    """Flashing lightning bolt (high voltage)."""
    on = (t < 0.15) or (0.25 <= t < 0.35)
    glow = 1.0 if on else 0.25
    if on:
        p.circle(64, 64, 58, fill=rgba(YELLOW_L, 0.35), outline=None)
    p.poly(BOLT, fill=YELLOW if on else (200, 160, 20, 255), width=2.4)
    if on:
        p.poly([(70, 18), (46, 62), (58, 62)], fill=rgba(WHITE, glow), outline=None)


def el_power_flow(p: Pen, t: float) -> None:
    """Power cable with energy flowing between two terminals."""
    p.rect(4, 48, 28, 80, fill=DARK, width=2, radius=4)
    p.rect(100, 48, 124, 80, fill=DARK, width=2, radius=4)
    p.rect(28, 58, 100, 70, fill=(40, 40, 40, 255), width=2, radius=6)
    for k in range(4):
        x = 28 + ((t + k / 4) % 1) * 72
        p.circle(x, 64, 4.5, fill=YELLOW, outline=ORANGE, width=1)
    p.poly([(12, 52), (8, 64), (14, 64), (10, 76), (20, 60), (14, 60), (18, 52)], fill=YELLOW, width=1)
    p.circle(112, 64, 8, fill=GREEN if t % 0.5 < 0.25 else GREEN_D, width=1.4)


def el_transformer(p: Pen, t: float) -> None:
    """Transformer symbol (two coils) with pulsing magnetic field."""
    for k in range(2):
        ph = (t + k / 2) % 1
        p.circle(64, 64, 30 + ph * 32, fill=None, outline=rgba(YELLOW, 1 - ph), width=2.2)
    p.circle(48, 64, 26, fill=rgba(STEEL_L, 0.9), outline=RED, width=4)
    p.circle(80, 64, 26, fill=None, outline=WATER, width=4)
    p.line([(4, 64), (22, 64)], fill=RED, width=3.5)
    p.line([(106, 64), (124, 64)], fill=WATER, width=3.5)


def el_breaker(p: Pen, t: float) -> None:
    """Circuit breaker switching on and off."""
    on = t < 0.5
    p.rect(30, 6, 98, 122, fill=STEEL_L, width=2.4, radius=6)
    p.rect(40, 40, 88, 88, fill=DARK, width=1.8, radius=4)
    y = 50 if on else 66
    p.rect(52, y, 76, y + 14, fill=GREEN if on else RED, width=1.8, radius=3)
    p.rect(44, 14, 84, 30, fill=WHITE, width=1.4, radius=2)
    p.line([(50, 22), (60, 22)], fill=OUTLINE, width=1.4)
    p.line([(66, 22), (78, 22)], fill=OUTLINE, width=1.4)
    p.circle(64, 106, 7, fill=GREEN if on else RED, width=1.4)
    for x in (44, 84):
        p.circle(x, 98, 3, fill=STEEL_D, width=1)


def _battery(p: Pen, bars: float, color, blink_last=False, t=0.0) -> None:
    """Draw a horizontal battery with ``bars`` (0..4) charged segments."""
    p.rect(8, 34, 112, 94, fill=WHITE, width=3, radius=8)
    p.rect(112, 52, 122, 76, fill=DARK, width=2, radius=3)
    for i in range(4):
        x = 15 + i * 24.5
        if i < int(bars) or (i == int(bars) and blink_last and t % 0.25 < 0.125):
            p.rect(x, 41, x + 20, 87, fill=color, outline=None, radius=3)


def el_battery_charge(p: Pen, t: float) -> None:
    """Battery charging (segments fill up)."""
    bars = min(4, int(t * 5))
    _battery(p, bars, GREEN)
    p.poly([(64, 46), (52, 66), (62, 66), (58, 82), (74, 60), (64, 60), (68, 46)], fill=YELLOW, width=1.8)


def el_battery_discharge(p: Pen, t: float) -> None:
    """Battery discharging (segments empty, low level blinks red)."""
    bars = 4 - min(4, int(t * 5))
    color = GREEN if bars >= 3 else (ORANGE if bars == 2 else RED)
    if bars == 0:
        _battery(p, 0, RED, blink_last=True, t=t)
    else:
        _battery(p, bars, color)


def el_motor(p: Pen, t: float) -> None:
    """Electric motor (front view) with the rotor turning."""
    p.rect(26, 104, 102, 116, fill=DARK, width=2, radius=2)
    p.circle(64, 62, 50, fill=STEEL, width=2.4)
    for i in range(16):
        a = i * 22.5
        p.line(rot([(64 + 44, 62), (64 + 50, 62)], 64, 62, a), fill=STEEL_D, width=2)
    p.circle(64, 62, 36, fill=DARK, width=2)
    for i in range(8):
        a = t * 90 + i * 45
        p.poly(rot([(64 + 10, 62 - 4), (64 + 32, 62 - 6), (64 + 32, 62 + 6), (64 + 10, 62 + 4)], 64, 62, a),
               fill=(184, 115, 51, 255), outline=OUTLINE, width=1)
    p.circle(64, 62, 10, fill=STEEL_L, width=1.6)
    k = rot([(64, 62 - 9)], 64, 62, t * 90)[0]
    p.circle(k[0], k[1], 2, fill=OUTLINE, outline=None)
    p.circle(64, 62, 4, fill=DARK, outline=None)


def el_solar(p: Pen, t: float) -> None:
    """Solar panel in the sun producing power."""
    a = t * 45
    p.circle(28, 26, 11, fill=YELLOW, outline=ORANGE, width=1.6)
    for i in range(8):
        ln = 6 + 3 * math.sin(2 * math.pi * t * 2 + i)
        p.line(rot([(28 + 15, 26), (28 + 15 + ln, 26)], 28, 26, a + i * 45), fill=ORANGE, width=2)
    panel = [(30, 64), (118, 56), (106, 108), (14, 116)]
    p.poly(panel, fill=(30, 70, 140, 255), width=2.4)
    for i in range(1, 4):
        f = i / 4
        p.line([(30 + (118 - 30) * f, 64 + (56 - 64) * f), (14 + (106 - 14) * f, 116 + (108 - 116) * f)],
               fill=SKY, width=1.2)
    for i in range(1, 3):
        f = i / 3
        p.line([(30 + (14 - 30) * f, 64 + (116 - 64) * f), (118 + (106 - 118) * f, 56 + (108 - 56) * f)],
               fill=SKY, width=1.2)
    p.line([(64, 112), (64, 124)], fill=DARK, width=3)
    for k in range(3):
        ph = (t + k / 3) % 1
        x, y = 50 + ph * 10, 40 + ph * 14
        p.line([(x, y), (x + 5, y + 7)], fill=rgba(YELLOW, 1 - ph * 0.6), width=2.4)


def el_wind_turbine(p: Pen, t: float) -> None:
    """Wind turbine rotating."""
    p.poly([(61, 44), (67, 44), (72, 124), (56, 124)], fill=WHITE, width=2)
    cx, cy = 64, 40
    for i in range(3):
        blade = [(cx - 3, cy), (cx - 5, cy - 18), (cx - 1, cy - 38), (cx + 3, cy - 36), (cx + 4, cy - 8), (cx + 3, cy)]
        p.poly(rot(blade, cx, cy, t * 120 + i * 120), fill=WHITE, width=1.6)
    p.circle(cx, cy, 5, fill=STEEL, width=1.6)
    for k in range(3):
        ph = (t + k / 3) % 1
        y = 70 + k * 14
        p.line([(4 + ph * 30, y), (18 + ph * 30, y)], fill=rgba(SKY, 1 - ph), width=2.4)


def el_meter(p: Pen, t: float) -> None:
    """Energy (kWh) meter with the disc turning and a counter."""
    p.rect(20, 8, 108, 120, fill=STEEL_L, width=2.4, radius=8)
    p.rect(30, 18, 98, 40, fill=DARK, width=1.6, radius=2)
    digits = f"{int(4810 + t * 10):05d}"
    for i, ch in enumerate(digits):
        x = 34 + i * 12.6
        p.rect(x, 22, x + 10, 36, fill=WHITE if i < 4 else RED, outline=None, radius=1)
        _seven_seg(p, x + 2, 24, ch, OUTLINE)
    p.ellipse(30, 56, 98, 76, fill=(230, 230, 230, 255), width=1.6)
    mx = 64 + 30 * math.sin(2 * math.pi * t * 2)
    p.rect(mx - 3, 63, mx + 3, 69, fill=RED, outline=None)
    p.rect(40, 92, 88, 104, fill=DARK, width=1.4, radius=2)
    p.circle(100, 98, 3.5, fill=RED if t % 0.25 < 0.12 else RED_D, outline=None)


_SEG = {"0": "abcdef", "1": "bc", "2": "abged", "3": "abgcd", "4": "fgbc", "5": "afgcd", "6": "afgedc",
        "7": "abc", "8": "abcdefg", "9": "abcfgd"}


def _seven_seg(p: Pen, x, y, ch, color) -> None:
    """Draw a tiny seven-segment digit (6 x 10 logical pixels)."""
    w, h = 6, 10
    segs = {"a": [(x, y), (x + w, y)], "b": [(x + w, y), (x + w, y + h / 2)], "c": [(x + w, y + h / 2), (x + w, y + h)],
            "d": [(x, y + h), (x + w, y + h)], "e": [(x, y + h / 2), (x, y + h)], "f": [(x, y), (x, y + h / 2)],
            "g": [(x, y + h / 2), (x + w, y + h / 2)]}
    for s in _SEG.get(ch, ""):
        p.line(segs[s], fill=color, width=1.2)


# ---------------------------------------------------------------------------
# Water
# ---------------------------------------------------------------------------
def water_pump(p: Pen, t: float) -> None:
    """Centrifugal pump: volute with the impeller turning."""
    p.rect(74, 6, 100, 30, fill=STEEL, width=2.2)
    p.rect(70, 4, 104, 10, fill=STEEL_D, width=1.6)
    pts = []
    for i in range(0, 361, 10):
        r = 34 + 12 * i / 360
        a = math.radians(i - 90)
        pts.append((60 + r * math.cos(a), 70 + r * math.sin(a)))
    pts += [(100, 70 - 46 + 10), (100, 26), (74, 26)]
    p.poly(pts, fill=STEEL, width=2.4)
    p.rect(30, 112, 98, 122, fill=DARK, width=2, radius=2)
    p.circle(60, 70, 30, fill=WATER, width=1.8)
    for i in range(6):
        a = t * 60 + i * 60
        vane = []
        for k in range(8):
            r = 8 + k * 2.8
            vane.append(rot([(60 + r, 70)], 60, 70, a - k * 7)[0])
        p.line(vane, fill=WHITE, width=3)
    p.circle(60, 70, 8, fill=STEEL_L, width=1.6)
    p.circle(60, 70, 3, fill=DARK, outline=None)


def water_pipe_flow(p: Pen, t: float) -> None:
    """Horizontal water pipe with flowing water."""
    p.rect(2, 44, 126, 84, fill=STEEL, width=2.4, radius=3)
    p.rect(6, 50, 122, 78, fill=WATER, outline=None)
    for row, (y, off) in enumerate(((56, 0.0), (64, 0.33), (72, 0.66))):
        flow_dashes(p, 6, 122, y, (t + off) % 1, WATER_L, length=10, gap=14, width=2.4)
    for x in (20, 108):
        p.rect(x - 4, 40, x + 4, 88, fill=STEEL_D, width=1.8, radius=1)


def water_valve(p: Pen, t: float) -> None:
    """Gate valve with handwheel turning and water flowing through it."""
    p.rect(2, 72, 40, 92, fill=WATER, width=2)
    p.rect(88, 72, 126, 92, fill=WATER, width=2)
    flow_dashes(p, 4, 38, 82, t, WATER_L, length=6, gap=6, width=2.2)
    flow_dashes(p, 90, 124, 82, t, WATER_L, length=6, gap=6, width=2.2)
    p.poly([(40, 62), (64, 82), (40, 102)], fill=STEEL_L, width=2.4)
    p.poly([(88, 62), (64, 82), (88, 102)], fill=STEEL_L, width=2.4)
    p.line([(64, 82), (64, 30)], fill=DARK, width=4)
    p.ellipse(34, 16, 94, 34, fill=None, outline=RED, width=4)
    for i in range(4):
        a = t * 180 + i * 45
        x = 64 + 28 * math.cos(math.radians(a))
        p.line([(64, 25), (x, 25)], fill=RED_D, width=2.2)
    p.circle(64, 25, 4, fill=DARK, outline=None)


def water_tank_fill(p: Pen, t: float) -> None:
    """Water tank filling up from an inlet pipe."""
    level = 0.1 + 0.8 * t
    p.rect(4, 6, 60, 16, fill=STEEL, width=2)
    p.rect(50, 6, 60, 26, fill=STEEL, width=2)
    top = 120 - (120 - 26 - 6) * level - 3
    p.rect(52, 26, 58, top + 2, fill=WATER, outline=None)
    tank_body(p, 24, 26, 104, 122, level, WATER, WATER_L, t)
    p.rect(52, 26, 58, top + 2, fill=WATER_L, outline=None)


def water_tank_drain(p: Pen, t: float) -> None:
    """Water tank draining through an outlet pipe."""
    level = 0.9 - 0.8 * t
    tank_body(p, 24, 10, 104, 106, level, WATER, WATER_L, t)
    p.rect(84, 106, 124, 116, fill=STEEL, width=2)
    for k in range(3):
        ph = (t * 3 + k / 3) % 1
        p.circle(118, 116 + ph * 10, 2.5, fill=rgba(WATER, 1 - ph), outline=None)


def water_drop(p: Pen, t: float) -> None:
    """Water drop falling into water and making ripples."""
    fall = min(1.0, t / 0.55)
    y = 10 + fall * 70
    if t < 0.55:
        pts = []
        for i in range(31):
            a = 2 * math.pi * i / 30
            r = 12 * (1 - 0.6 * max(0.0, -math.cos(a)) ** 3)
            sy = 1.3 * math.cos(a)
            pts.append((64 + r * math.sin(a), y - 12 * sy + 4))
        p.poly(pts, fill=WATER, width=2)
        p.ellipse(56, y - 4, 62, y + 4, fill=WATER_L, outline=None)
    else:
        ph = (t - 0.55) / 0.45
        for k in range(3):
            r = (ph - k * 0.2) * 52
            if r > 0:
                p.ellipse(64 - r, 96 - r * 0.25, 64 + r, 96 + r * 0.25, fill=None,
                          outline=rgba(WATER, 1 - min(1, r / 52)), width=2.4)
        if ph < 0.4:
            for s in (-1, 1):
                p.circle(64 + s * (8 + ph * 20), 90 - ph * 40 + ph * ph * 60, 3, fill=WATER, outline=None)
    p.ellipse(10, 90, 118, 102, fill=None, outline=rgba(WATER, 0.6), width=1.6)


def water_sprinkler(p: Pen, t: float) -> None:
    """Rotating lawn/fire sprinkler spraying water."""
    p.rect(58, 84, 70, 122, fill=STEEL, width=2)
    p.rect(46, 116, 82, 124, fill=DARK, width=1.6, radius=2)
    p.rect(54, 76, 74, 88, fill=STEEL_L, width=2, radius=3)
    swing = math.sin(2 * math.pi * t)
    for k in range(10):
        ph = (k / 10 + t * 2) % 1
        for side in (-1, 1):
            vx = side * (28 + 18 * swing * side) * 1.6
            x = 64 + vx * ph
            y = 78 - 70 * ph + 80 * ph * ph
            p.circle(x, y, 2.4 * (1 - ph * 0.5), fill=rgba(WATER, 1 - ph * 0.5), outline=None)


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------
def gen_genset(p: Pen, t: float) -> None:
    """Diesel generator set running: exhaust smoke and status lamp."""
    shake = 0.6 * math.sin(2 * math.pi * t * 6)
    for k in range(3):
        ph = (t + k / 3) % 1
        puff(p, 34 + ph * 14, 22 - ph * 18, 5 + ph * 10, (1 - ph) * 0.8)
    p.rect(30, 26 + shake, 38, 52 + shake, fill=DARK, width=1.8)
    p.rect(6, 108, 122, 118, fill=DARK, width=2, radius=1)
    p.rect(10, 50 + shake, 74, 108 + shake, fill=(230, 180, 30, 255), width=2.4, radius=4)
    for i in range(4):
        y = 60 + i * 11 + shake
        p.line([(18, y), (40, y)], fill=OUTLINE, width=2)
    p.rect(74, 58 + shake, 118, 104 + shake, fill=STEEL, width=2.4, radius=10)
    p.rect(48, 60 + shake, 68, 76 + shake, fill=DARK, width=1.4, radius=2)
    p.circle(58, 92 + shake, 5, fill=GREEN if t % 0.5 < 0.25 else GREEN_D, width=1.4)
    p.line([(84, 70 + shake), (108, 70 + shake)], fill=STEEL_D, width=2)
    p.line([(84, 92 + shake), (108, 92 + shake)], fill=STEEL_D, width=2)


def gen_alternator(p: Pen, t: float) -> None:
    """Alternator: rotor poles turning inside the stator windings."""
    p.circle(64, 64, 56, fill=STEEL, width=2.4)
    p.circle(64, 64, 44, fill=DARK, width=2)
    for i in range(6):
        a = i * 60
        c = rot([(64 + 49, 64)], 64, 64, a)[0]
        p.circle(c[0], c[1], 6, fill=(184, 115, 51, 255), width=1.4)
    a = t * 360
    p.pie(64, 64, 36, a, a + 180, fill=RED)
    p.pie(64, 64, 36, a + 180, a + 360, fill=WATER)
    p.circle(64, 64, 36, fill=None, outline=OUTLINE, width=1.8)
    n = rot([(64 + 22, 64)], 64, 64, a + 90)[0]
    s = rot([(64 - 22, 64)], 64, 64, a + 90)[0]
    _letter(p, n, "N")
    _letter(p, s, "S")
    p.circle(64, 64, 6, fill=STEEL_L, width=1.4)


def _letter(p: Pen, pos, ch) -> None:
    """Draw a simple stroked N or S (pole marking) at ``pos``."""
    x, y = pos
    if ch == "N":
        p.line([(x - 4, y + 6), (x - 4, y - 6), (x + 4, y + 6), (x + 4, y - 6)], fill=WHITE, width=2)
    else:
        p.line([(x + 4, y - 5), (x - 1, y - 6), (x - 4, y - 3), (x, y), (x + 4, y + 3), (x + 1, y + 6), (x - 4, y + 5)],
               fill=WHITE, width=2)


def gen_ac_output(p: Pen, t: float) -> None:
    """Generator symbol (G in a circle) with AC output wave."""
    p.circle(36, 64, 28, fill=WHITE, width=3)
    p.arc(36, 64, 13, 30, 330, fill=OUTLINE, width=3)
    p.line([(49, 70), (49, 64), (40, 64)], fill=OUTLINE, width=3)
    p.rect(64, 34, 124, 94, fill=DARK, width=2, radius=6)
    p.line([(68, 64), (120, 64)], fill=STEEL_D, width=1)
    wave(p, 68, 120, 64, 18, t, GREEN, width=2.6, periods=1.5)
    p.line([(64, 64), (60, 64)], fill=OUTLINE, width=2.4)


# ---------------------------------------------------------------------------
# Fuel
# ---------------------------------------------------------------------------
def fuel_tank(p: Pen, t: float) -> None:
    """Fuel tank with a level gauge (level going down and refilled)."""
    level = 0.85 - 0.65 * (0.5 - 0.5 * math.cos(2 * math.pi * t))
    tank_body(p, 18, 12, 86, 118, level, FUEL, FUEL_L, t)
    p.rect(96, 12, 112, 118, fill=WHITE, width=2, radius=6)
    top = 115 - 100 * level
    p.rect(99, top, 109, 115, fill=GREEN if level > 0.4 else (ORANGE if level > 0.25 else RED), outline=None,
           radius=3)


def fuel_dispenser(p: Pen, t: float) -> None:
    """Fuel pump (dispenser) with fuel dripping from the nozzle."""
    p.rect(14, 12, 68, 118, fill=RED, width=2.4, radius=6)
    p.rect(22, 22, 60, 50, fill=DARK, width=1.6, radius=2)
    digits = f"{int(t * 100):03d}"
    for i, ch in enumerate(digits):
        _seven_seg(p, 28 + i * 10, 31, ch, YELLOW)
    p.rect(8, 112, 74, 122, fill=DARK, width=2, radius=1)
    p.line([(68, 60), (88, 60), (96, 70), (96, 86)], fill=OUTLINE, width=4)
    p.poly([(88, 84), (110, 84), (112, 90), (98, 90), (98, 98), (88, 98)], fill=STEEL_D, width=2)
    p.line([(110, 90), (114, 96)], fill=OUTLINE, width=3)
    for k in range(3):
        ph = (t * 3 + k / 3) % 1
        y = 100 + ph * 24
        p.ellipse(113, y - 3, 118, y + 3, fill=rgba(FUEL, 1 - ph * 0.7), outline=None)


def fuel_flame(p: Pen, t: float) -> None:
    """Burner with a flickering flame."""
    p.rect(24, 104, 104, 120, fill=STEEL, width=2.2, radius=3)
    p.rect(34, 96, 94, 104, fill=DARK, width=1.8)

    def tongue(scale, color, phase_shift):
        """Draw one flame layer (teardrop with a swaying tip)."""
        h = 80 * scale * (1 + 0.1 * math.sin(2 * math.pi * (t * 2 + phase_shift)))
        w = 30 * scale
        pts = []
        for i in range(49):
            a = 2 * math.pi * i / 48
            up = (math.cos(a) + 1) / 2  # 1 at the tip, 0 at the bottom
            x = w * math.sin(a) * abs(math.sin(a / 2)) ** 1.5
            sway = 8 * scale * math.sin(2 * math.pi * (t + phase_shift) + up * 2) * up ** 2
            pts.append((64 + x + sway, 96 - up * h))
        p.poly(pts, fill=color, outline=None)

    tongue(1.0, ORANGE, 0.0)
    tongue(0.72, YELLOW, 0.3)
    tongue(0.42, WHITE, 0.6)
    tongue(0.25, (100, 170, 255, 255), 0.1)


def fuel_pipe_flow(p: Pen, t: float) -> None:
    """Fuel line with fuel flowing (amber)."""
    p.rect(2, 44, 126, 84, fill=STEEL, width=2.4, radius=3)
    p.rect(6, 50, 122, 78, fill=FUEL, outline=None)
    for y, off in ((56, 0.0), (64, 0.33), (72, 0.66)):
        flow_dashes(p, 6, 122, y, (t + off) % 1, FUEL_L, length=10, gap=14, width=2.4)
    for x in (20, 108):
        p.rect(x - 4, 40, x + 4, 88, fill=STEEL_D, width=1.8, radius=1)


def fuel_gas_cylinder(p: Pen, t: float) -> None:
    """Gas cylinder with a pressure gauge needle moving."""
    p.rect(36, 30, 92, 122, fill=(200, 60, 60, 255), width=2.4, radius=18)
    p.rect(54, 16, 74, 30, fill=STEEL, width=2)
    p.rect(48, 10, 80, 16, fill=STEEL_D, width=1.8, radius=2)
    p.circle(96, 18, 13, fill=WHITE, width=2)
    p.line([(80, 13), (86, 16)], fill=OUTLINE, width=3)
    ang = -210 + 240 * (0.5 - 0.5 * math.cos(2 * math.pi * t))
    tip = rot([(96 + 10, 18)], 96, 18, ang)[0]
    p.line([(96, 18), tip], fill=RED, width=2)
    p.circle(96, 18, 2, fill=OUTLINE, outline=None)
    p.rect(42, 64, 86, 84, fill=WHITE, width=1.4, radius=3)
    p.poly([(64, 66), (58, 76), (64, 82), (70, 76)], fill=ORANGE, width=1)


# ---------------------------------------------------------------------------
# Status / alarms
# ---------------------------------------------------------------------------
def st_beacon(p: Pen, t: float) -> None:
    """Rotating red warning beacon."""
    a = t * 360
    front = math.cos(math.radians(a))
    if front > 0.3:
        for i in range(7):
            ang = 180 + 30 * i
            p.line(rot([(64 + 30, 66), (64 + 30 + 10 * front, 66)], 64, 66, ang), fill=ORANGE, width=3)
    p.rect(30, 100, 98, 120, fill=DARK, width=2.2, radius=3)
    p.rect(38, 92, 90, 102, fill=STEEL, width=2)
    p.poly([(42, 92), (42, 54)] + [(64 + 22 * math.cos(math.radians(180 + i * 18)), 54 + 22 * math.sin(math.radians(180 + i * 18))) for i in range(11)] + [(86, 92)],
           fill=RED, width=2.2)
    x = 64 + 18 * math.sin(math.radians(a))
    p.rect(x - 5, 46, x + 5, 90, fill=rgba(YELLOW_L, 0.9 if math.cos(math.radians(a)) > 0 else 0.25), outline=None,
           radius=4)


def st_warning(p: Pen, t: float) -> None:
    """Blinking warning triangle."""
    on = t < 0.5
    p.poly([(64, 10), (122, 112), (6, 112)], fill=YELLOW if on else (150, 120, 20, 255), width=3.5)
    p.rect(58, 42, 70, 84, fill=OUTLINE, outline=None, radius=5)
    p.circle(64, 97, 7, fill=OUTLINE, outline=None)


def st_running(p: Pen, t: float) -> None:
    """Green 'running' indicator with pulse rings."""
    for k in range(2):
        ph = (t + k / 2) % 1
        p.circle(64, 64, 28 + ph * 32, fill=None, outline=rgba(GREEN, 1 - ph), width=3)
    p.circle(64, 64, 28, fill=GREEN, outline=GREEN_D, width=3)
    p.poly([(56, 50), (78, 64), (56, 78)], fill=WHITE, outline=None)


def st_bell(p: Pen, t: float) -> None:
    """Ringing alarm bell."""
    ang = 18 * math.sin(2 * math.pi * t * 2)
    bell = [(64, 18)]
    for i in range(13):
        u = i / 12
        bell.append((64 + 6 + 30 * u ** 1.4, 22 + 64 * u))
    bell += [(104, 92), (24, 92)]
    for i in range(12, -1, -1):
        u = i / 12
        bell.append((64 - 6 - 30 * u ** 1.4, 22 + 64 * u))
    p.poly(rot(bell, 64, 14, ang), fill=YELLOW, width=2.6)
    cl = rot([(64 - ang * 0.5, 100)], 64, 14, ang)[0]
    p.circle(cl[0], cl[1], 7, fill=ORANGE, width=2)
    p.circle(64, 14, 5, fill=ORANGE, width=2)
    if abs(ang) > 6:
        for r in (16, 26):
            p.arc(64, 60, 44 + r * 0.4, -40 if ang > 0 else 220, 40 if ang > 0 else 320 - 0, fill=RED, width=3)


def st_stopped(p: Pen, t: float) -> None:
    """Red 'stopped / fault' indicator, blinking."""
    on = t < 0.5
    p.poly([(40, 8), (88, 8), (120, 40), (120, 88), (88, 120), (40, 120), (8, 88), (8, 40)],
           fill=RED if on else RED_D, width=3)
    p.rect(30, 56, 98, 72, fill=WHITE, outline=None, radius=3)


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------
#: (category, file stem, display name, draw function, frames, ms per frame)
ANIMATIONS = [
    ("HVAC", "axial-fan", "Axial fan", hvac_fan, 12, 50),
    ("HVAC", "centrifugal-blower", "Centrifugal blower", hvac_blower, 12, 50),
    ("HVAC", "cooling-tower", "Cooling tower", hvac_cooling_tower, 24, 90),
    ("HVAC", "chiller", "Chiller", hvac_chiller, 24, 80),
    ("HVAC", "air-damper", "Air damper", hvac_damper, 30, 70),
    ("HVAC", "air-duct-flow", "Air duct flow", hvac_airflow, 12, 60),
    ("HVAC", "heater", "Heater / radiator", hvac_heater, 16, 70),
    ("HVAC", "thermometer", "Thermometer", hvac_thermometer, 30, 80),
    ("Electrical", "high-voltage", "High voltage", el_lightning, 20, 70),
    ("Electrical", "power-flow", "Power flow", el_power_flow, 16, 60),
    ("Electrical", "transformer", "Transformer", el_transformer, 20, 70),
    ("Electrical", "circuit-breaker", "Circuit breaker", el_breaker, 20, 100),
    ("Electrical", "battery-charging", "Battery charging", el_battery_charge, 20, 120),
    ("Electrical", "battery-discharging", "Battery discharging", el_battery_discharge, 20, 120),
    ("Electrical", "electric-motor", "Electric motor", el_motor, 12, 50),
    ("Electrical", "solar-panel", "Solar panel", el_solar, 24, 80),
    ("Electrical", "wind-turbine", "Wind turbine", el_wind_turbine, 18, 70),
    ("Electrical", "energy-meter", "Energy meter", el_meter, 30, 100),
    ("Water", "centrifugal-pump", "Centrifugal pump", water_pump, 12, 50),
    ("Water", "pipe-flow", "Water pipe flow", water_pipe_flow, 12, 60),
    ("Water", "gate-valve", "Gate valve", water_valve, 16, 70),
    ("Water", "tank-filling", "Tank filling", water_tank_fill, 30, 90),
    ("Water", "tank-draining", "Tank draining", water_tank_drain, 30, 90),
    ("Water", "water-drop", "Water drop", water_drop, 24, 60),
    ("Water", "sprinkler", "Sprinkler", water_sprinkler, 24, 70),
    ("Generators", "diesel-genset", "Diesel generator set", gen_genset, 24, 70),
    ("Generators", "alternator", "Alternator", gen_alternator, 24, 50),
    ("Generators", "generator-ac-output", "Generator AC output", gen_ac_output, 16, 60),
    ("Fuel", "fuel-tank", "Fuel tank", fuel_tank, 40, 100),
    ("Fuel", "fuel-dispenser", "Fuel dispenser", fuel_dispenser, 30, 80),
    ("Fuel", "burner-flame", "Burner flame", fuel_flame, 16, 60),
    ("Fuel", "fuel-line-flow", "Fuel line flow", fuel_pipe_flow, 12, 60),
    ("Fuel", "gas-cylinder", "Gas cylinder", fuel_gas_cylinder, 30, 80),
    ("Status", "warning-beacon", "Warning beacon", st_beacon, 18, 50),
    ("Status", "warning-triangle", "Warning triangle", st_warning, 2, 500),
    ("Status", "running", "Running", st_running, 20, 70),
    ("Status", "alarm-bell", "Alarm bell", st_bell, 16, 50),
    ("Status", "stopped-fault", "Stopped / fault", st_stopped, 2, 500),
]


def to_gif_frames(frames: list) -> list:
    """Convert RGBA frames to palette frames sharing one transparent palette.

    Pixels with alpha below 50 % become transparent (palette index 255); the
    other 255 colours are chosen for all frames together, so colours do not
    flicker between frames.

    Args:
        frames: List of RGBA images of equal size.

    Returns:
        List of ``P`` mode images.
    """
    w, h = frames[0].size
    strip = Image.new("RGB", (w * len(frames), h), (0, 0, 0))
    for i, fr in enumerate(frames):
        bg = Image.new("RGB", (w, h), (0, 0, 0))
        bg.paste(fr.convert("RGB"), mask=fr.getchannel("A").point(lambda a: 255 if a >= 128 else 0))
        strip.paste(bg, (i * w, 0))
    pal_img = strip.quantize(colors=255, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    palette = pal_img.getpalette()[: 255 * 3]
    palette += [0, 0, 0] * (256 - len(palette) // 3)
    ref = Image.new("P", (1, 1))
    ref.putpalette(palette)
    out = []
    for fr in frames:
        rgb = fr.convert("RGB").quantize(palette=ref, dither=Image.Dither.NONE)
        mask = fr.getchannel("A").point(lambda a: 255 if a < 128 else 0)
        rgb.paste(255, mask=mask)
        rgb.info["transparency"] = 255
        out.append(rgb)
    return out


def render(draw, frames: int, size: int) -> list:
    """Render all RGBA frames of one animation.

    Args:
        draw: Draw function ``draw(pen, t)`` working on a 128 x 128 grid.
        frames: Number of frames.
        size: Output size in pixels.

    Returns:
        List of RGBA images (``size`` x ``size``).
    """
    result = []
    for i in range(frames):
        pen = Pen(128)
        draw(pen, i / frames)
        img = pen.img
        result.append(img.resize((size, size), Image.LANCZOS))
    return result


def build(out_dir: str, size: int = 128, only: str | None = None) -> list:
    """Generate every animation and the manifest.

    Args:
        out_dir: Output folder.
        size: GIF size in pixels (square).
        only: Optional file stem; when given, only that animation is written.

    Returns:
        The manifest entries (``category``, ``name``, ``file``, ``frames``,
        ``duration_ms``).
    """
    random.seed(1)
    manifest = []
    for category, stem, name, draw, frames, ms in ANIMATIONS:
        rel = f"{category}/{stem}.gif"
        manifest.append({"category": category, "name": name, "file": rel, "frames": frames,
                         "duration_ms": frames * ms})
        if only and stem != only:
            continue
        rgba_frames = render(draw, frames, size)
        gif_frames = to_gif_frames(rgba_frames)
        path = os.path.join(out_dir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        gif_frames[0].save(path, save_all=True, append_images=gif_frames[1:], duration=ms, loop=0, disposal=2,
                           transparency=255, optimize=False)
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump({"size": size, "animations": manifest}, fh, indent=1)
    return manifest


def main() -> None:
    """Command-line entry point (see the module docstring)."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=os.path.join(here, "app", "library", "animations"))
    parser.add_argument("--size", type=int, default=128)
    parser.add_argument("--only", help="generate only this file stem (e.g. axial-fan)")
    args = parser.parse_args()
    items = build(args.out, args.size, args.only)
    print(f"{len(items)} animations -> {args.out}")


if __name__ == "__main__":
    main()

