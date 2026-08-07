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
    STRATEGY_SHA = "a79d167231793c39ad15f61ec9fe4c86b05bfd97"

    def identity_fields(self, version: str) -> dict[str, str]:
        identities = {
            "v2.0": {
                "version": "2.0",
                "overlay_type": "volatility_overheat_exit_then_target_volatility_scaling",
                "overheat_enabled": "True",
                "overheat_window": "60",
                "overheat_threshold": "0.23",
                "overheat_require_signal_reset": "True",
                "target_vol": "0.15",
                "target_vol_window": "75",
                "max_leverage": "1.5",
                "fixed_hedge_ratio": "0.8",
            },
            "v2.3": {
                "version": "2.3",
                "strategy_version": "v2.3",
                "overlay_type": "spread_nav_log_wls_lb25_vol10_overheat",
                "signal_model": "spread_nav_log_wls_exp_halflife_2p5_lb25_r2gate0p08_signal1p0_exec0p8_vol10_overheat",
                "lookback": "25",
                "halflife": "2.5",
                "r2_entry_gate": "0.08",
                "execution_hedge_ratio": "0.8",
                "overheat_enabled": "True",
                "overheat_feature_window": "10",
                "overheat_trigger_threshold": "0.26",
                "overheat_recovery_threshold": "0.195",
                "target_vol_enabled": "False",
                "target_vol": "0.0",
                "target_vol_window": "0",
            },
            "v2.5": {
                "version": "2.5",
                "strategy_version": "v2.5",
                "overlay_type": "microcap_only_log_wls_threshold_no_target_vol",
                "signal_model": "microcap_only_log_wls_exp_halflife_3p0_lb17_entry46_exit25_no_targetvol",
                "execution_hedge_ratio": "0.0",
                "fixed_hedge_ratio": "0.0",
                "hedge_removed": "True",
                "lookback": "17",
                "halflife": "3.0",
                "entry_threshold": "0.46",
                "exit_threshold": "0.25",
                "overheat_enabled": "False",
                "target_vol_enabled": "False",
                "target_vol": "0.0",
                "target_vol_window": "0",
            },
        }
        return {
            **identities[version],
            "member_rebalance_required": "False",
            "member_rebalance_actionable": "False",
            "member_rebalance_official": "True",
        }

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
        strategy_sha: str = STRATEGY_SHA,
        signal_csv_paths: dict[str, Path] | None = None,
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
        for version, csv_path in (signal_csv_paths or {}).items():
            argv += ["--signal-csv", f"{version}={csv_path}"]
        argv += ["--out-dir", str(out_dir), "--planned", "09:30 Asia/Shanghai"]
        argv += ["--strategy-sha", strategy_sha]
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
                    **self.identity_fields("v2.0"),
                    "blocked_until_signal_reset": "True",
                    "overheat_metric": "0.3296301",
                    "overheat_threshold": "0.23",
                    "next_session_actionable_scale": "0.0",
                },
                "v2.3": {
                    **self.identity_fields("v2.3"),
                    "overheat_risk_off": "True",
                    "overheat_feature_value": "0.3177309",
                    "overheat_trigger_threshold": "0.26",
                    "overheat_recovery_threshold": "0.195",
                    "next_session_actionable_scale": "0.0",
                },
                "v2.5": {**self.identity_fields("v2.5"), "next_session_actionable_scale": "1.0"},
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
        self.assertIn(self.STRATEGY_SHA, body)
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
                {"v2.0": self.identity_fields("v2.0")},
            )

        body = str(meta["body"])
        self.assertEqual(meta["subject"], "[无需操作] 微盘股 v2.0 日报 - 2026-08-07")
        self.assertIn("**所有版本均无需调仓。**", body)
        self.assertIn("微盘 Top100＋空头中证1000 → 微盘 Top100＋空头中证1000", body)
        self.assertIn("| v2.0 |", body)
        self.assertIn("| 0.72 |", body)
        self.assertIn("风险/数据异常：无", body)

    def test_member_rebalance_is_actionable_only_when_official_and_includes_dates(self) -> None:
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
                        **self.identity_fields("v2.0"),
                        "member_rebalance_required": "True",
                        "member_rebalance_state": "rebalance",
                        "member_rebalance_actionable": "True",
                        "member_rebalance_official": "True",
                        "member_rebalance_signal_date": "2026-08-07",
                        "member_rebalance_execution_date": "2026-08-10",
                        "member_enter_count": "7",
                        "member_exit_count": "7",
                        "member_rebalance_label": "名单调仓（调入 7，调出 7）",
                    }
                },
            )

        body = str(meta["body"])
        self.assertEqual(meta["subject"], "[需操作] 微盘股 v2.0 日报 - 2026-08-07")
        dated_action = "名单调仓（调入 7，调出 7；信号日 2026-08-07，执行日 2026-08-10）"
        self.assertIn(f"**v2.0 需要{dated_action}。**", body)
        self.assertIn(f"| {dated_action} |", body)
        self.assertLess(body.index("**v2.0：微盘 Top100＋空头中证1000 → 微盘 Top100＋空头中证1000"), body.index(dated_action))

    def test_historical_member_counts_are_not_actionable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            meta = self.run_digest(
                Path(tmp),
                {
                    "v2.0": "\n".join(
                        [
                            "realtime_signal",
                            "strategy_version: v2.0",
                            "snapshot_time: 2026-08-07 09:33:00+08:00",
                            "latest_anchor_trade_date: 2026-08-06",
                            "quote_trade_date: 2026-08-07",
                            "current_holding: long_microcap_short_zz1000",
                            "next_holding: long_microcap_short_zz1000",
                            "holding_trade_state: hold",
                            "trade_state: hold",
                            "scale_trade_state: hold_scale",
                            "next_session_actionable_scale: 0.73",
                        ]
                    )
                },
                {
                    "v2.0": {
                        **self.identity_fields("v2.0"),
                        "member_rebalance_required": "True",
                        "member_rebalance_actionable": "False",
                        "member_rebalance_official": "True",
                        "member_enter_count": "7",
                        "member_exit_count": "7",
                    }
                },
            )

        self.assertEqual(meta["subject"], "[无需操作] 微盘股 v2.0 日报 - 2026-08-07")
        self.assertIn("**所有版本均无需调仓。**", str(meta["body"]))
        self.assertNotIn("名单调仓", str(meta["body"]))

    def test_intraday_member_preview_is_not_actionable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            meta = self.run_digest(
                Path(tmp),
                {
                    "v2.0": "\n".join(
                        [
                            "realtime_signal",
                            "strategy_version: v2.0",
                            "snapshot_time: 2026-08-07 09:33:00+08:00",
                            "latest_anchor_trade_date: 2026-08-06",
                            "quote_trade_date: 2026-08-07",
                            "current_holding: long_microcap_short_zz1000",
                            "next_holding: long_microcap_short_zz1000",
                            "holding_trade_state: hold",
                            "trade_state: hold",
                            "scale_trade_state: hold_scale",
                            "next_session_actionable_scale: 0.73",
                        ]
                    )
                },
                {
                    "v2.0": {
                        **self.identity_fields("v2.0"),
                        "member_rebalance_required": "True",
                        "member_rebalance_actionable": "False",
                        "member_rebalance_official": "False",
                        "member_rebalance_signal_date": "2026-08-07",
                        "member_rebalance_execution_date": "2026-08-10",
                        "member_enter_count": "7",
                        "member_exit_count": "7",
                    }
                },
            )

        self.assertEqual(meta["subject"], "[无需操作] 微盘股 v2.0 日报 - 2026-08-07")
        self.assertIn("**所有版本均无需调仓。**", str(meta["body"]))
        self.assertNotIn("名单调仓", str(meta["body"]))

    def test_scale_and_member_rebalances_are_both_visible(self) -> None:
        fields = {
            "current_holding": "long_microcap_short_zz1000",
            "next_holding": "long_microcap_short_zz1000",
            "holding_trade_state": "hold",
            "trade_state": "hold",
            "scale_trade_state": "rebalance_scale",
            "scale_trade_required": "True",
            "member_rebalance_required": "True",
            "member_rebalance_actionable": "True",
            "member_rebalance_official": "True",
            "member_rebalance_signal_date": "2026-08-07",
            "member_rebalance_execution_date": "2026-08-10",
            "member_enter_count": "3",
            "member_exit_count": "3",
        }

        self.assertEqual(
            digest.action_label({"status": "OK", "fields": fields}),
            "调整仓位；名单调仓（调入 3，调出 3；信号日 2026-08-07，执行日 2026-08-10）",
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
                {"v2.5": self.identity_fields("v2.5")},
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
                {"v2.5": self.identity_fields("v2.5")},
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

    def test_legacy_v20_and_v23_production_identities_fail_closed(self) -> None:
        outputs = {
            version: "\n".join(
                [
                    "realtime_signal",
                    f"strategy_version: {version}",
                    "snapshot_time: 2026-08-07 09:33:00+08:00",
                    "latest_anchor_trade_date: 2026-08-06",
                    "quote_trade_date: 2026-08-07",
                    "current_holding: cash",
                    "next_holding: long_microcap_short_zz1000",
                    "trade_state: open",
                    "next_session_actionable_scale: 1.0",
                ]
            )
            for version in ("v2.0", "v2.3")
        }
        legacy_rows = {
            "v2.0": {
                "version": "2.0",
                "overlay_type": "standalone_target_vol_overlay",
                "target_vol": "0.25",
                "target_vol_window": "60",
                "max_leverage": "1.5",
            },
            "v2.3": {
                "strategy_version": "v2.3",
                "signal_model": "spread_nav_log_wls_exp_halflife_3p0_lb17_signal1p0_exec0p8",
                "lookback": "17",
                "halflife": "3.0",
                "target_vol": "0.15",
                "target_vol_window": "60",
            },
        }

        with tempfile.TemporaryDirectory() as tmp:
            meta = self.run_digest(Path(tmp), outputs, legacy_rows)

        body = str(meta["body"])
        self.assertEqual(meta["subject"], "[异常] 微盘股 v2.0/v2.3 日报 - 2026-08-07")
        self.assertIn("策略身份不匹配", body)
        self.assertIn("v2.0：持仓与下一交易日可执行仓位不可用", body)
        self.assertIn("v2.3：持仓与下一交易日可执行仓位不可用", body)
        self.assertNotIn("需要开仓", body)
        self.assertNotIn("空仓 → 微盘 Top100＋空头中证1000", body)

    def test_correct_cash_identities_pass_and_render_holdings_before_action_sentence(self) -> None:
        outputs = {
            version: "\n".join(
                [
                    "realtime_signal",
                    f"strategy_version: {version}",
                    "snapshot_time: 2026-08-07 09:33:00+08:00",
                    "latest_anchor_trade_date: 2026-08-06",
                    "quote_trade_date: 2026-08-07",
                    "current_holding: cash",
                    "next_holding: cash",
                    "trade_state: hold",
                    "holding_trade_state: hold",
                    "scale_trade_state: hold_scale",
                    "next_session_actionable_scale: 0.0",
                ]
            )
            for version in ("v2.0", "v2.3", "v2.5")
        }
        csv_rows = {version: self.identity_fields(version) for version in outputs}

        with tempfile.TemporaryDirectory() as tmp:
            meta = self.run_digest(Path(tmp), outputs, csv_rows)

        body = str(meta["body"])
        self.assertEqual(meta["subject"], "[无需操作] 微盘股 v2.0/v2.3/v2.5 日报 - 2026-08-07")
        holdings = "**v2.0：空仓 → 空仓，下一交易日可执行仓位 0.00；v2.3：空仓 → 空仓，下一交易日可执行仓位 0.00；v2.5：空仓 → 空仓，下一交易日可执行仓位 0.00。**"
        action = "**所有版本均无需调仓。**"
        self.assertIn(holdings, body)
        self.assertIn(action, body)
        self.assertLess(body.index(holdings), body.index(action))

    def test_stdout_identity_without_final_signal_csv_fails_closed(self) -> None:
        identity_lines = [f"{key}: {value}" for key, value in self.identity_fields("v2.5").items()]
        output = "\n".join(
            [
                "realtime_signal",
                "snapshot_time: 2026-08-07 09:33:00+08:00",
                "latest_anchor_trade_date: 2026-08-06",
                "quote_trade_date: 2026-08-07",
                "current_holding: cash",
                "next_holding: long_microcap_top100",
                "trade_state: open",
                "next_session_actionable_scale: 1.0",
                *identity_lines,
            ]
        )

        with tempfile.TemporaryDirectory() as tmp:
            meta = self.run_digest(Path(tmp), {"v2.5": output})

        body = str(meta["body"])
        self.assertEqual(meta["subject"], "[异常] 微盘股 v2.5 日报 - 2026-08-07")
        self.assertIn("最终实时信号 CSV", body)
        self.assertNotIn("需要开仓", body)
        self.assertNotIn("空仓 → 微盘 Top100", body)

    def test_missing_and_empty_final_signal_csvs_fail_closed(self) -> None:
        output = "\n".join(
            [
                "realtime_signal",
                "snapshot_time: 2026-08-07 09:33:00+08:00",
                "latest_anchor_trade_date: 2026-08-06",
                "quote_trade_date: 2026-08-07",
                "current_holding: cash",
                "next_holding: long_microcap_top100",
                "trade_state: open",
            ]
        )

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            empty_csv = tmp_path / "empty.csv"
            empty_csv.write_text("", encoding="utf-8")
            unreadable_csv = tmp_path / "csv-is-directory"
            unreadable_csv.mkdir()
            cases = {
                "missing": tmp_path / "missing.csv",
                "empty": empty_csv,
                "unreadable": unreadable_csv,
            }
            for label, csv_path in cases.items():
                with self.subTest(label=label):
                    case_dir = tmp_path / label
                    case_dir.mkdir()
                    try:
                        meta = self.run_digest(
                            case_dir,
                            {"v2.5": output},
                            signal_csv_paths={"v2.5": csv_path},
                        )
                    except OSError as exc:
                        self.fail(f"unreadable final CSV must fail closed in the digest: {exc}")
                    self.assertEqual(meta["subject"], "[异常] 微盘股 v2.5 日报 - 2026-08-07")
                    self.assertIn("最终实时信号 CSV", str(meta["body"]))
                    self.assertNotIn("需要开仓", str(meta["body"]))

    def test_v23_and_v25_reject_contradictory_csv_version_and_overlay(self) -> None:
        holding_by_version = {
            "v2.3": "long_microcap_short_zz1000",
            "v2.5": "long_microcap_top100",
        }
        for version, next_holding in holding_by_version.items():
            with self.subTest(version=version):
                output = "\n".join(
                    [
                        "realtime_signal",
                        f"strategy_version: {version}",
                        "snapshot_time: 2026-08-07 09:33:00+08:00",
                        "latest_anchor_trade_date: 2026-08-06",
                        "quote_trade_date: 2026-08-07",
                        "current_holding: cash",
                        f"next_holding: {next_holding}",
                        "trade_state: open",
                        "next_session_actionable_scale: 1.0",
                    ]
                )
                contradictory = {
                    **self.identity_fields(version),
                    "version": "9.9",
                    "overlay_type": "legacy_wrong_overlay",
                }
                with tempfile.TemporaryDirectory() as tmp:
                    meta = self.run_digest(Path(tmp), {version: output}, {version: contradictory})

                body = str(meta["body"])
                self.assertEqual(meta["subject"], f"[异常] 微盘股 {version} 日报 - 2026-08-07")
                self.assertIn("策略身份不匹配", body)
                self.assertIn("version expected", body)
                self.assertIn("overlay_type expected", body)
                self.assertNotIn("需要开仓", body)

    def test_missing_and_malformed_member_booleans_fail_closed(self) -> None:
        output = "\n".join(
            [
                "realtime_signal",
                "strategy_version: v2.5",
                "snapshot_time: 2026-08-07 09:33:00+08:00",
                "latest_anchor_trade_date: 2026-08-06",
                "quote_trade_date: 2026-08-07",
                "current_holding: cash",
                "next_holding: long_microcap_top100",
                "trade_state: open",
                "next_session_actionable_scale: 1.0",
            ]
        )
        boolean_fields = (
            "member_rebalance_required",
            "member_rebalance_actionable",
            "member_rebalance_official",
        )
        cases: list[tuple[str, dict[str, str]]] = []
        for field in boolean_fields:
            missing = self.identity_fields("v2.5")
            missing.pop(field)
            cases.append((f"missing-{field}", missing))
            malformed = self.identity_fields("v2.5")
            malformed[field] = "sometimes"
            cases.append((f"malformed-{field}", malformed))
        required_without_actionable = self.identity_fields("v2.5")
        required_without_actionable["member_rebalance_required"] = "True"
        required_without_actionable.pop("member_rebalance_actionable")
        cases.append(("required-true-missing-actionable", required_without_actionable))

        for label, csv_row in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                meta = self.run_digest(Path(tmp), {"v2.5": output}, {"v2.5": csv_row})

            body = str(meta["body"])
            self.assertEqual(meta["subject"], "[异常] 微盘股 v2.5 日报 - 2026-08-07")
            self.assertIn("成员调仓契约无效", body)
            self.assertNotIn("需要开仓", body)

    def test_inconsistent_member_contract_and_action_dates_fail_closed(self) -> None:
        output = "\n".join(
            [
                "realtime_signal",
                "strategy_version: v2.5",
                "snapshot_time: 2026-08-07 09:33:00+08:00",
                "latest_anchor_trade_date: 2026-08-06",
                "quote_trade_date: 2026-08-07",
                "current_holding: cash",
                "next_holding: long_microcap_top100",
                "trade_state: open",
                "next_session_actionable_scale: 1.0",
            ]
        )
        cases = {
            "arbitrary-dates": {
                "member_rebalance_required": "True",
                "member_rebalance_actionable": "True",
                "member_rebalance_official": "True",
                "member_rebalance_signal_date": "next Tuesday",
                "member_rebalance_execution_date": "eventually",
            },
            "reversed-dates": {
                "member_rebalance_required": "True",
                "member_rebalance_actionable": "True",
                "member_rebalance_official": "True",
                "member_rebalance_signal_date": "2026-08-10",
                "member_rebalance_execution_date": "2026-08-07",
            },
            "required-false-actionable-true": {
                "member_rebalance_required": "False",
                "member_rebalance_actionable": "True",
                "member_rebalance_official": "True",
                "member_rebalance_signal_date": "2026-08-07",
                "member_rebalance_execution_date": "2026-08-10",
            },
            "official-false-actionable-true": {
                "member_rebalance_required": "True",
                "member_rebalance_actionable": "True",
                "member_rebalance_official": "False",
                "member_rebalance_signal_date": "2026-08-07",
                "member_rebalance_execution_date": "2026-08-10",
            },
        }

        for label, overrides in cases.items():
            csv_row = {**self.identity_fields("v2.5"), **overrides}
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                meta = self.run_digest(Path(tmp), {"v2.5": output}, {"v2.5": csv_row})

            body = str(meta["body"])
            self.assertEqual(meta["subject"], "[异常] 微盘股 v2.5 日报 - 2026-08-07")
            self.assertIn("成员调仓契约无效", body)
            self.assertNotIn("需要开仓", body)

    def test_untrusted_values_cannot_inject_markdown_structure(self) -> None:
        holding = "rogue\n## INJECT-HOLDING\n**break-holding**"
        csv_row = {
            **self.identity_fields("v2.0"),
            "current_holding": holding,
            "next_holding": holding,
            "next_session_actionable_scale": "0.73",
            "member_rebalance_required": "True",
            "member_rebalance_actionable": "True",
            "member_rebalance_official": "True",
            "member_rebalance_signal_date": "2026-08-07",
            "member_rebalance_execution_date": "2026-08-10",
            "member_enter_count": "1",
            "member_exit_count": "1",
            "member_rebalance_label": "名单调仓\n## INJECT-MEMBER\n**break-member** [click](https://evil.invalid)",
            "snapshot_time": "2026-08-07 09:33\n## INJECT-SNAPSHOT\n**break-snapshot**",
            "quote_coverage": "100/100\n## INJECT-COVERAGE\n**break-coverage**",
        }
        output = "\n".join(
            [
                "realtime_signal",
                "strategy_version: v2.0",
                "latest_anchor_trade_date: 2026-08-06",
                "quote_trade_date: 2026-08-07",
                "current_holding: cash",
                "next_holding: cash",
                "trade_state: hold",
                "holding_trade_state: hold",
                "scale_trade_state: hold_scale",
            ]
        )
        strategy_sha = "abc123\n## INJECT-SHA\n**break-sha**"

        with tempfile.TemporaryDirectory() as tmp:
            meta = self.run_digest(
                Path(tmp),
                {"v2.0": output},
                {"v2.0": csv_row},
                strategy_sha=strategy_sha,
            )

        body = str(meta["body"])
        headings = [line for line in body.splitlines() if line.startswith("## ")]
        self.assertEqual(headings, ["## 今日结论", "## 需要关注"])
        self.assertTrue(body.splitlines()[2].startswith("**") and body.splitlines()[2].endswith("**"))
        self.assertTrue(body.splitlines()[4].startswith("**") and body.splitlines()[4].endswith("**"))
        for marker in ("holding", "member", "snapshot", "coverage", "sha"):
            self.assertNotIn(f"**break-{marker}**", body)
            self.assertIn(f"\\*\\*break-{marker}\\*\\*", body)
        self.assertNotIn("[click](https://evil.invalid)", body)
        self.assertIn("\\[click\\](https://evil.invalid)", body)

    def test_invalid_member_date_cannot_inject_error_markdown(self) -> None:
        csv_row = {
            **self.identity_fields("v2.5"),
            "member_rebalance_required": "True",
            "member_rebalance_actionable": "True",
            "member_rebalance_official": "True",
            "member_rebalance_signal_date": "bad\n## INJECT-DATE\n**break-date**",
            "member_rebalance_execution_date": "2026-08-10",
        }
        output = "\n".join(
            [
                "realtime_signal",
                "strategy_version: v2.5",
                "snapshot_time: 2026-08-07 09:33:00+08:00",
                "latest_anchor_trade_date: 2026-08-06",
                "quote_trade_date: 2026-08-07",
                "current_holding: cash",
                "next_holding: long_microcap_top100",
                "trade_state: open",
            ]
        )

        with tempfile.TemporaryDirectory() as tmp:
            meta = self.run_digest(Path(tmp), {"v2.5": output}, {"v2.5": csv_row})

        body = str(meta["body"])
        headings = [line for line in body.splitlines() if line.startswith("## ")]
        self.assertEqual(headings, ["## 今日结论", "## 需要关注"])
        self.assertNotIn("**break-date**", body)
        self.assertIn("\\*\\*break-date\\*\\*", body)

    def test_identity_mismatch_cannot_inject_abnormal_warning_markdown(self) -> None:
        csv_row = {
            **self.identity_fields("v2.5"),
            "version": "9.9\n## INJECT-IDENTITY\n**break-identity**",
        }
        output = "\n".join(
            [
                "realtime_signal",
                "strategy_version: v2.5",
                "snapshot_time: 2026-08-07 09:33:00+08:00",
                "latest_anchor_trade_date: 2026-08-06",
                "quote_trade_date: 2026-08-07",
                "current_holding: cash",
                "next_holding: long_microcap_top100",
                "trade_state: open",
            ]
        )

        with tempfile.TemporaryDirectory() as tmp:
            meta = self.run_digest(Path(tmp), {"v2.5": output}, {"v2.5": csv_row})

        body = str(meta["body"])
        headings = [line for line in body.splitlines() if line.startswith("## ")]
        self.assertEqual(meta["subject"], "[异常] 微盘股 v2.5 日报 - 2026-08-07")
        self.assertEqual(headings, ["## 今日结论", "## 需要关注"])
        self.assertNotIn("**break-identity**", body)
        self.assertIn("\\*\\*break-identity\\*\\*", body)


if __name__ == "__main__":
    unittest.main()
