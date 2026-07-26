from __future__ import annotations

import unittest

from app import cabinets
from app.config import settings


POLL = (
    "id=ab12cd34\n"
    "serial=268410000000\n"
    "name=Front Left\n"
    "game=S111\n"
    "version=1.5.0\n"
    "seq=0\n"
    "have tja_x1\n"
    "have_count=1\n"
    "\n"
    "[network]\nconnector_host = 10.0.0.2\n"
    "[chassis]\nforce_freeplay = 0\n"
)


def heartbeat(body: str) -> str:
    """A full `H` frame: carries a complete inventory and the config body."""
    return cabinets.handle_frame(body, inventory=True)


def telemetry(body: str) -> str:
    """A compact `T` frame: progress only, never touches the inventory."""
    return cabinets.handle_frame(body, inventory=False)


class CabinetPollTests(unittest.TestCase):
    def setUp(self) -> None:
        settings.cabinets_root.mkdir(parents=True, exist_ok=True)
        for p in settings.cabinets_root.glob("*.json"):
            p.unlink()

    def test_heartbeat_registers_cabinet(self) -> None:
        resp = heartbeat(POLL)
        self.assertEqual(resp, "\n")  # unmanaged, nothing pending
        cab = cabinets.load("ab12cd34")
        self.assertEqual(cab["game_name"], "Green")
        self.assertEqual(cab["have"], ["tja_x1"])
        self.assertIn("[network]", cab["reported_cfg"])

    def test_pending_config_and_selection_roundtrip(self) -> None:
        heartbeat(POLL)
        cabinets.set_config("ab12cd34", {"chassis.force_freeplay": "1"})
        cabinets.set_selection("ab12cd34", ["tja_x1", "tja_y2"])

        resp = heartbeat(POLL)
        self.assertIn("managed=1", resp)
        self.assertIn("seq=1", resp)
        self.assertIn("sel tja_y2", resp)
        self.assertIn("cfg chassis.force_freeplay=1", resp)

        acked = POLL.replace("seq=0", "seq=1").replace(
            "have tja_x1\nhave_count=1",
            "have tja_x1\nhave tja_y2\nhave_count=2\n"
            "applied=chassis.force_freeplay=1",
        )
        resp = heartbeat(acked)
        self.assertNotIn("cfg ", resp)
        cab = cabinets.load("ab12cd34")
        self.assertEqual(cab["acked_seq"], 1)
        self.assertEqual(cab["config_pending"], {})

    def test_reported_config_clears_pending_after_reboot(self) -> None:
        heartbeat(POLL)
        cabinets.set_config("ab12cd34", {"chassis.force_freeplay": "1"})

        rebooted = POLL.replace("force_freeplay = 0", "force_freeplay = 1")
        heartbeat(rebooted)

        self.assertEqual(cabinets.load("ab12cd34")["config_pending"], {})

    def test_selection_edits_queue_behind_active_sync(self) -> None:
        heartbeat(POLL)
        first = cabinets.set_selection("ab12cd34", ["tja_x1", "tja_y2"])
        self.assertEqual(first["selection_seq"], 1)

        queued = cabinets.set_selection("ab12cd34", ["tja_x1", "tja_z3"])
        self.assertEqual(queued["selection"], ["tja_x1", "tja_y2"])
        self.assertEqual(queued["queued_selection"], ["tja_x1", "tja_z3"])
        self.assertEqual(queued["selection_seq"], 1)

        pending = heartbeat(POLL)
        self.assertIn("seq=1", pending)
        self.assertIn("sel tja_y2", pending)
        self.assertNotIn("sel tja_z3", pending)

        promoted = heartbeat(POLL.replace("seq=0\n", "seq=1\ndesired_ack=1\n"))
        self.assertIn("seq=2", promoted)
        self.assertIn("sel tja_z3", promoted)
        cab = cabinets.load("ab12cd34")
        self.assertEqual(cab["acked_seq"], 1)
        self.assertEqual(cab["selection_seq"], 2)
        self.assertIsNone(cab["queued_selection"])

    def test_operation_progress_is_saved(self) -> None:
        poll = POLL.replace(
            "seq=0\n",
            "seq=0\nop_seq=3\nop_phase=downloading\nop_done=12\n"
            "op_total=40\nop_failed=1\nop_song=tja_y2\n"
            "op_error=conversion failed\n",
        )
        heartbeat(poll)
        cab = cabinets.load("ab12cd34")
        self.assertEqual(cab["operation_seq"], 3)
        self.assertEqual(cab["operation_phase"], "downloading")
        self.assertEqual(cab["operation_done"], 12)
        self.assertEqual(cab["operation_total"], 40)
        self.assertEqual(cab["operation_failed"], 1)
        self.assertEqual(cab["operation_song"], "tja_y2")

    def test_selection_promotes_after_desired_ack(self) -> None:
        heartbeat(POLL)
        cabinets.set_selection("ab12cd34", ["tja_x1"])
        cabinets.set_selection("ab12cd34", ["tja_y2"])

        response = heartbeat(
            POLL.replace("seq=0\n", "seq=0\ndesired_ack=1\nactive_seq=0\n")
        )
        self.assertIn("seq=2", response)
        self.assertIn("sel tja_y2", response)

    def test_resync_bumps_verify_generation(self) -> None:
        heartbeat(POLL)
        cabinets.set_selection("ab12cd34", ["tja_x1"])
        resynced = cabinets.force_resync("ab12cd34")
        self.assertEqual(resynced["selection_seq"], 1)
        self.assertEqual(resynced["verify_generation"], 1)
        response = heartbeat(
            POLL
        )
        self.assertIn("verify=1", response)

    def test_package_state_is_saved(self) -> None:
        body = POLL.replace(
            "seq=0\n",
            "seq=0\npkg tja_x1 0123456789abcdef installed\n",
        )
        heartbeat(body)
        state = cabinets.load("ab12cd34")["package_states"]["tja_x1"]
        self.assertEqual(state["state"], "installed")
        self.assertEqual(state["revision"], "0123456789abcdef")

    def test_status_frame_preserves_last_complete_inventory(self) -> None:
        heartbeat(POLL)
        busy = POLL.replace(
            "seq=0\n", "seq=0\nop_phase=downloading\n"
        ).replace("have tja_x1\n", "")
        telemetry(busy)
        self.assertEqual(cabinets.load("ab12cd34")["have"], ["tja_x1"])

    def test_websocket_telemetry_updates_transfer_without_clearing_inventory(self) -> None:
        heartbeat(POLL)
        frame = (
            "id=ab12cd34\n"
            "op_seq=4\nop_phase=downloading\nop_done=12\nop_total=50\n"
            "op_failed=0\nop_song=osu_live\nop_error=\n"
            "xfer_active=1\nxfer_done=1048576\nxfer_total=4194304\n"
            "xfer_bps=2097152\nxfer_asset=osu_live/solo/song_ura.bin\n\n"
        )
        telemetry(frame)
        cab = cabinets.load("ab12cd34")
        self.assertEqual(cab["have"], ["tja_x1"])
        self.assertEqual(cab["operation_phase"], "downloading")
        self.assertTrue(cab["transfer_active"])
        self.assertEqual(cab["transfer_done"], 1048576)
        self.assertEqual(cab["transfer_total"], 4194304)
        self.assertEqual(cab["transfer_bps"], 2097152)

    def test_websocket_command_snapshot_matches_poll_grammar(self) -> None:
        heartbeat(POLL)
        cabinets.set_selection("ab12cd34", ["tja_x1", "osu_y2"])
        command = cabinets.command_for("ab12cd34")
        self.assertIn("managed=1\n", command)
        self.assertIn("seq=1\n", command)
        self.assertIn("sel osu_y2\n", command)
        self.assertIn("sel tja_x1\n", command)

    def test_update_is_dispatched_and_acknowledged(self) -> None:
        heartbeat(POLL)
        artifact = {
            "id": "a" * 40,
            "sha1": "a" * 40,
            "version": "0.11.0",
            "size": 123456,
            "filename": "zucchini-gex.sprx",
            "flavor": "gex",
            "uploaded_at": 1,
        }
        queued = cabinets.queue_update("ab12cd34", artifact)
        self.assertEqual(queued["update_phase"], "queued")
        response = heartbeat(POLL)
        self.assertIn(f"update {'a' * 40} 123456 0.11.0", response)
        self.assertTrue(cabinets.load("ab12cd34")["update_dispatched"])

        progress = POLL.replace(
            "seq=0\n",
            f"seq=0\nupdate_work_id={'a' * 40}\nupdate_phase=downloading\n"
            "update_done=65536\nupdate_total=123456\nupdate_error=\n",
        )
        heartbeat(progress)
        self.assertEqual(cabinets.load("ab12cd34")["update_done"], 65536)

        ack = POLL.replace("seq=0\n", f"seq=0\nupdate_ack={'a' * 40}\n")
        response = heartbeat(ack)
        self.assertNotIn("update ", response)
        installed = cabinets.load("ab12cd34")
        self.assertIsNone(installed["update_pending"])
        self.assertEqual(installed["update_installed_version"], "0.11.0")
        self.assertEqual(installed["update_phase"], "complete")

    def test_dispatched_update_cannot_be_cancelled_until_failure(self) -> None:
        heartbeat(POLL)
        artifact = {
            "id": "b" * 40,
            "version": "0.11.0",
            "size": 10,
        }
        cabinets.queue_update("ab12cd34", artifact)
        heartbeat(POLL)
        with self.assertRaises(RuntimeError):
            cabinets.cancel_update("ab12cd34")

        failed = POLL.replace(
            "seq=0\n",
            f"seq=0\nupdate_work_id={'b' * 40}\nupdate_phase=failed\n"
            "update_done=0\nupdate_total=10\nupdate_error=network error\n",
        )
        heartbeat(failed)
        cancelled = cabinets.cancel_update("ab12cd34")
        self.assertIsNone(cancelled["update_pending"])

    def test_stale_value_ack_does_not_clear_newer_pending_value(self) -> None:
        heartbeat(POLL)
        cabinets.set_config("ab12cd34", {"chassis.force_freeplay": "1"})

        stale_ack = POLL.replace(
            "have tja_x1", "have tja_x1\napplied=chassis.force_freeplay=0"
        )
        response = heartbeat(stale_ack)

        self.assertIn("cfg chassis.force_freeplay=1", response)
        self.assertEqual(
            cabinets.load("ab12cd34")["config_pending"],
            {"chassis.force_freeplay": "1"},
        )

    def test_missing_id_rejected(self) -> None:
        self.assertIn("error=", heartbeat("serial=1\n\n"))

    def test_path_traversal_sanitized(self) -> None:
        heartbeat(POLL.replace("id=ab12cd34", "id=../../etc/pwn"))
        for p in settings.cabinets_root.glob("*.json"):
            self.assertNotIn("/", p.stem)


if __name__ == "__main__":
    unittest.main()
