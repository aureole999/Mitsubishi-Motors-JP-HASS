"""Offline protocol tests. No network or user data is used."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).parents[1]
PACKAGE = ROOT / "custom_components" / "mitsubishi_motors_jp"
PACKAGE_NAME = "mitsubishi_motors_jp"


def _load(name: str):
    if PACKAGE_NAME not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            PACKAGE_NAME,
            PACKAGE / "__init__.py",
            submodule_search_locations=[str(PACKAGE)],
        )
        package = importlib.util.module_from_spec(spec)
        sys.modules[PACKAGE_NAME] = package
        # Do not execute __init__.py because Home Assistant is not needed here.
        package.__path__ = [str(PACKAGE)]
    spec = importlib.util.spec_from_file_location(
        f"{PACKAGE_NAME}.{name}", PACKAGE / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


protocol = _load("protocol")
models = _load("models")


class ProtocolTests(unittest.TestCase):
    def test_idm_round_trip(self):
        value = {"username": "example@example.invalid", "nested": {"ok": True}}
        self.assertEqual(protocol.idm_decrypt(protocol.idm_encrypt(value)), value)

    def test_kintaro_round_trip(self):
        value = {"vin": "REDACTED", "refreshType": 0}
        encrypted = protocol.kintaro_encrypt(
            json.dumps(value, separators=(",", ":")), protocol.KINTARO_INIT_KEY
        )
        self.assertEqual(
            protocol.kintaro_decrypt(encrypted, protocol.KINTARO_INIT_KEY), value
        )

    def test_signature_formula(self):
        ciphertext = "ciphertext"
        timestamp = "1780000000000"
        key = "sign-key"
        expected_input = (
            ciphertext
            + timestamp
            + timestamp[protocol.KINTARO_INDICES[8] :]
            + timestamp[protocol.KINTARO_INDICES[9] :]
            + key
        )
        self.assertEqual(
            protocol.kintaro_signature(ciphertext, timestamp, key),
            hashlib.sha256(expected_input.encode()).hexdigest().upper(),
        )

    def test_vehicle_name_does_not_expose_full_vin(self):
        vehicle = models.Vehicle("0123456789ABCDEFG", "internal", model="Outlander")
        self.assertEqual(vehicle.name, "Outlander BCDEFG")
        self.assertNotIn(vehicle.vin, vehicle.name)


if __name__ == "__main__":
    unittest.main()

