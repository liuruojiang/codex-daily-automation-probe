"""Real Git cutover tests: exact code review, no data migration or false PASS."""
import json
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import activate_microcap_scoped_runtime as runtime


@pytest.fixture
def prepared(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()

    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.STDOUT).decode().strip()

    def put(name, body):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)

    git("init", "-q")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "Isolated activation test")
    put("scripts/core.py", b"old core\n")
    put("outputs/nav.csv", b"original approved prefix\n")
    put(".microcap_index_cache/security_master.csv", b"historical full master\n")
    git("add", ".")
    git("commit", "-qm", "baseline")
    old = git("rev-parse", "HEAD")
    put("scripts/core.py", b"new core\n")
    put("scripts/new_consumer.py", b"new module\n")
    put("docs/source-review.md", b"reviewed evidence\n")
    put("tests/fixtures/native evidence.pdf", b"real-source-shaped transport fixture\n")
    git("add", ".")
    git("commit", "-qm", "reviewed source successor")
    new = git("rev-parse", "HEAD")
    git("remote", "add", "origin", str(root))
    changes = {}
    for name in git("diff", "--name-only", old, new).splitlines():
        try:
            before = git("show", old + ":" + name).encode() + b"\n"
        except subprocess.CalledProcessError:
            before = None
        changes[name] = [runtime.digest(before) if before is not None else None,
                         runtime.digest((root / name).read_bytes())]
    git("checkout", "--detach", old)
    config = tmp_path / "review.json"
    config.write_text(json.dumps(dict(schema_version=1, baseline_sha=old, runtime_sha=new, changes=changes)))
    restore = root / "restore.json"
    restore.write_text(json.dumps(dict(restored=True, strategy_sha=old)))
    put("outputs/nav.csv", b"actual restored extended state\r\n")
    put(".microcap_index_cache/new raw.csv", b"untracked authentic source\x00\xff")
    return root, config, restore, tmp_path / "activation.json", git, put


def test_exact_activation_preserves_restored_state_and_evidence(prepared):
    root, config, restore, receipt, git, _ = prepared
    originals = {p: p.read_bytes() for p in [restore, root / "outputs/nav.csv",
                 root / ".microcap_index_cache/security_master.csv", root / ".microcap_index_cache/new raw.csv"]}
    result = runtime.activate(root, config, restore, receipt)
    assert result["activated"] and not result["formal_release_verified"]
    assert not result["current_data_verified"] and not result["historical_rewrite_authorized"]
    assert git("rev-parse", "HEAD") == result["runtime_sha"]
    assert json.loads(receipt.read_bytes()) == result
    assert all(p.read_bytes() == raw for p, raw in originals.items())
    assert set(result["changes"]) == set(json.loads(config.read_bytes())["changes"])
    assert not (root / ".git/codex-scoped-activation.lock").exists()


@pytest.mark.parametrize('fault',[None,'existing_verified_clock','bundle','manifest','runtime','native','missing_bundle'])
def test_scoped_whole_receipt_requires_original_bound_archive(prepared,fault):
    root,config,restore,receipt,git,put=prepared
    old=json.loads(config.read_bytes())['baseline_sha']
    manifest=dict(ok=True,status='complete',release_sha='c'*40,expected_date='2026-11-10',verified_at='2026-11-10T16:30:00+08:00')
    raw=json.dumps(manifest).encode();put('outputs/top100_delivery_manifest.json',raw)
    bundle=restore.parent.parent/'whole.zip'
    with zipfile.ZipFile(bundle,'w') as archive:archive.writestr('outputs/top100_delivery_manifest.json',raw)
    body=dict(restored=True,status='scoped_whole_restore_verified',runtime_sha=old,
        historical_release_files_equivalent=True,repository='liuruojiang/codex-daily-automation-probe',
        native_restore=dict(ok=True,expected_date='2026-11-10'),seed_date='2026-11-10',
        source_manifest_release_sha='c'*40,restored_manifest_release_sha='c'*40,
        source_manifest_sha256=runtime.digest(raw),bundle_sha256=runtime.digest(bundle.read_bytes()),bundle_bytes=bundle.stat().st_size)
    if fault=='bundle':bundle.write_bytes(bundle.read_bytes()+b'changed')
    elif fault=='manifest':put('outputs/top100_delivery_manifest.json',raw+b' ')
    elif fault=='runtime':body['runtime_sha']='d'*40
    elif fault=='native':body['native_restore']['ok']=False
    elif fault=='existing_verified_clock':
        manifest['verified_at']='2026-11-10T16:31:00+08:00'
        raw=json.dumps(manifest).encode();put('outputs/top100_delivery_manifest.json',raw)
        body['native_restore']['restore_source']='existing_whole_delivery'
    restore.write_text(json.dumps(body))
    if fault and fault!='existing_verified_clock':
        with pytest.raises(ValueError):runtime.activate(root,config,restore,receipt,bundle=None if fault=='missing_bundle' else bundle)
        assert git('rev-parse','HEAD')==old and not receipt.exists()
    else:
        result=runtime.activate(root,config,restore,receipt,bundle=bundle)
        assert result['activated'] and result['original_archive']['restored_manifest_sha256']==runtime.digest(raw)
        assert (root/'outputs/top100_delivery_manifest.json').read_bytes()==raw


@pytest.mark.parametrize("fault", ["old_hash", "new_hash", "absent_before_lie", "omit_code",
    "omit_document", "omit_fixture", "extra_path", "data_path", "dirty_tracked_code",
    "untracked_code", "failed_restore", "wrong_restore_sha", "wrong_head", "same_commits",
    "short_sha", "bad_schema", "bool_schema", "overwrite_restore", "existing_receipt", "traversal"])
def test_unreviewed_activation_leaves_head_data_and_original_evidence(prepared, fault):
    root, config, restore, receipt, git, put = prepared
    value = json.loads(config.read_bytes())
    if fault == "old_hash": value["changes"]["scripts/core.py"][0] = "0" * 64
    elif fault == "new_hash": value["changes"]["scripts/core.py"][1] = "0" * 64
    elif fault == "absent_before_lie": value["changes"]["scripts/core.py"][0] = None
    elif fault == "omit_code": value["changes"].pop("scripts/new_consumer.py")
    elif fault == "omit_document": value["changes"].pop("docs/source-review.md")
    elif fault == "omit_fixture": value["changes"].pop("tests/fixtures/native evidence.pdf")
    elif fault == "extra_path": value["changes"]["scripts/unreviewed.py"] = [None, "1" * 64]
    elif fault == "data_path": value["changes"]["outputs/nav.csv"] = ["1" * 64, "2" * 64]
    elif fault == "dirty_tracked_code": put("scripts/core.py", b"uncommitted user edit\n")
    elif fault == "untracked_code": put("scripts/shadow.py", b"untracked source\n")
    elif fault == "failed_restore": restore.write_text(json.dumps(dict(restored=False, strategy_sha=value["baseline_sha"])))
    elif fault == "wrong_restore_sha": restore.write_text(json.dumps(dict(restored=True, strategy_sha="a" * 40)))
    elif fault == "wrong_head": git("checkout", "--detach", value["runtime_sha"])
    elif fault == "same_commits": value["runtime_sha"] = value["baseline_sha"]
    elif fault == "short_sha": value["runtime_sha"] = value["runtime_sha"][:8]
    elif fault == "bad_schema": value["unknown"] = True
    elif fault == "bool_schema": value["schema_version"] = True
    elif fault == "overwrite_restore": receipt = restore
    elif fault == "existing_receipt": receipt.write_bytes(b"previous evidence\n")
    elif fault == "traversal": value["changes"]["../outside.py"] = [None, "1" * 64]
    config.write_text(json.dumps(value))
    before_head, before_restore, before_data = git("rev-parse", "HEAD"), restore.read_bytes(), (root / "outputs/nav.csv").read_bytes()
    with pytest.raises(ValueError):
        runtime.activate(root, config, restore, receipt)
    assert git("rev-parse", "HEAD") == before_head
    assert restore.read_bytes() == before_restore
    assert (root / "outputs/nav.csv").read_bytes() == before_data
    assert not (root / ".git/codex-scoped-activation.lock").exists()


def test_concurrent_restore_change_is_detected_before_checkout(prepared, monkeypatch):
    root, config, restore, receipt, git, _ = prepared
    original_run = subprocess.run
    def run(command, **kwargs):
        result = original_run(command, **kwargs)
        if "fetch" in command:
            restore.write_bytes(restore.read_bytes() + b" ")
        return result
    monkeypatch.setattr(runtime.subprocess, "run", run)
    with pytest.raises(ValueError, match="concurrently"):
        runtime.activate(root, config, restore, receipt)
    assert git("rev-parse", "HEAD") == json.loads(config.read_bytes())["baseline_sha"]
    assert not receipt.exists()


def test_after_checkout_readback_failure_never_writes_acceptance(prepared, monkeypatch):
    root, config, restore, receipt, git, put = prepared
    original_run = subprocess.run
    def run(command, **kwargs):
        result = original_run(command, **kwargs)
        if "checkout" in command:
            put("scripts/core.py", b"changed during checkout\n")
        return result
    monkeypatch.setattr(runtime.subprocess, "run", run)
    with pytest.raises(ValueError, match="runtime source readback"):
        runtime.activate(root, config, restore, receipt)
    assert not receipt.exists()
    assert git("rev-parse", "HEAD") == json.loads(config.read_bytes())["runtime_sha"]
    assert not (root / ".git/codex-scoped-activation.lock").exists()


def test_data_tree_change_cannot_hide_behind_an_incomplete_source_review(prepared):
    root, config, restore, receipt, git, put = prepared
    value = json.loads(config.read_bytes())
    git("checkout", "--detach", value["runtime_sha"])
    put("outputs/nav.csv", b"forbidden committed history rewrite\n")
    git("add", "outputs/nav.csv")
    git("commit", "-qm", "not an allowed source-only activation")
    value["runtime_sha"] = git("rev-parse", "HEAD")
    git("checkout", "--detach", value["baseline_sha"])
    put("outputs/nav.csv", b"actual restored state survives refusal\n")
    config.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="complete reviewed path set"):
        runtime.activate(root, config, restore, receipt)
    assert (root / "outputs/nav.csv").read_bytes() == b"actual restored state survives refusal\n"
    assert git("rev-parse", "HEAD") == value["baseline_sha"]
    assert not receipt.exists()


def test_new_source_cannot_claim_a_nonexistent_old_blob(prepared):
    root, config, restore, receipt, git, _ = prepared
    value = json.loads(config.read_bytes())
    value["changes"]["scripts/new_consumer.py"][0] = "f" * 64
    config.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="Git source hash"):
        runtime.activate(root, config, restore, receipt)
    assert git("rev-parse", "HEAD") == value["baseline_sha"]
    assert not receipt.exists()
