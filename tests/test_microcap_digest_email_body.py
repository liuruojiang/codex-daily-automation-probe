from __future__ import annotations

import csv
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import build_microcap_realtime_digest as digest  # noqa: E402


class MicrocapDigestEmailBodyTests(unittest.TestCase):
    def write_csv(self, path: Path, row: dict[str, str]) -> None:
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(row))
            writer.writeheader()
            writer.writerow(row)

    def run_digest(
        self,
        tmp_path: Path,
        outputs: dict[str, str],
        csv_rows: dict[str, dict[str, str]] | None = None,
        exit_codes: dict[str, str] | None = None,
        subject_prefix: str = "",
    ) -> dict[str, object]:
        out_dir = tmp_path / "artifacts"
        argv = ["build_microcap_realtime_digest.py"]
        for version, output in outputs.items():
            result_path = tmp_path / f"{version.replace('.', '')}.txt"
            result_path.write_text(output, encoding="utf-8")
            argv += ["--result", f"{version}={result_path}"]
        for version, row in (csv_rows or {}).items():
            csv_path = tmp_path / f"{version.replace('.', '')}.csv"
            self.write_csv(csv_path, row)
            argv += ["--signal-csv", f"{version}={csv_path}"]
        argv += ["--out-dir", str(out_dir), "--planned", "09:30 Asia/Shanghai"]
        if subject_prefix:
            argv += ["--subject-prefix", subject_prefix]
        for version in outputs:
            argv += ["--exit-code", f"{version}={(exit_codes or {}).get(version, '0')}"]

        fixed_now = datetime(2026, 8, 7, 9, 35, tzinfo=digest.BJ)
        with (
            patch.object(sys, "argv", argv),
            patch.object(digest, "now_bj", return_value=fixed_now),
            patch.dict(os.environ, {"GITHUB_RUN_URL": "https://github.com/example/actions/runs/123"}),
        ):
            self.assertEqual(digest.main(), 0)
        return json.loads((out_dir / "metadata.json").read_text(encoding="utf-8"))

    def test_actionable_digest_is_compact_and_uses_version_correct_momentum_labels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            outputs = {
                "v2.0": "\n".join(
                    [
                        "realtime_signal",
                        "strategy_version: v2.0",
                        "snapshot_time: 2026-08-07 09:31:00+08:00",
                        "latest_anchor_trade_date: 2026-08-06",
                        "quote_trade_date: 2026-08-07",
                        "current_holding: cash",
                        "next_holding: cash",
                        "trade_state: hold",
                        "holding_trade_state: hold",
                        "scale_trade_state: hold_scale",
                        "current_execution_scale: 0.00",
                        "next_session_actionable_scale: 0.00",
                        "microcap_mom: +5.6597%",
                        "hedge_mom: -3.6763%",
                        "momentum_gap: +9.3360%",
                        "quote_coverage: 100/100",
                    ]
                ),
                "v2.3": "\n".join(
                    [
                        "realtime_signal",
                        "strategy_version: v2.3",
                        "snapshot_time: 2026-08-07 09:32:00+08:00",
                        "latest_anchor_trade_date: 2026-08-06",
                        "quote_trade_date: 2026-08-07",
                        "current_holding: cash",
                        "next_holding: cash",
                        "trade_state: hold",
                        "holding_trade_state: hold",
                        "scale_trade_state: hold_scale",
                        "current_execution_scale: 0.00",
                        "next_session_actionable_scale: 0.00",
                        "annualized_log_wls_score: +166.4220%",
                        "log_wls_r2: 0.5144",
                        "quote_coverage: 100/100",
                    ]
                ),
                "v2.5": "\n".join(
                    [
                        "realtime_signal",
                        "strategy_version: v2.5",
                        "snapshot_time: 2026-08-07 09:33:00+08:00",
                        "latest_anchor_trade_date: 2026-08-06",
                        "quote_trade_date: 2026-08-07",
                        "current_holding: cash",
                        "next_holding: long_microcap_top100",
                        "trade_state: open",
                        "holding_trade_state: enter",
                        "scale_trade_state: scale_up",
                        "current_execution_scale: 0.00",
                        "next_session_actionable_scale: 1.00",
                        "annualized_log_wls_score: +300.6818%",
                        "log_wls_r2: 0.7828",
                        "quote_coverage: 100/100",
                    ]
                ),
            }
            csv_rows = {
                "v2.0": {
                    "blocked_until_signal_reset": "True",
                    "overheat_metric": "0.3296301",
                    "overheat_threshold": "0.23",
                    "next_session_actionable_scale": "0.0",
                },
                "v2.3": {
                    "overheat_risk_off": "True",
                    "overheat_feature_value": "0.3177309",
                    "overheat_trigger_threshold": "0.26",
                    "overheat_recovery_threshold": "0.195",
                    "next_session_actionable_scale": "0.0",
                },
                "v2.5": {"next_session_actionable_scale": "1.0"},
            }
            meta = self.run_digest(tmp_path, outputs, csv_rows)

        body = str(meta["body"])
        self.assertEqual(meta["subject"], "[需操作] 微盘股 v2.0/v2.3/v2.5 日报 - 2026-08-07")
        self.assertIn("**v2.5 需要开仓；其他版本无需调仓。**", body)
        self.assertIn("空仓 → 微盘 Top100", body)
        self.assertIn("微盘 **+5.66%**；对冲 **-3.68%**；动量差 **+9.34%**", body)
        self.assertIn("对冲价差年化 WLS 得分 **+166.42%**；R² **0.514**", body)
        self.assertIn("微盘年化 WLS 得分 **+300.68%**；R² **0.783**", body)
        self.assertIn("**v2.0：**过热退出后锁定", body)
        self.assertIn("32.96%", body)
        self.assertIn("23.00%", body)
        self.assertIn("**v2.3：**过热风险关闭中", body)
        self.assertIn("31.77%", body)
        self.assertIn("19.50%", body)
        self.assertIn("[查看完整诊断与原始输出](https://github.com/example/actions/runs/123)", body)
        self.assertNotIn("原始实时信号输出", body)
        self.assertNotIn("脚本退出码", body)
        self.assertNotIn("strategy_version:", body)
        self.assertNotIn(str(tmp_path), body)
        self.assertFalse(meta.get("attachment"))

    def test_no_action_digest_uses_no_action_subject_and_stdout_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            meta = self.run_digest(
                Path(tmp),
                {
                    "v2.0": "\n".join(
                        [
                            "realtime_signal",
                            "strategy_version: v2.0",
                            "snapshot_time: 2026-08-07 09:31:00+08:00",
                            "latest_anchor_trade_date: 2026-08-06",
                            "quote_trade_date: 2026-08-07",
                            "current_holding: long_microcap_short_zz1000",
                            "next_holding: long_microcap_short_zz1000",
                            "trade_state: hold",
                            "holding_trade_state: hold",
                            "scale_trade_state: hold_scale",
                            "current_execution_scale: 0.72",
                            "microcap_mom: +4.6571%",
                            "hedge_mom: -4.1575%",
                            "momentum_gap: +8.8147%",
                        ]
                    )
                },
            )

        body = str(meta["body"])
        self.assertEqual(meta["subject"], "[无需操作] 微盘股 v2.0 日报 - 2026-08-07")
        self.assertIn("**所有版本均无需调仓。**", body)
        self.assertIn("微盘 Top100＋空头中证1000 → 微盘 Top100＋空头中证1000", body)
        self.assertIn("| v2.0 |", body)
        self.assertIn("| 0.72 |", body)
        self.assertIn("风险/数据异常：无", body)

    def test_member_rebalance_is_an_action_when_holding_and_scale_are_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            meta = self.run_digest(
                Path(tmp),
                {
                    "v2.0": "\n".join(
                        [
                            "realtime_signal",
                            "strategy_version: v2.0",
                            "snapshot_time: 2026-08-07 12:17:56+08:00",
                            "latest_anchor_trade_date: 2026-08-06",
                            "quote_trade_date: 2026-08-07",
                            "current_holding: long_microcap_short_zz1000",
                            "next_holding: long_microcap_short_zz1000",
                            "trade_state: hold",
                            "holding_trade_state: hold",
                            "scale_trade_state: hold_scale",
                            "current_execution_scale: 0.73",
                            "next_session_actionable_scale: 0.73",
                            "microcap_mom: +4.2612%",
                            "hedge_mom: -0.2173%",
                            "momentum_gap: +4.4784%",
                            "quote_coverage: 100/100",
                        ]
                    )
                },
                {
                    "v2.0": {
                        "member_rebalance_required": "True",
                        "member_rebalance_state": "rebalance",
                        "member_enter_count": "7",
                        "member_exit_count": "7",
                        "member_rebalance_label": "名单调仓（调入 7，调出 7）",
                    }
                },
            )

        body = str(meta["body"])
        self.assertEqual(meta["subject"], "[需操作] 微盘股 v2.0 日报 - 2026-08-07")
        self.assertIn("**v2.0 需要名单调仓（调入 7，调出 7）。**", body)
        self.assertIn("| 名单调仓（调入 7，调出 7） |", body)

    def test_scale_and_member_rebalances_are_both_visible(self) -> None:
        fields = {
            "current_holding": "long_microcap_short_zz1000",
            "next_holding": "long_microcap_short_zz1000",
            "holding_trade_state": "hold",
            "trade_state": "hold",
            "scale_trade_state": "rebalance_scale",
            "scale_trade_required": "True",
            "member_rebalance_required": "True",
            "member_enter_count": "3",
            "member_exit_count": "3",
        }

        self.assertEqual(
            digest.action_label({"status": "OK", "fields": fields}),
            "调整仓位；名单调仓（调入 3，调出 3）",
        )

    def test_corrected_dispatch_adds_visible_subject_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            meta = self.run_digest(
                Path(tmp),
                {
                    "v2.5": "\n".join(
                        [
                            "realtime_signal",
                            "strategy_version: v2.5",
                            "snapshot_time: 2026-08-07 15:05:00+08:00",
                            "latest_anchor_trade_date: 2026-08-06",
                            "quote_trade_date: 2026-08-07",
                            "current_holding: long_microcap_top100",
                            "next_holding: long_microcap_top100",
                            "trade_state: hold",
                        ]
                    )
                },
                subject_prefix="纠正版",
            )

        self.assertEqual(
            meta["subject"],
            "[纠正版][无需操作] 微盘股 v2.5 日报 - 2026-08-07",
        )

    def test_stale_anchor_uses_abnormal_subject_and_visible_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            meta = self.run_digest(
                Path(tmp),
                {
                    "v2.0": "\n".join(
                        [
                            "realtime_signal",
                            "strategy_version: v2.0",
                            "snapshot_time: 2026-05-20 14:52:59+08:00",
                            "latest_anchor_trade_date: 2026-05-15",
                            "quote_trade_date: 2026-05-20",
                            "current_holding: long_microcap_short_zz1000",
                            "next_holding: long_microcap_short_zz1000",
                            "trade_state: hold",
                        ]
                    )
                },
            )

        body = str(meta["body"])
        self.assertEqual(meta["subject"], "[异常] 微盘股 v2.0 日报 - 2026-08-07")
        self.assertIn("**存在异常版本，请勿执行异常版本信号。**", body)
        self.assertIn("**v2.0：**数据过期", body)
        self.assertIn("2026-05-15", body)
        self.assertIn("2026-05-19", body)
        self.assertNotIn("Digest status", body)

    def test_missing_required_holdings_is_abnormal_instead_of_no_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            meta = self.run_digest(
                Path(tmp),
                {
                    "v2.5": "\n".join(
                        [
                            "realtime_signal",
                            "strategy_version: v2.5",
                            "snapshot_time: 2026-08-07 09:33:00+08:00",
                            "latest_anchor_trade_date: 2026-08-06",
                            "quote_trade_date: 2026-08-07",
                            "annualized_log_wls_score: +300.6818%",
                        ]
                    )
                },
            )

        body = str(meta["body"])
        self.assertEqual(meta["subject"], "[异常] 微盘股 v2.5 日报 - 2026-08-07")
        self.assertIn("缺少必要信号字段", body)
        self.assertIn("current_holding", body)
        self.assertIn("next_holding", body)

    def test_preflight_refresh_failure_uses_refresh_reason(self) -> None:
        status, note = digest.classify_signal_output(
            "\n".join(
                [
                    "preflight_failed",
                    "refresh_exit_code: 1",
                    "reason: Top100 realtime state refresh failed, so the v2_0 realtime signal was not run.",
                    "refresh_log: microcap/realtime_state_refresh_result.txt",
                ]
            ),
            "unknown",
        )

        self.assertEqual(status, "FAILED")
        self.assertIn("state refresh failed", note)
        self.assertNotIn("marker is missing", note)


if __name__ == "__main__":
    unittest.main()
