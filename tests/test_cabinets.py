from __future__ import annotations

import os
import tempfile
import unittest

os.environ.setdefault("CONNECTOR_CABINETS_ROOT", tempfile.mkdtemp())

from app import cabinets
from app.config import settings


POLL = (
    "id=ab12cd34\n"
    "serial=268410000000\n"
    "name=Front Left\n"
    "game=S111\n"
    "version=1.5.0\n"
    "seq=0\n"
    "have ese_x1\n"
    "\n"
    "[network]\nconnector_host = 10.0.0.2\n"
)


class CabinetPollTests(unittest.TestCase):
    def setUp(self) -> None:
        settings.cabinets_root.mkdir(parents=True, exist_ok=True)
        for p in settings.cabinets_root.glob("*.json"):
            p.unlink()

    def test_heartbeat_registers_cabinet(self) -> None:
        resp = cabinets.handle_poll(POLL)
        self.assertEqual(resp, "\n")  # unmanaged, nothing pending
        cab = cabinets.load("ab12cd34")
        self.assertEqual(cab["game_name"], "Green")
        self.assertEqual(cab["have"], ["ese_x1"])
        self.assertIn("[network]", cab["reported_cfg"])

    def test_pending_config_and_selection_roundtrip(self) -> None:
        cabinets.handle_poll(POLL)
        cabinets.set_config("ab12cd34", {"chassis.force_freeplay": "1"})
        cabinets.set_selection("ab12cd34", ["ese_x1", "ese_y2"])

        resp = cabinets.handle_poll(POLL)
        self.assertIn("managed=1", resp)
        self.assertIn("seq=1", resp)
        self.assertIn("sel ese_y2", resp)
        self.assertIn("cfg chassis.force_freeplay=1", resp)

        acked = POLL.replace("seq=0", "seq=1").replace(
            "have ese_x1",
            "have ese_x1\nhave ese_y2\napplied=chassis.force_freeplay=1",
        )
        resp = cabinets.handle_poll(acked)
        self.assertNotIn("cfg ", resp)
        cab = cabinets.load("ab12cd34")
        self.assertEqual(cab["acked_seq"], 1)
        self.assertEqual(cab["config_pending"], {})

    def test_reported_config_clears_pending_after_reboot(self) -> None:
        cabinets.handle_poll(POLL)
        cabinets.set_config("ab12cd34", {"chassis.force_freeplay": "1"})

        rebooted = POLL.replace(
            "[network]\nconnector_host = 10.0.0.2\n",
            "[network]\nconnector_host = 10.0.0.2\n"
            "[chassis]\nforce_freeplay = 1\n",
        )
        cabinets.handle_poll(rebooted)

        self.assertEqual(cabinets.load("ab12cd34")["config_pending"], {})

    def test_selection_edits_queue_behind_active_sync(self) -> None:
        cabinets.handle_poll(POLL)
        first = cabinets.set_selection("ab12cd34", ["ese_x1", "ese_y2"])
        self.assertEqual(first["selection_seq"], 1)

        queued = cabinets.set_selection("ab12cd34", ["ese_x1", "ese_z3"])
        self.assertEqual(queued["selection"], ["ese_x1", "ese_y2"])
        self.assertEqual(queued["queued_selection"], ["ese_x1", "ese_z3"])
        self.assertEqual(queued["selection_seq"], 1)

        pending = cabinets.handle_poll(POLL)
        self.assertIn("seq=1", pending)
        self.assertIn("sel ese_y2", pending)
        self.assertNotIn("sel ese_z3", pending)

        promoted = cabinets.handle_poll(POLL.replace("seq=0", "seq=1"))
        self.assertIn("seq=2", promoted)
        self.assertIn("sel ese_z3", promoted)
        cab = cabinets.load("ab12cd34")
        self.assertEqual(cab["acked_seq"], 1)
        self.assertEqual(cab["selection_seq"], 2)
        self.assertIsNone(cab["queued_selection"])

    def test_operation_progress_is_saved(self) -> None:
        poll = POLL.replace(
            "seq=0\n",
            "seq=0\nop_seq=3\nop_phase=downloading\nop_done=12\n"
            "op_total=40\nop_failed=1\nop_song=ese_y2\n"
            "op_error=conversion failed\n",
        )
        cabinets.handle_poll(poll)
        cab = cabinets.load("ab12cd34")
        self.assertEqual(cab["operation_seq"], 3)
        self.assertEqual(cab["operation_phase"], "downloading")
        self.assertEqual(cab["operation_done"], 12)
        self.assertEqual(cab["operation_total"], 40)
        self.assertEqual(cab["operation_failed"], 1)
        self.assertEqual(cab["operation_song"], "ese_y2")

    def test_incomplete_inventory_preserves_last_complete_list(self) -> None:
        cabinets.handle_poll(POLL)
        busy = POLL.replace(
            "seq=0\n",
            "seq=0\nhave_complete=0\nop_phase=downloading\n",
        ).replace("have ese_x1\n", "")
        cabinets.handle_poll(busy)
        self.assertEqual(cabinets.load("ab12cd34")["have"], ["ese_x1"])

    def test_stale_value_ack_does_not_clear_newer_pending_value(self) -> None:
        cabinets.handle_poll(POLL)
        cabinets.set_config("ab12cd34", {"chassis.force_freeplay": "0"})

        stale_ack = POLL.replace(
            "have ese_x1", "have ese_x1\napplied=chassis.force_freeplay=1"
        )
        response = cabinets.handle_poll(stale_ack)

        self.assertIn("cfg chassis.force_freeplay=0", response)
        self.assertEqual(
            cabinets.load("ab12cd34")["config_pending"],
            {"chassis.force_freeplay": "0"},
        )

    def test_missing_id_rejected(self) -> None:
        self.assertIn("error=", cabinets.handle_poll("serial=1\n\n"))

    def test_path_traversal_sanitized(self) -> None:
        cabinets.handle_poll(POLL.replace("id=ab12cd34", "id=../../etc/pwn"))
        for p in settings.cabinets_root.glob("*.json"):
            self.assertNotIn("/", p.stem)


if __name__ == "__main__":
    unittest.main()
