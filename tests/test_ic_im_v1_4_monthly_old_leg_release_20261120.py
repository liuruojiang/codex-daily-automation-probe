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

OLD = "docs/ic_im_report_tenor_spreads_20261120_v1_manifest.json"
NEW = "docs/ic_im_monthly_old_leg_and_failure_digest_20261120_v1_manifest.json"
RELEASE = "20261120-monthly-old-leg-digest-v1"
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
        "ic_im_adjusted_put_migration_v1.py", "ic_im_evidence_adapter_v1.py",
        "ic_im_expiry_evidence_v1.py", "ic_im_held_put_close_v1.py", "poe_ic_im_mainline_v1_4_bot.py"}
    for key in ("support_files", "historical_exclusion_registry_sha256",
                "daily_gate_deselections", "daily_gate_outer_timeout_seconds"):
        assert new[key] == old[key]
    assert new["required_local_tests"] == old["required_local_tests"] + [
        "test_icim_monthly_old_leg_sse_20261120.py"]
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
