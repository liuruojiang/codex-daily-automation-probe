from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
import zipfile
from datetime import date
from pathlib import Path
from unittest import mock


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
            im_put_calendar_revision="execution_day_20260911_v1",
            put_monthly_reset_preview=True,
            option_monthly_reset_due=False,
            put_monthly_reset_execution_date="2026-09-18",
            put_reference_future="IM2609",
            put_reference_price_date="2026-09-17",
        )
    return value


class ICIMV13DailyDigestTests(unittest.TestCase):
    def test_required_restore_fails_when_no_seed_artifact_exists(self) -> None:
        argv = [
            "restore_ic_im_v1_3_ledger.py",
            "--state-dir", "state",
            "--repository", "owner/repo",
            "--token", "token",
            "--required",
        ]
        with mock.patch.object(sys, "argv", argv), mock.patch.object(
            restore, "fetch_latest", return_value=None
        ), mock.patch.object(restore, "write_output") as output:
            self.assertEqual(restore.main(), 1)
        output.assert_called_once_with(False)

    def test_legacy_seed_cutoff_excludes_post_fix_artifact(self) -> None:
        payload = {
            "artifacts": [
                {"id": 1, "name": restore.ARTIFACT_NAME, "created_at": "2026-09-04T07:52:39Z", "expired": False, "archive_download_url": "a"},
                {"id": 2, "name": restore.ARTIFACT_NAME, "created_at": "2026-09-07T10:03:16Z", "expired": False, "archive_download_url": "b"},
            ]
        }
        selected = restore.latest_artifact(
            payload, restore.ARTIFACT_NAME, "2026-09-07T00:00:00Z"
        )
        self.assertEqual(selected["id"], 1)

    def test_digest_names_release_and_explains_new_filters(self) -> None:
        payload = {
            "status": "ok",
            "strategy_revision": "r7",
            "build": "v1.3-test-r7",
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
        self.assertIn("IC/IM 1.3-r7", subject)
        self.assertIn("6%防守门槛触发并减半", body)
        self.assertIn("Volume/MA160=0.900", body)
        self.assertIn("IC 1.3规则明确禁止卖Call", body)
        self.assertIn("合计2.25张", body)
        self.assertIn("动量0.75张 P-MOM-NEXT", body)
        self.assertIn("独立动量Put按父规则数量×0.5×动量执行权重配置", body)
        self.assertIn("本次目标为0.75张 P-MOM-NEXT；合计目标2.25张", body)
        self.assertNotIn("动量袖和网格均不配期权", body)
        self.assertIn("月度Put维护预告：2026-09-18执行", body)
        self.assertIn("当前不锁定新合约", body)

    def test_digest_explains_execution_day_reference_contract_and_price_date(self) -> None:
        execution = signal("IM")
        execution.update(
            put_monthly_reset_preview=False,
            option_monthly_reset_due=True,
            put_monthly_reset_execution_date="2026-09-18",
            put_reference_future="IM2612",
            put_reference_price_date="2026-09-18",
        )
        payload = {
            "status": "ok",
            "strategy_revision": "r7",
            "build": "v1.3-test-r7",
            "publication_mode": "close_confirmed",
            "market_date": "2026-09-18",
            "completed_day": "2026-09-18",
            "next_trade_day": "2026-09-21",
            "verified_day": "2026-09-18",
            "sequence": 8,
            "digest": "c" * 64,
            "advanced_sessions": 1,
            "signals": {"IC": signal("IC"), "IM": execution},
        }
        _subject, body, _actionable = digest.build_success(payload, "", "")
        self.assertIn("月度Put执行日：2026-09-18", body)
        self.assertIn("参考期货IM2612，取价日2026-09-18", body)
        self.assertIn("当天按该参考期货价格重选Put并写入执行记录", body)

    def test_gate_and_success_marker_are_revision_mode_date_digest_scoped(self) -> None:
        prefix = gate.marker_prefix(date(2026, 9, 3), "realtime")
        payload = {
            "status": "ok",
            "strategy_revision": "r7",
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
