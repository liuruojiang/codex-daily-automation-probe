import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_ic_im_v1_3_digest as digest


def test_legacy_and_missing_are_explicit_proxies():
    text = digest.valuation_disclosure({})
    assert "代理估值／非当日真实PE、PB" in text
    assert "2026-08-14" in text
    assert "未收到/未通过校验" in text


def test_stale_reason_and_real_date_visible_in_html():
    signal = {"valuation_provenance": {"mode": "proxy", "real_data_date": "2026-09-07",
              "reason": "VIP数据日期与信号日不一致", "proxy_anchor_date": "2026-08-14"}}
    page = digest.build_success_html({"signals": {"IC": signal, "IM": signal}}, "")
    assert "估值数据来源" in page
    assert "VIP数据日期与信号日不一致" in page
    assert "2026-09-07" in page and "2026-08-14" in page
    assert page.index("估值数据来源") < page.index("IC / 中证500")


def test_actual_inputs_are_not_claimed_to_refresh_other_factors():
    signal = {"valuation_provenance": {"mode": "vip_actual", "real_data_date": "2026-09-08"}}
    text = digest.valuation_disclosure(signal)
    assert "真实PE/PB：乐咕VIP" in text
    assert "国债收益率、股息和相对估值阈值沿用原冻结口径" in text
    assert "代理估值" not in text


def test_bond_actual_and_fallback_visible_without_claiming_fresh_dividends():
    for mode in ("official_actual", "frozen_fallback"):
        p = {"mode": "vip_actual", "real_data_date": "2026-09-08", "gov10y": {
            "mode": mode, "yield_decimal": .016798, "used_date": "2026-09-08",
            "reason": "模拟来源失败", "observed_date": "2026-09-07"}}
        text = digest.valuation_disclosure({"valuation_provenance": p})
        assert "1.6798%" in text
        assert "股息和相对估值阈值沿用原冻结口径" in text
        if mode == "frozen_fallback":
            assert "非当日利率" in text and "模拟来源失败" in text
        else:
            assert "国债利率：中债官方10年期" in text
