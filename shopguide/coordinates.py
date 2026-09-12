"""PDF bottom-left CropBox points ↔ clockwise-rotated top-left normalized points."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .schemas import BBox


@dataclass(frozen=True)
class PageTransform:
    cropbox: tuple[float, float, float, float]
    rotation: int = 0

    def __post_init__(self):
        x0, y0, x1, y1 = self.cropbox
        if not all(math.isfinite(v) for v in self.cropbox) or x0 >= x1 or y0 >= y1:
            raise ValueError("invalid CropBox")
        if self.rotation not in (0, 90, 180, 270):
            raise ValueError("unsupported rotation")

    def forward(self, x: float, y: float) -> tuple[float, float]:
        x0, y0, x1, y1 = self.cropbox
        u, v = (x - x0) / (x1 - x0), (y1 - y) / (y1 - y0)
        return {0: (u, v), 90: (1 - v, u), 180: (1 - u, 1 - v), 270: (v, 1 - u)}[
            self.rotation
        ]

    def inverse(self, u: float, v: float) -> tuple[float, float]:
        u, v = {0: (u, v), 90: (v, 1 - u), 180: (1 - u, 1 - v), 270: (1 - v, u)}[
            self.rotation
        ]
        x0, y0, x1, y1 = self.cropbox
        return x0 + u * (x1 - x0), y1 - v * (y1 - y0)

    def box(self, box: tuple[float, float, float, float]) -> BBox:
        x0, y0, x1, y1 = box
        if x0 >= x1 or y0 >= y1:
            raise ValueError("invalid source box")
        points = [self.forward(x, y) for x in (x0, x1) for y in (y0, y1)]
        return BBox(
            x0=min(p[0] for p in points),
            y0=min(p[1] for p in points),
            x1=max(p[0] for p in points),
            y1=max(p[1] for p in points),
        )

    def matrix(self) -> tuple[float, ...]:
        a, b = self.forward(0, 0)
        c, d = self.forward(1, 0)
        e, f = self.forward(0, 1)
        return c - a, e - a, a, d - b, f - b, b, 0.0, 0.0, 1.0


def pixel_box(box: BBox, width: int, height: int) -> tuple[int, int, int, int]:
    if width <= 0 or height <= 0:
        raise ValueError("invalid image dimensions")
    return (
        math.floor(box.x0 * width),
        math.floor(box.y0 * height),
        math.ceil(box.x1 * width),
        math.ceil(box.y1 * height),
    )
