from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_ic_im_digest as digest  # noqa: E402
import check_ic_im_delivery as gate  # noqa: E402
import restore_ic_im_ledger as restore  # noqa: E402


def product_signal(product: str, action: str = "HOLD") -> dict[str, object]:
    signal: dict[str, object] = {
        "core_action": "HOLD",
        "momentum_action": action,
        "grid_action": "HOLD",
        "put_action": "HOLD",
        "call_action": "HOLD",
        "total_units_current": 1.0,
        "total_units_target": 1.0,
        "momentum_current_weight": 1.0,
        "momentum_next_weight": 1.0,
        "grid_current": 0,
        "grid_target": 0,
        "put_current_contract": "510500P2612M07500" if product == "IC" else "MO2612-P-7200",
        "put_target_contract": "510500P2612M07500" if product == "IC" else "MO2612-P-7200",
        "call_target_qty_normalized": 0,
    }
    if product == "IC":
        signal.update(put_current_total_qty=14, put_target_total_qty=14)
    else:
        signal.update(
            core_put_current_qty_normalized=1.5,
            core_put_target_qty_normalized=1.5,
            call_has_position=False,
        )
    return signal


class ICIMDigestTests(unittest.TestCase):
    def test_success_digest_has_close_confirmed_subject_and_actions(self) -> None:
        payload = {
            "status": "ok",
            "build": "v1.2-test",
            "completed_day": "2026-08-26",
            "next_trade_day": "2026-08-27",
            "verified_day": "2026-08-26",
            "sequence": 2,
            "digest": "abcdef1234567890",
            "advanced_sessions": 1,
            "signals": {
                "IC": product_signal("IC"),
                "IM": product_signal("IM", "TURN_OFF"),
            },
        }
        subject, body, actionable = digest.build_success(
            payload, "https://github.com/example/actions/runs/1", ""
        )
        self.assertTrue(actionable)
        self.assertEqual(subject, "[收盘确认][需调整] IC/IM 1.2 日报 - 2026-08-26")
        self.assertIn("IM | 1 → 1", body)
        self.assertIn("动量袖 TURN_OFF", body)
        self.assertIn("研究审计信号", body)

    def test_realtime_digest_is_provisional_and_uses_market_date(self) -> None:
        payload = {
            "status": "ok",
            "publication_mode": "realtime",
            "build": "v1.2-test",
            "market_date": "2026-08-26",
            "completed_day": "2026-08-25",
            "next_trade_day": "2026-08-27",
            "verified_day": "2026-08-25",
            "sequence": 1,
            "digest": "abcdef1234567890",
            "advanced_sessions": 0,
            "signals": {
                "IC": product_signal("IC"),
                "IM": product_signal("IM", "TURN_OFF"),
            },
        }
        subject, body, actionable = digest.build_success(payload, "", "")
        self.assertTrue(actionable)
        self.assertEqual(
            subject,
            "[盘中实时][预估需调整] IC/IM 1.2 日报 - 2026-08-26",
        )
        self.assertIn("等待收盘确认", body)
        self.assertIn("盘中值并未写入账本", body)

        html_body = digest.build_success_html(payload, "https://github.com/example/run")
        self.assertIn("<!doctype html>", html_body.lower())
        self.assertIn("IC / 中证500", html_body)
        self.assertIn("IM / 中证1000", html_body)
        self.assertIn("盘中结果会随行情变化", html_body)
        self.assertIn("动量：关闭", html_body)
        self.assertNotIn("| 品种 |", html_body)

    def test_success_html_escapes_untrusted_signal_values(self) -> None:
        payload = {
            "status": "ok",
            "publication_mode": "realtime",
            "market_date": "2026-08-26",
            "completed_day": "2026-08-25",
            "next_trade_day": "2026-08-27",
            "verified_day": "2026-08-25",
            "signals": {
                "IC": {**product_signal("IC"), "core_current": "<script>alert(1)</script>"},
                "IM": product_signal("IM"),
            },
        }
        html_body = digest.build_success_html(payload, "https://example.com/?a=1&b=2")
        self.assertNotIn("<script>", html_body)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html_body)
        self.assertIn("a=1&amp;b=2", html_body)

    def test_wait_iv_is_monitoring_state_not_actionable_change(self) -> None:
        payload = {
            "status": "ok",
            "publication_mode": "close_confirmed",
            "completed_day": "2026-08-26",
            "next_trade_day": "2026-08-27",
            "verified_day": "2026-08-26",
            "signals": {
                "IC": product_signal("IC"),
                "IM": {**product_signal("IM"), "call_action": "WAIT_IV"},
            },
        }
        subject, _, actionable = digest.build_success(payload, "", "")
        html_body = digest.build_success_html(payload, "")
        self.assertFalse(actionable)
        self.assertIn("[无需调整]", subject)
        self.assertIn("等待IV条件（无需操作）", html_body)
        self.assertIn("维持现状", html_body)

    def test_failure_html_is_readable_and_escapes_error(self) -> None:
        html_body = digest.build_failure_html(
            {
                "publication_mode": "realtime",
                "error_type": "RuntimeError",
                "error": "bad <source>",
            },
            "",
        )
        self.assertIn("信号生成失败", html_body)
        self.assertIn("请勿依据旧邮件调整", html_body)
        self.assertIn("bad &lt;source&gt;", html_body)

    def test_failure_digest_blocks_old_signal_use(self) -> None:
        subject, body = digest.build_failure(
            {
                "status": "failed",
                "generated_at": "2026-08-26T17:30:00+08:00",
                "build": "v1.2-test",
                "error_type": "RuntimeError",
                "error": "official data unavailable",
            },
            "",
            "",
        )
        self.assertEqual(
            subject, "[异常][收盘确认] IC/IM 1.2 日报 - 2026-08-26"
        )
        self.assertIn("请勿依据旧邮件调整", body)
        self.assertIn("没有因本次失败而跳日", body)

    def test_delivery_marker_uses_beijing_date(self) -> None:
        day = gate.delivery_date(datetime(2026, 8, 26, 9, 0, tzinfo=timezone.utc))
        self.assertEqual(
            gate.marker_name(day),
            "ic-im-v1-2-realtime-digest-delivered-2026-08-26",
        )

    def test_latest_ledger_artifact_ignores_expired(self) -> None:
        payload = {
            "artifacts": [
                {"id": 1, "name": restore.ARTIFACT_NAME, "expired": True, "created_at": "2026-08-27", "archive_download_url": "x"},
                {"id": 2, "name": restore.ARTIFACT_NAME, "expired": False, "created_at": "2026-08-26", "archive_download_url": "y"},
            ]
        }
        self.assertEqual(restore.latest_artifact(payload)["id"], 2)

    def test_artifact_redirect_strips_auth_only_when_origin_changes(self) -> None:
        handler = restore.StripCrossOriginAuthRedirectHandler()
        request = restore.api_request(
            "https://api.github.com/repos/example/repo/actions/artifacts/1/zip",
            "secret-token",
        )
        cross_origin = handler.redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "https://signed-results.example.net/archive.zip?sig=abc",
        )
        self.assertIsNotNone(cross_origin)
        self.assertIsNone(cross_origin.get_header("Authorization"))
        self.assertIsNone(cross_origin.get_header("X-GitHub-Api-Version"))

        same_origin = handler.redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "https://api.github.com/redirected",
        )
        self.assertEqual(same_origin.get_header("Authorization"), "Bearer secret-token")

    def test_ledger_extract_rejects_path_traversal(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("../latest.json", "{}")
        with self.assertRaisesRegex(RuntimeError, "unsafe"):
            restore.safe_members(buffer.getvalue())

    def test_ledger_extract_accepts_only_latest_and_journal(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("latest.json", json.dumps({"ok": True}))
            archive.writestr("journal/000000-2026-08-24.json", "{}")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "state"
            restore.extract(buffer.getvalue(), root)
            self.assertTrue((root / "latest.json").is_file())
            self.assertTrue((root / "journal" / "000000-2026-08-24.json").is_file())


if __name__ == "__main__":
    unittest.main()
