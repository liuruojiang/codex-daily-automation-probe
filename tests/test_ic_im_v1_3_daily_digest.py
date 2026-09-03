from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
import zipfile
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_ic_im_v1_3_digest as digest  # noqa: E402
import check_ic_im_v1_3_delivery as gate  # noqa: E402
import prepare_ic_im_v1_3_marker as marker  # noqa: E402
import restore_ic_im_v1_3_ledger as restore  # noqa: E402


def signal(product: str) -> dict[str, object]:
    value: dict[str, object] = {
        "core_action": "HOLD",
        "momentum_action": "DECREASE",
        "grid_action": "HOLD",
        "put_action": "HOLD",
        "call_action": "HOLD",
        "total_units_current": 1.0,
        "total_units_target": 0.75,
        "momentum_current_weight": 1.0,
        "momentum_next_weight": 0.5,
        "momentum_score": 7.3,
        "momentum_abs20": -0.01,
        "momentum_120": -0.05,
        "score": 1.8,
        "grid_current": 0,
        "grid_target": 0,
        "put_current_contract": "P-OLD",
        "put_target_contract": "P-NEXT",
        "core_current": f"{product}2609",
        "core_target": f"{product}2609",
        "call_target_qty_normalized": 0,
    }
    if product == "IC":
        value.update(
            momentum_base_dd=-0.07,
            momentum_nav_defense=True,
            put_current_total_qty=14,
            put_target_total_qty=14,
            valuation_put_delta=0.25,
            mom120_floor_delta=0.5,
            core_put_target_delta=0.25,
            momentum_put_target_delta=0,
            total_put_target_delta=0.25,
        )
    else:
        value.update(
            momentum_volume_ratio=0.9,
            momentum_volume_pass=True,
            momentum_volume_placeholder=False,
            momentum_score_hot=False,
            core_put_current_qty_normalized=1.5,
            core_put_target_qty_normalized=1.5,
            momentum_put_current_qty_normalized=0.0,
            momentum_put_target_qty_normalized=0.75,
            total_put_current_qty_normalized=1.5,
            total_put_target_qty_normalized=2.25,
            core_put_current_contract="P-CORE-OLD",
            core_put_target_contract="P-CORE-NEXT",
            momentum_put_current_contract=None,
            momentum_put_target_contract="P-MOM-NEXT",
            valuation_puts_per_full_core=1,
            mom120_floor_puts_per_full_core=3,
            call_has_position=False,
        )
    return value


class ICIMV13DailyDigestTests(unittest.TestCase):
    def test_digest_names_release_and_explains_new_filters(self) -> None:
        payload = {
            "status": "ok",
            "strategy_revision": "r6",
            "build": "v1.3-test-r6",
            "publication_mode": "realtime",
            "market_date": "2026-09-03",
            "completed_day": "2026-09-02",
            "next_trade_day": "2026-09-04",
            "verified_day": "2026-09-02",
            "sequence": 7,
            "digest": "a" * 64,
            "advanced_sessions": 0,
            "signals": {"IC": signal("IC"), "IM": signal("IM")},
        }
        subject, body, actionable = digest.build_success(payload, "", "")
        self.assertTrue(actionable)
        self.assertIn("IC/IM 1.3-r6", subject)
        self.assertIn("6%防守门槛触发并减半", body)
        self.assertIn("Volume/MA160=0.900", body)
        self.assertIn("IC 1.3规则明确禁止卖Call", body)
        self.assertIn("合计2.25张", body)
        self.assertIn("动量0.75张 P-MOM-NEXT", body)

    def test_gate_and_success_marker_are_revision_mode_date_digest_scoped(self) -> None:
        prefix = gate.marker_prefix(date(2026, 9, 3), "realtime")
        payload = {
            "status": "ok",
            "strategy_revision": "r6",
            "publication_mode": "realtime",
            "market_date": "2026-09-03",
            "digest": "b" * 64,
        }
        name = marker.marker_name(payload)
        self.assertTrue(name.startswith(prefix))
        self.assertTrue(gate.marker_exists({"artifacts": [{"name": name}]}, prefix))

    def test_v13_ledger_requires_migration_record(self) -> None:
        good = io.BytesIO()
        with zipfile.ZipFile(good, "w") as archive:
            archive.writestr("latest.json", json.dumps({"ok": True}))
            archive.writestr("migration_record.json", json.dumps({"ok": True}))
            archive.writestr("journal/000000-2026-08-24.json", "{}")
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "state"
            restore.extract(good.getvalue(), destination)
            self.assertTrue((destination / "migration_record.json").is_file())

        bad = io.BytesIO()
        with zipfile.ZipFile(bad, "w") as archive:
            archive.writestr("latest.json", json.dumps({"ok": True}))
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, "migration_record"):
                restore.extract(bad.getvalue(), Path(tmp) / "state")


if __name__ == "__main__":
    unittest.main()
