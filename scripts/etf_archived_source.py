"""Replay immutable, reviewed historical fixtures without expiring Actions downloads.

This is historical provenance verification, never a live-delivery authorization.
Live verify-source / verify-resume commands retain their strict online checks.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import zipfile

import etf_cloud_checkpoint_probe as checkpoint
import etf_fresh_shadow as fresh

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/etf_archived"
PINS = {
    "baseline": (12231389856, "2a7f7b9c124aedb6a37647af7622784cf76eabf4d15968b3ac8786027b3f56b2"),
    "fresh": (12308593073, "380c653419854065e9a9c936adee355f7b9d9bc1aaf57ce42ee33d56cdcd818d"),
    "checkpoint": (12269585268, "8b43513281f565ea4cf2e4bba48c486f610abf3f56ed7a8065891680400deba8"),
}


def verified_archive(name, fixture_dir=FIXTURES):
    artifact_id, digest = PINS[name]
    raw = base64.b64decode((fixture_dir / (name + ".zip.b64")).read_text("ascii").strip(), validate=True)
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("archived artifact bytes changed")
    meta = json.loads((fixture_dir / "provenance.json").read_text("utf-8"))[name]
    artifact = meta["artifact"]
    if artifact["id"] != artifact_id or artifact["digest"] != "sha256:" + digest:
        raise ValueError("archived artifact identity changed")
    if name == "fresh":
        pin = json.loads((ROOT / "config/etf-fresh-shadow-record.json").read_text("utf-8"))["source"]
        fresh.verify_source(meta["run"], artifact, pin, pin["repository"], pin["branch"])
    elif name == "baseline":
        checkpoint.verify_source(meta["run"], checkpoint.RESUME_REPOSITORY, "main")
        linked = artifact["workflow_run"]
        if (linked["id"] != int(checkpoint.SOURCE_RUN) or linked["head_sha"] != meta["run"]["head_sha"]
                or meta["run"]["head_sha"] != "dc34eab7dcd3d44a487bc18ff3807472fdcac14b"
                or linked["repository_id"] != meta["run"]["repository"]["id"]
                or artifact["expired"] is not False):
            raise ValueError("archived baseline producer mismatch")
    else:
        checkpoint.verify_resume(meta["run"], artifact, meta["commit"], checkpoint.RESUME_REPOSITORY)
    z = zipfile.ZipFile(io.BytesIO(raw))
    paths = [PurePosixPath(i.filename) for i in z.infolist()]
    if len(paths) != len(set(paths)) or any(
        p.is_absolute() or ".." in p.parts or "\\" in str(p) or ":" in str(p)
        for p in paths
    ) or any(i.file_size > 10_000_000 or ((i.external_attr >> 16) & 0o170000) == 0o120000 for i in z.infolist()):
        raise ValueError("unsafe archive member")
    return z, meta


def materialize(name, destination, fixture_dir=FIXTURES):
    if destination.exists():
        raise ValueError("archive destination must be new; historical files are never overwritten")
    proof_dir = destination.with_name(destination.name + "-provenance")
    if proof_dir.exists():
        raise ValueError("archive provenance destination must be new")
    z, meta = verified_archive(name, fixture_dir)
    destination.mkdir(parents=True)
    z.extractall(destination)
    proof = {"mode": "archived_historical_replay", "current_live_status": "not_checked",
             "artifact_id": PINS[name][0], "artifact_sha256": PINS[name][1],
             "historical_provenance_verified": True, "delivery_eligible": False, "sent": False}
    if name == "baseline":
        (destination / "source.json").write_text(json.dumps(meta["run"], indent=2), "utf-8")
    proof_dir.mkdir()
    if name == "checkpoint":
        (proof_dir / "resume.json").write_text(json.dumps(checkpoint.resume_metadata(), indent=2), "utf-8")
    (proof_dir / "archived-source-proof.json").write_text(json.dumps(proof, indent=2), "utf-8")
    return proof


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name", choices=PINS)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(materialize(args.name, args.out_dir)))


if __name__ == "__main__":
    main()
