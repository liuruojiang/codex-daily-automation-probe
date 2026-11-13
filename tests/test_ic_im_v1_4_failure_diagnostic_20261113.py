"""Native failure diagnostics preserve evidence without manufacturing signals."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("failure_diagnostic_renderer_under_test", ROOT / "scripts/build_ic_im_v1_4_digest.py")
renderer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(renderer)


def make_artifact(tmp_path, query="### IC\n报价缺项\n### IM\n保留已取得的逐腿信息\n"):
    generated = "2026-11-13T20:03:18+08:00"
    text = ("# 本次 IC/IM 失败诊断\n\n生成时间：" + generated
            + "\n目标行情日：2026-11-13\n模式：close\n\n" + query)
    path = tmp_path / "diagnostic_report.md"
    path.write_text(text, encoding="utf-8")
    raw = path.read_bytes()
    payload = {"status": "failed", "strategy_revision": "r1", "market_date": "2026-11-13",
               "generated_at": generated, "publication_mode": "close_confirmed",
               "diagnostic_report_contract": "icim_failure_diagnostic_v1",
               "diagnostic_report_file": "diagnostic_report.md",
               "diagnostic_report_sha256": hashlib.sha256(raw).hexdigest(),
               "diagnostic_report_market_date": "2026-11-13",
               "diagnostic_report_generated_at": generated, "diagnostic_report_mode": "close"}
    proof = {"status": "verified", "strategy_sha": "a" * 40, "release": "20261113-held-put-close-v1",
             "source_sha256": {"run_ic_im_v1_4_github_digest.py": "b" * 64}}
    return path, payload, proof


def test_verified_original_diagnostic_is_preserved_without_signal_parsing(tmp_path):
    path, payload, proof = make_artifact(tmp_path)
    before = copy.deepcopy(payload)
    report, text = renderer.verified_failure_diagnostic(tmp_path / "result.json", payload, proof)
    assert Path(report) == path.resolve()
    assert text == path.read_bytes().decode("utf-8")
    body, html = renderer.failure_diagnostic_display(text)
    assert "IM" in body and "IM" in html
    assert "整体信号尚未确认" in body and "整体信号尚未确认" in html
    assert payload == before and "signals" not in payload


@pytest.mark.parametrize("key,value", [
    ("diagnostic_report_file", "../diagnostic_report.md"),
    ("diagnostic_report_file", "other.md"),
    ("diagnostic_report_file", "D:/secrets.txt"),
    ("diagnostic_report_sha256", "0" * 64),
    ("diagnostic_report_sha256", None),
    ("diagnostic_report_market_date", "2026-11-12"),
    ("diagnostic_report_generated_at", "2026-11-13T20:02:18+08:00"),
    ("diagnostic_report_mode", "realtime"),
    ("diagnostic_report_contract", "unknown"),
    ("status", "ok"),
])
def test_identity_hash_path_or_mode_mismatch_is_rejected(tmp_path, key, value):
    _path, payload, proof = make_artifact(tmp_path)
    payload[key] = value
    with pytest.raises((ValueError, FileNotFoundError)):
        renderer.verified_failure_diagnostic(tmp_path / "result.json", payload, proof)


@pytest.mark.parametrize("mutation", ["hash_tamper", "header_date", "header_time", "header_mode", "missing_file", "invalid_utf8", "duplicate_header"])
def test_saved_report_contents_and_file_must_match_result(tmp_path, mutation):
    path, payload, proof = make_artifact(tmp_path)
    if mutation == "hash_tamper":
        path.write_bytes(path.read_bytes() + b"tampered")
    elif mutation == "missing_file":
        path.unlink()
    else:
        raw = path.read_bytes()
        if mutation == "invalid_utf8":
            raw += b"\xff"
        elif mutation == "header_date":
            raw = raw.replace("目标行情日：2026-11-13".encode(), "目标行情日：2026-11-12".encode())
        elif mutation == "header_time":
            raw = raw.replace(b"20:03:18", b"20:02:18")
        elif mutation == "header_mode":
            raw = raw.replace(b"mode:close", b"mode:realtime") if b"mode:close" in raw else raw.replace(b"close", b"realtime")
        elif mutation == "duplicate_header":
            raw += "\n目标行情日：2026-11-13\n".encode()
        path.write_bytes(raw)
        payload["diagnostic_report_sha256"] = hashlib.sha256(raw).hexdigest()
    with pytest.raises((ValueError, FileNotFoundError)):
        renderer.verified_failure_diagnostic(tmp_path / "result.json", payload, proof)


def test_verified_source_is_required_for_new_diagnostic_contract(tmp_path):
    _path, payload, _proof = make_artifact(tmp_path)
    with pytest.raises(ValueError, match="source"):
        renderer.verified_failure_diagnostic(tmp_path / "result.json", payload, None)


def test_old_failure_retains_warning_only_compatibility(tmp_path):
    assert renderer.verified_failure_diagnostic(tmp_path / "result.json", {"status": "failed"}, None) == (None, None)


def test_current_release_cannot_silently_drop_diagnostic_binding(tmp_path):
    _path, payload, proof = make_artifact(tmp_path)
    for key in list(payload):
        if key.startswith("diagnostic_report_"):
            del payload[key]
    with pytest.raises(ValueError, match="requires a bound"):
        renderer.verified_failure_diagnostic(tmp_path / "result.json", payload, proof)


@pytest.mark.parametrize("release", [None, "20261112-expert-review-v5-r3", "unknown"])
def test_new_diagnostic_contract_cannot_borrow_old_source_release(tmp_path, release):
    _path, payload, proof = make_artifact(tmp_path)
    proof["release"] = release
    with pytest.raises(ValueError, match="source"):
        renderer.verified_failure_diagnostic(tmp_path / "result.json", payload, proof)


@pytest.mark.parametrize("kind", ["future", "beijing_previous_day", "naive", "utc_same_beijing_day", "historical_later_generation"])
def test_generation_time_uses_actual_clock_and_beijing_market_day(tmp_path, kind):
    from datetime import datetime, timedelta, timezone
    path, payload, proof = make_artifact(tmp_path)
    old_generated = payload["generated_at"]
    if kind == "future":
        generated = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    elif kind == "beijing_previous_day":
        generated = "2026-11-13T00:15:00+14:00"
    elif kind == "naive":
        generated = "2026-11-13T20:03:18"
    elif kind == "utc_same_beijing_day":
        generated = "2026-11-12T23:30:00+00:00"
    else:
        generated = old_generated
        payload["market_date"] = payload["diagnostic_report_market_date"] = "2026-11-12"
    raw = path.read_bytes().replace(old_generated.encode(), generated.encode())
    if kind == "historical_later_generation":
        raw = raw.replace("目标行情日：2026-11-13".encode(), "目标行情日：2026-11-12".encode())
    path.write_bytes(raw)
    payload["generated_at"] = payload["diagnostic_report_generated_at"] = generated
    payload["diagnostic_report_sha256"] = hashlib.sha256(raw).hexdigest()
    if kind in {"utc_same_beijing_day", "historical_later_generation"}:
        report, text = renderer.verified_failure_diagnostic(tmp_path / "result.json", payload, proof)
        assert Path(report) == path.resolve() and generated in text
    else:
        with pytest.raises(ValueError, match="generation time"):
            renderer.verified_failure_diagnostic(tmp_path / "result.json", payload, proof)


def test_diagnostic_text_cannot_inject_html_or_end_markdown_fence():
    raw = "IM\n```\n<script>alert(1)</script>\n<img src=x onerror=steal()>\n"
    body, html = renderer.failure_diagnostic_display(raw)
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "<img src=x" not in html
    assert "````text" in body


def test_cli_failure_displays_and_attaches_verified_diagnostic(tmp_path, monkeypatch):
    path, payload, proof = make_artifact(tmp_path)
    from check_ic_im_v1_4_delivery import expected_identity_for_day
    from datetime import date
    payload["build"], payload["delivery_revision"] = expected_identity_for_day(date(2026, 11, 13))
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(payload), encoding="utf-8")
    out = tmp_path / "mail"
    monkeypatch.setattr(renderer, "source_provenance", lambda *_a, **_k: proof)
    monkeypatch.setattr(sys, "argv", ["build", "--result", str(result_path), "--out-dir", str(out)])
    assert renderer.main() == 0
    metadata = json.loads((out / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["attachment"] == str(path.resolve())
    assert "IM" in metadata["body"] and "IM" in metadata["html_body"]
    assert "整体信号尚未确认" in metadata["body"]
    assert json.loads(result_path.read_text(encoding="utf-8")) == payload
