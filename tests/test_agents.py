"""webMAN agent queue and the route preference that depends on it."""

from __future__ import annotations

import asyncio
import unittest

from app import agents, main


CABINET_ID = "agent01"


class AgentHubTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        agents.hub = agents.AgentHub()

    async def test_queue_is_refused_when_no_agent_is_polling(self) -> None:
        """A command parked for an absent console would fire unpredictably."""
        self.assertFalse(agents.hub.enqueue(CABINET_ID, "/reboot.ps3?soft"))

    async def test_queued_command_wakes_a_waiting_poll(self) -> None:
        agents.hub.note_seen(CABINET_ID, "xmb")
        poll = asyncio.create_task(agents.hub.wait(CABINET_ID))
        await asyncio.sleep(0.05)

        self.assertTrue(agents.hub.enqueue(CABINET_ID, "/reboot.ps3?soft"))
        self.assertEqual(await poll, ["/reboot.ps3?soft"])

    async def test_commands_queued_before_the_poll_are_returned_at_once(self) -> None:
        agents.hub.note_seen(CABINET_ID, "game")
        agents.hub.enqueue(CABINET_ID, "/a.ps3")
        agents.hub.enqueue(CABINET_ID, "/b.ps3")
        self.assertEqual(
            await asyncio.wait_for(agents.hub.wait(CABINET_ID), timeout=1),
            ["/a.ps3", "/b.ps3"],
        )

    async def test_presence_expires(self) -> None:
        agents.hub.note_seen(CABINET_ID, "xmb")
        self.assertTrue(agents.hub.online(CABINET_ID))
        agents.hub._seen[CABINET_ID] = (0.0, "xmb")
        self.assertFalse(agents.hub.online(CABINET_ID))

    async def test_agent_is_preferred_over_the_other_routes(self) -> None:
        """The agent is the only route that works with no game running."""
        from app import cabinets
        from app.config import settings

        settings.cabinets_root.mkdir(parents=True, exist_ok=True)
        cab = dict(cabinets._defaults(), cabinet_id=CABINET_ID)
        cabinets._save(cab)
        agents.hub.note_seen(CABINET_ID, "xmb")

        result = await main.cabinet_webman(CABINET_ID, "restart_game")
        self.assertEqual(result["route"], "agent")
        self.assertEqual(
            await asyncio.wait_for(agents.hub.wait(CABINET_ID), timeout=1),
            [main.WEBMAN_ACTIONS["restart_game"]],
        )


class AgentTokenTests(unittest.TestCase):
    def test_agent_token_is_not_the_catalog_token(self) -> None:
        """The agent credential is cleartext on the LAN and sits on cabinet
        disks; it must not be the one that also mints TaikOnline cards."""
        from app.config import settings

        self.assertTrue(settings.agent_token)
        self.assertNotEqual(settings.agent_token, settings.api_token)

    def test_connector_provisions_its_token_to_a_cabinet(self) -> None:
        from app import cabinets
        from app.config import settings

        frame = (
            "id=prov01\nhave_count=0\n\n"
            "[network]\nagent_token = stale-value\n"
        )
        cabinets.handle_frame(frame, True)
        cab = cabinets.load("prov01")
        self.assertEqual(
            cab["config_pending"].get("network.agent_token"), settings.agent_token
        )


if __name__ == "__main__":
    unittest.main()


class ScreenshotTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        agents.hub = agents.AgentHub()

    async def test_falls_back_to_the_agent_when_no_game_is_running(self) -> None:
        """The agent's verb is not a webMAN path — it captures, then uploads."""
        agents.hub.note_seen("shot01", "xmb")
        result = await main.cabinet_screenshot_request("shot01")
        self.assertEqual(result["route"], "agent")
        self.assertEqual(
            await asyncio.wait_for(agents.hub.wait("shot01"), timeout=1), ["screenshot"]
        )

    async def test_prefers_the_plugin_while_the_game_runs(self) -> None:
        """Only the plugin can capture a running game; webMAN refuses to."""
        from app import control

        hub = control.ControlHub()
        sent: list[str] = []

        class _Socket:
            async def send_text(self, text: str) -> None:
                sent.append(text)

        hub._cabinets["shot02"] = _Socket()
        agents.hub.note_seen("shot02", "game")
        original = control.hub
        control.hub = hub
        try:
            result = await main.cabinet_screenshot_request("shot02")
        finally:
            control.hub = original
        self.assertEqual(result["route"], "plugin")
        self.assertEqual(sent, ["G\n"])

    async def test_upload_replaces_atomically(self) -> None:
        from app.config import settings

        class _Req:
            async def body(self):
                return b"BM" + b"\x00" * 64

        path = main._upload_path("shot01", "screenshot")
        result = await main.agent_upload(_Req(), id="shot01", kind="screenshot")
        self.assertEqual(result["status"], "stored")
        self.assertTrue(path.is_file())
        # A crashed upload must not leave the partial file in place of the good one.
        self.assertFalse(path.with_suffix(".part").exists())
        self.assertTrue(str(path).startswith(str(settings.cabinets_root)))

    async def test_unknown_kind_is_refused(self) -> None:
        class _Req:
            async def body(self):
                return b"x"

        with self.assertRaises(Exception):
            await main.agent_upload(_Req(), id="shot01", kind="../etc/passwd")


class AgentEverTests(unittest.TestCase):
    """The console-control UI is hidden until a cabinet proves it has an agent."""

    def test_first_poll_marks_it_and_later_polls_do_not_rewrite(self) -> None:
        from app import cabinets

        cabinets._save(dict(cabinets._defaults(), cabinet_id="ever01"))
        self.assertFalse(cabinets.load("ever01")["agent_ever"])

        cabinets.mark_agent_seen("ever01")
        self.assertTrue(cabinets.load("ever01")["agent_ever"])

        # Repeats every ~25s forever; must not rewrite the file each time.
        stamp = cabinets._path("ever01").stat().st_mtime_ns
        cabinets.mark_agent_seen("ever01")
        self.assertEqual(cabinets._path("ever01").stat().st_mtime_ns, stamp)

    def test_unknown_cabinet_is_ignored(self) -> None:
        from app import cabinets

        cabinets.mark_agent_seen("nosuchcab")
        self.assertIsNone(cabinets.load("nosuchcab"))


class SensorTests(unittest.TestCase):
    """Console health rides on the poll; a refused syscall is not an error."""

    def setUp(self) -> None:
        agents.hub = agents.AgentHub()

    def test_reported_values_reach_the_status_payload(self) -> None:
        agents.hub.note_seen(CABINET_ID, "xmb")
        agents.hub.note_sensors(CABINET_ID, "62", "58", "41")
        status = agents.hub.status(CABINET_ID)["agent_health"]
        self.assertEqual(status["cpu_temp"], 62)
        self.assertEqual(status["rsx_temp"], 58)
        self.assertEqual(status["fan_percent"], 41)

    def test_missing_or_implausible_readings_become_zero(self) -> None:
        # lv2 gates get_fan_policy, so an empty fan field is a normal cabinet.
        agents.hub.note_sensors(CABINET_ID, "62", "", "")
        status = agents.hub.status(CABINET_ID)["agent_health"]
        self.assertEqual(status["rsx_temp"], 0)
        self.assertEqual(status["fan_percent"], 0)

        agents.hub.note_sensors(CABINET_ID, "999", "-5", "12x")
        status = agents.hub.status(CABINET_ID)["agent_health"]
        self.assertEqual((status["cpu_temp"], status["rsx_temp"], status["fan_percent"]), (0, 0, 0))

    def test_a_cabinet_that_never_reported_still_has_the_fields(self) -> None:
        self.assertEqual(agents.hub.status("silent")["agent_health"]["cpu_temp"], 0)


# One page as webMAN renders it: the markup around each figure, the localised
# fan label, the Fahrenheit block that must not be mistaken for Celsius.
WEBMAN_PAGE = b"""<html><body>
<a href="/">Refresh</a> [KLIC] [BGM]
SCEEXE001 Taiko no Tatsujin(S111) <b>pid=01010200</b>
<hr><font size="42px"><b><a class="s" href="/cpursx.ps3?up">CPU: 68&deg;C (MAX: 69&deg;C)<br>
RSX: 68&deg;C</a><hr><a class="s" href="/cpursx.ps3?dn">CPU: 154&deg;F (MAX: 156&deg;F)<br>
RSX: 154&deg;F</a><br>GPU: 500 Mhz &bull; VRAM: 650 Mhz<hr>
<a class="s" href="/games.ps3">MEM: 1,576 KB (XMB)</a><br>
<a href="/dev_hdd0/">HDD: 116.7 GB free</a><hr>
<a class="s" href="/cpursx.ps3?mode">FAN SPEED: 30% (0x4D)</a><br><hr>
<label title="Startup">00:28:39</label>
</font><H1>206d 23:34:09 &bull; 4,649 ON &bull; 4,627 OFF (22)</H1>
NOR Firmware: 4.84 DEX Cobra 8.1
</body></html>"""


class HealthPageTests(unittest.TestCase):
    """webMAN's info page is the only source for the figures lv2 gates."""

    def setUp(self) -> None:
        agents.hub = agents.AgentHub()

    def test_figures_are_read_off_the_page(self) -> None:
        page = agents.parse_health(WEBMAN_PAGE)
        self.assertEqual(page["cpu_temp"], 68)
        self.assertEqual(page["rsx_temp"], 68)
        self.assertEqual(page["max_temp"], 69)
        self.assertEqual(page["fan_percent"], 30)
        self.assertEqual(page["mem_kb"], 1576)
        self.assertEqual(page["gpu_mhz"], 500)
        self.assertEqual(page["vram_mhz"], 650)
        self.assertEqual(page["hdd_free"], "116.7 GB free")
        self.assertEqual(page["firmware"], "4.84 DEX Cobra 8.1")
        self.assertEqual(page["power_ons"], 4649)
        self.assertIn("206d 23:34:09", str(page["runtime"]))

    def test_markup_is_gone_and_the_text_survives(self) -> None:
        text = agents.health_text(WEBMAN_PAGE)
        self.assertNotIn("<", text)
        self.assertIn("FAN SPEED: 30% (0x4D)", text)
        self.assertIn("CPU: 68°C (MAX: 69°C)", text)

    def test_an_unrecognised_page_yields_zeros_not_an_error(self) -> None:
        page = agents.parse_health(b"<html><body>Access denied</body></html>")
        self.assertEqual(page["cpu_temp"], 0)
        self.assertEqual(page["fan_percent"], 0)
        self.assertEqual(page["text"], "Access denied")

    def test_an_oversized_page_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            agents.parse_health(b"x" * (agents.MAX_HEALTH_BYTES + 1))

    def test_poll_readings_win_over_the_older_page(self) -> None:
        # Temperatures arrive every 25 s, the page every two minutes.
        agents.hub.note_health(CABINET_ID, agents.parse_health(WEBMAN_PAGE))
        agents.hub.note_sensors(CABINET_ID, "71", "70", "")
        health = agents.hub.health(CABINET_ID)["agent_health"]
        self.assertEqual(health["cpu_temp"], 71)
        # ...but a field only the page has is not wiped by a poll without it.
        self.assertEqual(health["fan_percent"], 30)
        self.assertEqual(health["hdd_free"], "116.7 GB free")
