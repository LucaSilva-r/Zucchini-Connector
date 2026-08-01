from __future__ import annotations

import asyncio
import unittest

from app.control import ControlHub


class StubCabinet:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send_text(self, message: str) -> None:
        self.messages.append(message)


class ItaikoSettingsProtocolTests(unittest.TestCase):
    def test_status_parses_only_supported_settings(self) -> None:
        cabinet_id, device, status = ControlHub._parse_itaiko_frame(
            "id=front-left\n"
            "dev=1\n"
            "state=ready\n"
            "version=1.17.0\n"
            "edition=iTAIKO\n"
            "mode=KEYBOARD_P2\n"
            "settings=0:80 4:30 9:1 17:4095 46:25\n"
            "error=\n"
        )
        self.assertEqual(cabinet_id, "front-left")
        self.assertEqual(device, 1)
        self.assertEqual(status["mode"], "KEYBOARD_P2")
        self.assertEqual(status["state"], "ready")
        self.assertEqual(
            status["settings"],
            {"0": 80, "4": 30, "9": 1, "17": 4095, "46": 25},
        )

    def test_status_rejects_key_mapping_and_out_of_range_values(self) -> None:
        for settings_line in ("18:7", "4:1001", "46:51", "9:2"):
            with self.subTest(settings_line=settings_line):
                with self.assertRaises(ValueError):
                    ControlHub._parse_itaiko_frame(
                        "id=front-left\n"
                        "dev=0\n"
                        "state=ready\n"
                        "version=1.17.0\n"
                        "edition=iTAIKO\n"
                        f"settings={settings_line}\n"
                        "error=\n"
                    )

    def test_write_is_canonical_and_rejects_non_settings(self) -> None:
        hub = ControlHub()
        socket = StubCabinet()
        hub._cabinets["front-left"] = socket  # protocol unit test

        sent = asyncio.run(
            hub.request_itaiko_settings(
                "front-left", 1, {"46": 25, "0": 80, "4": 30}
            )
        )
        self.assertTrue(sent)
        self.assertEqual(socket.messages, ["I SET 1 0:80 4:30 46:25\n"])

        for invalid in ({"18": 7}, {"4": 1001}, {"0": 1.5}, {"9": True}):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    asyncio.run(
                        hub.request_itaiko_settings("front-left", 0, invalid)
                    )


    def test_frame_without_device_index_is_drum_zero(self) -> None:
        _, device, _ = ControlHub._parse_itaiko_frame(
            "id=front-left\nstate=ready\nsettings=0:80\nerror=\n"
        )
        self.assertEqual(device, 0)

    def test_frame_with_out_of_range_device_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ControlHub._parse_itaiko_frame(
                "id=front-left\ndev=9\nstate=ready\nsettings=0:80\nerror=\n"
            )

    def test_disconnected_drum_leaves_the_cabinet(self) -> None:
        hub = ControlHub()
        hub._set_itaiko("front-left", 0, {"state": "ready", "settings": {}})
        hub._set_itaiko("front-left", 1, {"state": "ready", "settings": {}})
        self.assertEqual(
            [drum["index"] for drum in hub.decorate({"cabinet_id": "front-left"})["itaiko"]],
            [0, 1],
        )
        hub._set_itaiko("front-left", 0, {"state": "disconnected"})
        self.assertEqual(
            [drum["index"] for drum in hub.decorate({"cabinet_id": "front-left"})["itaiko"]],
            [1],
        )


if __name__ == "__main__":
    unittest.main()
