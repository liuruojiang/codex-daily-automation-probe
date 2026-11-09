"""Replay the real producer proof against Git bytes, not Windows checkout bytes."""
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import build_ic_im_v1_4_digest as digest


def test_actual_cloud_proof_accepts_committed_manifest_bytes(tmp_path, monkeypatch):
    relative = 'docs/ic_im_fear_source_split_20261109_manifest.json'
    raw = subprocess.check_output(['git', '-C', str(ROOT), 'show', 'HEAD:' + relative])
    target = tmp_path / relative
    target.parent.mkdir(parents=True)
    target.write_bytes(raw)
    # This is the byte layout of a fresh Linux checkout, without newline conversion.
    monkeypatch.setattr(digest, '__file__', str(tmp_path / 'scripts/build_ic_im_v1_4_digest.py'))
    proof_path = ROOT / 'tests/fixtures/icim/fear_split_20261109/program-source.json'
    proof = json.loads(proof_path.read_bytes())
    assert digest.source_provenance(str(proof_path), proof['strategy_sha']) == proof
