import json
from pathlib import Path

from haier2mqtt.codec import decode
from haier2mqtt.model import Raw


def test_real_dump_decodes():
    d = json.loads((Path(__file__).parent / "fixtures" / "registers_2026-10-06.json").read_text())
    raw = Raw(tuple(int(v, 16) for v in d["raw"]["core"]), tuple(d["raw"]["status"]), tuple(d["raw"]["mode"]),
              tuple(d["raw"]["advanced"]) if d["raw"]["advanced"] else None)
    r = decode(raw)
    assert r.ch_target == d["reading"]["ch_target"]
    assert (r.twi, r.two, r.tank, r.tao) == (d["reading"]["twi"], d["reading"]["two"], d["reading"]["tank"],
                                             d["reading"]["tao"])
    assert r.unit_state.value == d["reading"]["unit_state"]
