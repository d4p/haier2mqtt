import re
from pathlib import Path

import yaml  # PyYAML is a pytest dependency in most environments; if missing: pip install pyyaml

from tests.test_config_store import OPTIONS

ADDON = Path(__file__).resolve().parents[1]


def test_addon_options_match_loader_keys():
    cfg = yaml.safe_load((ADDON / "config.yaml").read_text())
    assert set(cfg["options"]) == set(OPTIONS)
    assert set(cfg["schema"]) == set(OPTIONS)
    assert cfg["options"]["writes_enabled"] is False
    assert cfg["slug"] == "haier2mqtt" and "mqtt:need" in cfg["services"]


def test_translations_cover_all_options():
    tr = yaml.safe_load((ADDON / "translations" / "en.yaml").read_text())
    assert set(tr["configuration"]) == set(OPTIONS)


def test_run_sh_does_not_mask_bashio_failures():
    text = (ADDON / "run.sh").read_text()
    assert "set -e" in text
    assert not re.search(r'export\s+\w+="\$\(', text)           # `export VAR="$(...)"` hides the exit status
    for var in ("MQTT_HOST", "MQTT_PORT", "MQTT_USER", "MQTT_PASSWORD"):
        assert re.search(rf'^{var}="\$\(bashio::services mqtt ', text, re.MULTILINE), var
    assert "bashio::services.available" in text and "bashio::log.error" in text and "exit 1" in text
