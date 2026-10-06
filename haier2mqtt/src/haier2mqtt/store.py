"""Persistent runtime state in /data/state.json."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from .curve import Curve, CurveError, format_points, parse_points, validate

_LOG = logging.getLogger(__name__)


class Store:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def load(self) -> dict:
        try:
            return json.loads(self._path.read_text())
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            _LOG.warning("ignoring unreadable state file %s: %s", self._path, exc)
            return {}

    def save(self, data: dict) -> None:
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1))
        os.replace(tmp, self._path)


def curve_to_dict(c: Curve) -> dict:
    return {"points": format_points(c.points), "min": c.min_water, "max": c.max_water, "offset": c.offset}


def curve_from_dict(d: dict, fallback: Curve) -> Curve:
    try:
        return validate(Curve(parse_points(d["points"]), float(d["min"]), float(d["max"]), float(d["offset"])))
    except (KeyError, TypeError, ValueError, CurveError):
        return fallback
