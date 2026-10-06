import math

import pytest

from haier2mqtt.curve import (
    Curve,
    CurveError,
    choose_outdoor,
    format_points,
    parse_points,
    render_svg,
    target,
    validate,
)

DEFAULT = Curve(points=parse_points("-20:40, 0:32, 10:29, 20:28"))


def test_parse_and_format_roundtrip():
    pts = parse_points("-20:40, 0:32, 10:29.5, 20:28")
    assert pts == ((-20.0, 40.0), (0.0, 32.0), (10.0, 29.5), (20.0, 28.0))
    assert format_points(pts) == "-20:40, 0:32, 10:29.5, 20:28"


@pytest.mark.parametrize("text", ["-20:40, 0", "a:b", "-20;40"])
def test_parse_rejects_garbage(text):
    with pytest.raises(CurveError):
        parse_points(text)


def test_target_interpolates_clamps_and_rounds():
    assert target(DEFAULT, -30) == 40.0          # flat beyond the ends
    assert target(DEFAULT, -10) == 36.0
    assert target(DEFAULT, 5) == 30.5            # 30.5 exactly
    assert target(DEFAULT, 30) == 28.0
    assert target(Curve(DEFAULT.points, offset=2.0), 5) == 32.5
    assert target(Curve(DEFAULT.points, min_water=30, max_water=45), 30) == 30.0   # clamped to min


@pytest.mark.parametrize("bad, fragment", [
    (Curve(parse_points("0:30")), "od 2 do 8"),
    (Curve(parse_points("0:30, 0:29")), "powtórzona"),
    (Curve(parse_points("10:29, 0:32")), "posortowane"),
    (Curve(parse_points("0:30, 10:31")), "nie może rosnąć"),
    (Curve(parse_points("0:50, 10:30")), "poza zakresem"),
    (Curve(DEFAULT.points, min_water=45, max_water=30), "zakres"),
    (Curve(DEFAULT.points, min_water=10, max_water=45), "zakres"),
    (Curve(DEFAULT.points, offset=6), "przesunięcie"),
    (Curve(DEFAULT.points, offset=math.nan), "przesunięcie"),
])
def test_validate_rejects(bad, fragment):
    with pytest.raises(CurveError, match=fragment):
        validate(bad)


def test_validate_accepts_default():
    assert validate(DEFAULT) is DEFAULT


def test_choose_outdoor_prefers_fresh_forecast():
    assert choose_outdoor(9.0, 60, 12.0) == (9.0, "prognoza")
    assert choose_outdoor(9.0, 1801, 12.0) == (12.0, "Tao")
    assert choose_outdoor(None, None, 12.0) == (12.0, "Tao")
    assert choose_outdoor(None, None, None) == (None, None)


def test_render_svg_contains_curve_and_marker():
    svg = render_svg(DEFAULT, outdoor=5.0, current=30.5)
    assert svg.startswith("<svg") and "polyline" in svg and "#e8743b" in svg and "30.5" in svg
    assert "#e8743b" not in render_svg(DEFAULT)          # no marker without outdoor/current
