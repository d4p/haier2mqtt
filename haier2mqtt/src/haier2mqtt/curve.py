"""Heating curve: outdoor temperature -> CH water target."""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass

Point = tuple[float, float]
WATER_HARD_MIN, WATER_HARD_MAX = 20.0, 55.0
OFFSET_LIMIT = 5.0


class CurveError(ValueError):
    """Invalid curve; message is Polish (shown in HA)."""


@dataclass(frozen=True)
class Curve:
    points: tuple[Point, ...]
    min_water: float = 25.0
    max_water: float = 45.0
    offset: float = 0.0


def parse_points(text: str) -> tuple[Point, ...]:
    points: list[Point] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            outdoor, water = part.split(":")
            points.append((float(outdoor), float(water)))
        except ValueError as exc:
            raise CurveError(f"niepoprawny punkt „{part}” (oczekiwano np. -20:40)") from exc
    return tuple(points)


def _num(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else str(v)


def format_points(points: tuple[Point, ...]) -> str:
    return ", ".join(f"{_num(o)}:{_num(w)}" for o, w in points)


def validate(c: Curve) -> Curve:
    p = c.points
    if not 2 <= len(p) <= 8:
        raise CurveError("krzywa musi mieć od 2 do 8 punktów")
    if any(not (math.isfinite(o) and math.isfinite(w)) for o, w in p):
        raise CurveError("punkty krzywej muszą być liczbami")
    outdoors = [o for o, _ in p]
    if len(set(outdoors)) != len(outdoors):
        raise CurveError("powtórzona temperatura zewnętrzna w punktach krzywej")
    if outdoors != sorted(outdoors):
        raise CurveError("punkty muszą być posortowane rosnąco według temperatury zewnętrznej")
    if not (math.isfinite(c.min_water) and math.isfinite(c.max_water)
            and WATER_HARD_MIN <= c.min_water < c.max_water <= WATER_HARD_MAX):
        raise CurveError(f"zakres temperatury wody musi mieścić się w {WATER_HARD_MIN:g}–{WATER_HARD_MAX:g} °C (min < max)")
    for _, w in p:
        if not c.min_water <= w <= c.max_water:
            raise CurveError(f"temperatura wody {w:g} °C poza zakresem {c.min_water:g}–{c.max_water:g} °C")
    if any(b[1] > a[1] for a, b in itertools.pairwise(p)):
        raise CurveError("temperatura wody nie może rosnąć wraz z temperaturą zewnętrzną")
    if not (math.isfinite(c.offset) and -OFFSET_LIMIT <= c.offset <= OFFSET_LIMIT):
        raise CurveError(f"przesunięcie krzywej musi być w zakresie ±{OFFSET_LIMIT:g} °C")
    return c


def target(c: Curve, outdoor: float) -> float:
    p = c.points
    if outdoor <= p[0][0]:
        water = p[0][1]
    elif outdoor >= p[-1][0]:
        water = p[-1][1]
    else:
        water = p[-1][1]
        for (o1, w1), (o2, w2) in itertools.pairwise(p):
            if o1 <= outdoor <= o2:
                water = w1 + (w2 - w1) * (outdoor - o1) / (o2 - o1)
                break
    water = min(max(water + c.offset, c.min_water), c.max_water)
    return round(water * 2) / 2


def choose_outdoor(
    forecast: float | None, forecast_age_s: float | None, tao: float | None, stale_after_s: float = 1800.0
) -> tuple[float | None, str | None]:
    if forecast is not None and forecast_age_s is not None and forecast_age_s <= stale_after_s:
        return forecast, "prognoza"
    if tao is not None:
        return tao, "Tao"
    return None, None


def render_svg(c: Curve, outdoor: float | None = None, current: float | None = None) -> str:
    w, h, left, right, top, bottom = 600, 360, 56, 20, 20, 44
    xmin, xmax = -25.0, 25.0
    ymin, ymax = c.min_water - 2, c.max_water + 2

    def x(o: float) -> float:
        return left + (min(max(o, xmin), xmax) - xmin) / (xmax - xmin) * (w - left - right)

    def y(t: float) -> float:
        return top + (ymax - t) / (ymax - ymin) * (h - top - bottom)

    grid = []
    for o in range(-25, 26, 5):
        grid.append(f'<line x1="{x(o):.1f}" y1="{top}" x2="{x(o):.1f}" y2="{h - bottom}" stroke="#8a8a85" stroke-opacity=".25"/>'
                    f'<text x="{x(o):.1f}" y="{h - bottom + 18}" font-size="13" fill="#8a8a85" text-anchor="middle">{o}°</text>')
    t = int(math.ceil(ymin / 5) * 5)
    while t <= ymax:
        grid.append(f'<line x1="{left}" y1="{y(t):.1f}" x2="{w - right}" y2="{y(t):.1f}" stroke="#8a8a85" stroke-opacity=".25"/>'
                    f'<text x="{left - 8}" y="{y(t) + 4:.1f}" font-size="13" fill="#8a8a85" text-anchor="end">{t}°</text>')
        t += 5
    line = " ".join(f"{x(o):.1f},{y(target(c, o)):.1f}" for o in range(-25, 26))
    dots = "".join(f'<circle cx="{x(o):.1f}" cy="{y(min(max(wt + c.offset, c.min_water), c.max_water)):.1f}" r="4" fill="#3987e5"/>'
                   for o, wt in c.points)
    marker = ""
    if outdoor is not None and current is not None:
        marker = (f'<circle cx="{x(outdoor):.1f}" cy="{y(current):.1f}" r="8" fill="#e8743b"/>'
                  f'<text x="{x(outdoor) + 12:.1f}" y="{y(current) - 10:.1f}" font-size="15" fill="#e8743b">'
                  f'{outdoor:g}° → {current:g}°</text>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
            f'font-family="Roboto,Arial,sans-serif">{"".join(grid)}'
            f'<polyline points="{line}" fill="none" stroke="#3987e5" stroke-width="3"/>{dots}{marker}'
            f'<text x="{w - right}" y="{h - 6}" font-size="12" fill="#8a8a85" text-anchor="end">temperatura zewnętrzna</text>'
            f'</svg>')
