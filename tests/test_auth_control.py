from __future__ import annotations

import time
import unittest

from app import auth
from app.config import settings
from app.control import ControlHub


class ManagementAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.old_pin = settings.management_pin
        self.old_seconds = settings.management_session_seconds
        settings.management_pin = "2468"
        settings.management_session_seconds = 3600

    def tearDown(self) -> None:
        settings.management_pin = self.old_pin
        settings.management_session_seconds = self.old_seconds

    def test_signed_cookie_roundtrip_and_tamper_rejection(self) -> None:
        cookie = auth.issue_cookie()
        self.assertTrue(auth.cookie_valid(cookie))
        self.assertFalse(auth.cookie_valid(cookie + "0"))
        self.assertFalse(auth.cookie_valid(None))

    def test_expired_cookie_is_rejected(self) -> None:
        payload = f"{auth.COOKIE_VERSION}.{int(time.time()) - 1}.nonce"
        cookie = f"{payload}.{auth._sign(payload)}"
        self.assertFalse(auth.cookie_valid(cookie))

    def test_pin_comparison(self) -> None:
        self.assertTrue(auth.pin_matches("2468"))
        self.assertFalse(auth.pin_matches("2469"))


class ControlProtocolTests(unittest.TestCase):
    def test_state_message_maps_buttons_to_stable_bits(self) -> None:
        seq, mask = ControlHub._parse_state(
            '{"type":"state","seq":12,"buttons":["hit_center_left","coin","down",'
            '"p2_hit_side_right"]}'
        )
        self.assertEqual(seq, 12)
        self.assertEqual(mask, (1 << 1) | (1 << 7) | (1 << 9) | (1 << 13))

    def test_state_message_rejects_unknown_buttons(self) -> None:
        with self.assertRaises(ValueError):
            ControlHub._parse_state(
                '{"type":"state","seq":1,"buttons":["reboot"]}'
            )

    def test_state_message_rejects_invalid_sequence(self) -> None:
        with self.assertRaises(ValueError):
            ControlHub._parse_state('{"type":"state","seq":0,"buttons":[]}')


if __name__ == "__main__":
    unittest.main()
