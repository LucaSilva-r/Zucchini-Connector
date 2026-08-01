"""Build-name mapping and the capability fields the cabinet reports.

Command delivery itself lives in test_agents: the cabinet no longer relays
webMAN commands, the VSH agent does.
"""

from __future__ import annotations

import asyncio
import unittest

from app import cabinets
from app.config import settings
from app.control import ControlHub
from app.main import PAD_BUTTONS, WEBMAN_ACTIONS

from tests.test_control_heartbeat import CABINET_ID, StubSocket


HEARTBEAT = (
    "H\n"
    f"id={CABINET_ID}\n"
    "serial=268410000000\n"
    "name=Front Left\n"
    "game=ST71\n"
    "build=ST7100-1-JP-MPR0-A03\n"
    "version=1.5.0\n"
    "song_inject=0\n"
    "seq=0\n"
    "have_count=0\n"
    "\n"
)


class BuildNameTests(unittest.TestCase):
    def test_series_is_what_names_the_game_not_the_variant(self) -> None:
        # Red ships as ST8100-1 and ST8100-7; both are Red.
        self.assertEqual(cabinets.game_name("ST81"), "Red")
        self.assertEqual(cabinets.game_name("ST87"), "Red")

    def test_arcade_release_order(self) -> None:
        self.assertEqual(cabinets.game_name("ST71"), "White")
        self.assertEqual(cabinets.game_name("ST91"), "Yellow")
        self.assertEqual(cabinets.game_name("S101"), "Blue")
        self.assertEqual(cabinets.game_name("S111"), "Green")

    def test_unknown_code_is_shown_raw(self) -> None:
        self.assertEqual(cabinets.game_name("ZZ99"), "ZZ99")
        self.assertEqual(cabinets.game_name(""), "")


class CapabilityFieldTests(unittest.TestCase):
    def setUp(self) -> None:
        self.old_token = settings.api_token
        settings.api_token = ""
        settings.cabinets_root.mkdir(parents=True, exist_ok=True)
        for path in settings.cabinets_root.glob("*.json"):
            path.unlink()

    def tearDown(self) -> None:
        settings.api_token = self.old_token

    def test_heartbeat_capabilities_are_stored(self) -> None:
        asyncio.run(ControlHub().cabinet(StubSocket([HEARTBEAT]), CABINET_ID))
        cab = cabinets.load(CABINET_ID)
        self.assertEqual(cab["game_name"], "White")
        self.assertEqual(cab["build"], "ST7100-1-JP-MPR0-A03")
        self.assertFalse(cab["song_inject"])


class WebmanActionTests(unittest.TestCase):
    def test_every_action_is_a_plain_path(self) -> None:
        # These land in a GET request line inside webMAN: a space would end the
        # request target and CR/LF would forge a second request.
        for path in WEBMAN_ACTIONS.values():
            self.assertTrue(path.startswith("/"))
            self.assertTrue(all(0x20 < ord(c) < 0x7F for c in path), path)


class VirtualPadTests(unittest.TestCase):
    def test_no_button_name_contains_another(self) -> None:
        # webMAN picks buttons out of the query with strcasestr, so a name that
        # contains another one presses both. Nothing warns about it: the press
        # just does more than the button says.
        names = [button.lower() for button in PAD_BUTTONS]
        for name in names:
            for other in names:
                if name != other:
                    self.assertNotIn(other, name, f"pad_{name} would also press {other}")

    def test_every_button_is_reachable_as_an_action(self) -> None:
        for button in PAD_BUTTONS:
            self.assertEqual(WEBMAN_ACTIONS[f"pad_{button.lower()}"], f"/pad.ps3?{button}")


if __name__ == "__main__":
    unittest.main()
