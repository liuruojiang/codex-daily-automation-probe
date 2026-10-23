"""Independent display-only references must not alter verified targets or dates."""
import copy

import pytest

from test_ic_im_v1_4_fear_sources import payload
import build_ic_im_v1_4_digest as digest


@pytest.mark.parametrize("missing", [False, True])
@pytest.mark.parametrize("pollution", [
    {"total_units_target": 99., "grid_target": .5, "grid_action": "ADD"},
    {"market_date": "2099-01-01", "product": "WRONG", "next_trade_date": "2099-01-02"},
    {"fear_source_warning": "OVERRIDE_WARNING", "fear_cross_validation": "matched", "fear_data_date": "2099-01-01"},
    {"put_target_total_qty": 999, "put_target_contract": "WRONG_PUT", "call_target_contract": "WRONG_CALL"},
])
def test_reference_pollution_cannot_change_text_html_or_fear_banner(monkeypatch, missing, pollution):
    clean = payload(monkeypatch, missing=missing)
    expected = (digest.build_success(clean, "", ""), digest.build_success_html(clean, ""),
                digest.fear_observation_text(clean))
    attacked = copy.deepcopy(clean)
    attacked["jiu_reference"].update(pollution)
    original = copy.deepcopy(attacked)
    actual = (digest.build_success(attacked, "", ""), digest.build_success_html(attacked, ""),
              digest.fear_observation_text(attacked))
    assert actual == expected
    assert attacked == original
    assert "9圈恐慌贪婪指数：2.28" in actual[0][1]
    if missing:
        assert "网格未确认" in actual[0][0]
        assert attacked["signals"]["IC"]["total_units_target"] is None


@pytest.mark.parametrize("reference", [None, [], "unavailable", 3])
def test_malformed_optional_reference_does_not_block_report(monkeypatch, reference):
    clean = payload(monkeypatch)
    clean.pop("jiu_reference")
    expected = (digest.build_success(clean, "", ""), digest.build_success_html(clean, ""))
    clean["jiu_reference"] = reference
    assert (digest.build_success(clean, "", ""), digest.build_success_html(clean, "")) == expected

