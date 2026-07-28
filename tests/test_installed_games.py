from __future__ import annotations

import asyncio
import unittest
from urllib.parse import quote

from app import agents, cabinets, main
from app.config import settings


CABINET_ID = "games01"
GREEN = "SCEEXE001 GREEN"
YELLOW = "SCEEXE001 YELLOW"


def report(autoboot: str = GREEN) -> bytes:
    return (
        "version=1\n"
        f"autoboot={quote(autoboot, safe='') if autoboot else ''}\n"
        "delay=15\n"
        f"game\t{quote(GREEN, safe='')}\tSCEEXE001\t"
        f"{quote('Taiko no Tatsujin (Green)', safe='')}\t01.00\t1\n"
        f"game\t{quote(YELLOW, safe='')}\tSCEEXE001\t"
        f"{quote('太鼓の達人 Yellow', safe='')}\t01.00\t0\n"
    ).encode()


class InstalledGameReportTests(unittest.TestCase):
    def test_report_preserves_directory_identity_and_utf8_titles(self) -> None:
        parsed = agents.parse_games_report(report())
        self.assertEqual(parsed["autoboot_dir"], GREEN)
        self.assertEqual(parsed["autoboot_delay"], 15)
        games = parsed["installed_games"]
        self.assertEqual({game["directory"] for game in games}, {GREEN, YELLOW})
        self.assertIn("太鼓の達人", next(game["title"] for game in games if game["directory"] == YELLOW))

    def test_path_traversal_is_rejected(self) -> None:
        bad = b"version=1\nautoboot=\ndelay=15\ngame\t..\tBAD\tBad\t1\t0\n"
        with self.assertRaises(ValueError):
            agents.parse_games_report(bad)

    def test_commands_round_trip_spaces(self) -> None:
        self.assertEqual(agents.launch_command(GREEN), "launch\tSCEEXE001%20GREEN")
        self.assertEqual(
            agents.autoboot_command(GREEN, 900),
            "autoboot\tSCEEXE001%20GREEN\t600",
        )


class InstalledGameApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        agents.hub = agents.AgentHub()
        settings.cabinets_root.mkdir(parents=True, exist_ok=True)
        for path in settings.cabinets_root.glob("*.json"):
            path.unlink()
        cabinets._save(dict(cabinets._defaults(), cabinet_id=CABINET_ID))

    async def test_agent_inventory_replaces_complete_snapshot(self) -> None:
        class Request:
            async def body(self) -> bytes:
                return report()

        result = await main.agent_games(Request(), id=CABINET_ID)
        self.assertEqual(result, {"status": "stored", "games": 2})
        cab = cabinets.load(CABINET_ID)
        self.assertEqual(len(cab["installed_games"]), 2)
        self.assertEqual(cab["autoboot_dir"], GREEN)
        self.assertTrue(cab["games_updated_at"])

    async def test_launch_is_one_ordered_exit_then_launch_batch(self) -> None:
        parsed = agents.parse_games_report(report())
        cabinets.set_installed_games(
            CABINET_ID,
            parsed["installed_games"],
            parsed["autoboot_dir"],
            parsed["autoboot_delay"],
        )
        agents.hub.note_seen(CABINET_ID, "game")

        result = main.cabinet_game_launch(CABINET_ID, GREEN)
        self.assertEqual(result["status"], "switching")
        self.assertEqual(
            await asyncio.wait_for(agents.hub.wait(CABINET_ID), timeout=1),
            [
                "/xmb.ps3$exit;/wait.ps3?xmb;/wait.ps3?2",
                "launch\tSCEEXE001%20GREEN",
            ],
        )

    async def test_autoboot_rejects_a_directory_not_in_inventory(self) -> None:
        agents.hub.note_seen(CABINET_ID, "xmb")
        with self.assertRaises(Exception):
            main.cabinet_game_autoboot(CABINET_ID, "NOT INSTALLED", 15)


if __name__ == "__main__":
    unittest.main()
