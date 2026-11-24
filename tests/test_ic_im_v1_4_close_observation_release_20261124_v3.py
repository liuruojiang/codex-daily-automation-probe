"""Exact release inventory and source-proof compatibility for the dated repair."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_ic_im_v1_4_digest as digest
import verify_icim_source_release as verifier

OLD = "docs/ic_im_report_close_curve_20261124_v2_manifest.json"
NEW = "docs/ic_im_report_close_observation_20261124_v3_manifest.json"
RELEASE = "20261124-report-close-observation-v3"
COMMIT = "b" * 40


def proof(name):
    raw = (ROOT / name).read_bytes()
    m = json.loads(raw)
    return {"status": "verified", "release": m["release"], "strategy_sha": COMMIT,
            "manifest_sha256": hashlib.sha256(raw).hexdigest(), "source_sha256": m["files"],
            "support_sha256": m["support_files"], "verified_at": datetime.now(timezone.utc).isoformat()}


def test_new_release_changes_only_exact_reviewed_inventory():
    old, new = (json.loads((ROOT / name).read_bytes()) for name in (OLD, NEW))
    assert new["release"] == RELEASE and new["files"].keys() == old["files"].keys()
    assert {k for k in new["files"] if new["files"][k] != old["files"][k]} == {
        "poe_ic_im_mainline_v1_4_bot.py", "run_ic_im_v1_4_github_digest.py"}
    for key in ("support_files", "historical_exclusion_registry_sha256",
                "daily_gate_deselections", "daily_gate_outer_timeout_seconds"):
        assert new[key] == old[key]
    assert new["required_local_tests"] == old["required_local_tests"] + [
        "test_icim_close_failure_observation_20261124_v3.py"]
    assert all(new["fixture_sha256"][k] == v for k, v in old["fixture_sha256"].items())


def test_current_proof_cannot_borrow_old_manifest_or_drop_support(tmp_path):
    path = tmp_path / "proof.json"
    current = proof(NEW)
    path.write_text(json.dumps(current))
    assert digest.source_provenance(str(path), COMMIT, RELEASE) == current
    for bad in (proof(OLD), {**current, "support_sha256": {}},
                {**current, "manifest_sha256": "0" * 64}):
        path.write_text(json.dumps(bad))
        with pytest.raises(ValueError):
            digest.source_provenance(str(path), COMMIT, RELEASE)


def test_missing_new_manifest_refuses_old_fallback(tmp_path, monkeypatch):
    old_path = tmp_path / OLD
    old_path.parent.mkdir(parents=True)
    old_path.write_bytes((ROOT / OLD).read_bytes())
    def git(args, **kwargs):
        if args[-2:] == ["rev-parse", "HEAD"]:
            return COMMIT + "\n"
        if "ls-tree" in args:
            return NEW + "\n" + OLD + "\n"
        pytest.fail("new pinned manifest must reject before old read")
    monkeypatch.setattr(verifier.subprocess, "check_output", git)
    with pytest.raises(ValueError, match="pinned release manifest is missing"):
        verifier.verify(tmp_path, COMMIT)


# Exact native failure fixture captured from the real producer's source validation,
# missing-new-near guard, public exception boundary and product failure renderer.
NATIVE = "### IC 1.4 / 中证500\n\n⚠️ 当日信号警告：IC规则下一挂牌合约 IC2701 缺少有限正价，禁止跳到更远月份\n\n当日恐慌指数：未核验（本轮尚无已采集恐慌证据；失败兜底不另行联网）。\n\n9圈恐慌贪婪指数：暂未取得；仅供参考，不参与网格信号。\n\n该品种的新目标尚未确认；不会用NaN、陈旧行情或未重放账本推导替代目标。\n\n**部分数据可用；Put及完整组合目标尚未确认。**\n\nIC规则下一挂牌合约 IC2701 缺少有限正价，禁止跳到更远月份\n\n信号日：`2026-12-18`；状态：`close_observation_after_failure`。\n\n**2027交易日历采用国务院2026-11-05假期通知＋周末休市推导（用户批准）；调休周末不开市，尚未标为交易所年度公告确认。**\n\n最后已核验期货合约：`IC2609`；账本日：`2026-08-24`。\n\n已核验期货报价（仅行情观察）：\n\n- `IC2612`：6000.0。\n- `IC2703`：5800.0。\n- `IC2706`：5700.0。\n\n- 期限贴水（近月－远月）：N/A（IC2702：目标合约尚未实际挂牌）（仅行情观察）\n- 期限贴水（近月－近季）：N/A（IC2701：缺失或重复同日报价）（仅行情观察）\n- 期限贴水（近月－远季）：N/A（IC2701：缺失或重复同日报价）（仅行情观察）\n- 期现贴水（近月相对指数）：N/A（IC2701：缺失或重复同日报价）（仅行情观察）\n- 期现贴水（远月相对指数）：N/A（IC2702：目标合约尚未实际挂牌）（仅行情观察）\n- 期现贴水（近季度相对指数）：中证500 6100.00 − IC2703 5800.00 = +300.00点（+4.92%；距到期 91天；年化 +19.73%/年，非保证收益）；行情 2026-12-18，期货报价 independent fixture（仅行情观察）\n- 期现贴水（远季度相对指数）：中证500 6100.00 − IC2706 5700.00 = +400.00点（+6.56%；距到期 182天；年化 +13.15%/年，非保证收益）；行情 2026-12-18，期货报价 independent fixture（仅行情观察）\n- 贴水口径：正值为贴水，负值为升水。指数为当日收盘；期货为同日最后报价（个别可能早于15:00），不是结算价。（仅行情观察）\n- 贴水口径：诊断JSON中的 diagnostic_close_report_curve 为本次独立行情观察；diagnostic_tenor_spreads 仅为兼容记录，不作为新版贴水口径。\n\n完整目标：N/A；本次结果仅供研究诊断，不生成新执行计划，不写入正式账本。\n\n"


@pytest.mark.parametrize("retry_observation", [False, True])
def test_native_ordinary_failure_and_retry_observation_reach_both_email_formats(retry_observation):
    from html import unescape
    native = NATIVE
    if retry_observation:
        native = native.replace("**部分数据可用；Put及完整组合目标尚未确认。**\n\n",
            "**部分数据可用；Put及完整组合目标尚未确认。**\n\n"
            "本轮首次尝试的已核验行情；后续重试未取得新的合格观察，未与重试行情拼接。首次异常：")
    md, html = digest.failure_diagnostic_display(native)
    for rendered in (md, unescape(html)):
        assert "IC2701 缺少有限正价" in rendered
        assert rendered.count("期限贴水（") == 3
        assert rendered.count("期现贴水（") == 4
        assert "IC2703 5800.00" in rendered and "IC2706 5700.00" in rendered
        assert "+4.92%" in rendered and "+6.56%" in rendered
        assert "diagnostic_tenor_spreads 仅为兼容记录" in rendered
        assert "整体信号尚未确认" in rendered and "仅行情观察" in rendered
        if retry_observation:
            assert "本轮首次尝试" in rendered and "未与重试行情拼接" in rendered
