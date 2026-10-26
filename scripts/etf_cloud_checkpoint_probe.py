"""Offline cross-runner checkpoint probe. Synthetic provider responses only."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.request
from unittest.mock import patch

import etf_content_cards as cards
import etf_shadow_run as shadow
from github_metadata import get_json

VERSION = "etf-cloud-checkpoint-probe-v1"
SOURCE_RUN = "39347818936"
RESUME_REPOSITORY = "liuruojiang/codex-daily-automation-probe"
# Accepted stage-8 producer and immutable artifact, observed after successful completion.
# Keep bundle v1 compatibility; these pins do not relax shadow context validation.
RESUME_ORIGIN = {"GITHUB_RUN_ID": "39432084491", "GITHUB_RUN_ATTEMPT": "1",
                 "GITHUB_SHA": "2b9feddb5ece6e1844e26451acbc3ef14b83c9f5"}
RESUME_HEAD = "1664a7d3f19ae88c84acb2f7d1d249b01ff5372b"
RESUME_BASE = "b69e296d21b63f40d5b6e46726c555a85b1cc5b0"
RESUME_ARTIFACT = {"id": 12269585268, "name": "etf-checkpoint-seed-39432084491-1",
                   "digest": "sha256:8b43513281f565ea4cf2e4bba48c486f610abf3f56ed7a8065891680400deba8"}
TEXT = ("The study reports a 12% historical return before transaction costs and uses a limited sample of portfolio observations. "
        "The authors explain that these observations cannot establish future performance and that implementation costs have not been tested.")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def config(label):
    return {"run_label": label, "endpoint": "https://example.org/synthetic-only", "generator": "synthetic-generator",
            "reviewer": "synthetic-reviewer", "thinking": None, "max_requests": 1,
            "max_reserved_output_tokens": 2400, "generation_tokens": 2400, "review_tokens": 3200,
            "max_input_chars": 60000}


def origin():
    return {k: os.environ.get(k, "local") for k in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_SHA")}


def verify_source(run, repository, default_branch):
    require(str(run.get("id")) == SOURCE_RUN and run.get("repository", {}).get("full_name") == repository
            and run.get("head_repository", {}).get("full_name") == repository
            and run.get("path") == ".github/workflows/etf-allocation-digest.yml"
            and run.get("head_branch") == default_branch and run.get("status") == "completed"
            and run.get("conclusion") == "success" and run.get("event") in ("schedule", "workflow_dispatch"),
            "unexpected production source")


def fetch_source(destination):
    repository = os.environ["GITHUB_REPOSITORY"]
    run = get_json("https://api.github.com/repos/" + repository + "/actions/runs/" + SOURCE_RUN, os.environ["GH_TOKEN"])
    verify_source(run, repository, os.environ["DEFAULT_BRANCH"])
    # Persist selected public provenance only, never credentials or request headers.
    cards.write_json(destination, {k: run[k] for k in ("id", "path", "head_sha", "head_branch", "event", "conclusion")})


def resume_metadata():
    return {"version": "etf-cross-run-provenance-v1", "repository": RESUME_REPOSITORY,
            "producer_origin": dict(RESUME_ORIGIN), "artifact": dict(RESUME_ARTIFACT), "consumer_origin": origin()}


def verify_resume(run, artifact, commit, repository):
    current = origin()["GITHUB_RUN_ID"]
    require(current.isdigit() and int(current) > 0 and current != RESUME_ORIGIN["GITHUB_RUN_ID"],
            "cross-run restore requires a different positive consumer run ID")
    require(repository == RESUME_REPOSITORY and str(run.get("id")) == RESUME_ORIGIN["GITHUB_RUN_ID"]
            and run.get("run_attempt") == 1 and run.get("head_sha") == RESUME_HEAD
            and run.get("repository", {}).get("full_name") == repository
            and run.get("head_repository", {}).get("full_name") == repository
            and run.get("path") == ".github/workflows/delivery-regression.yml"
            and run.get("head_branch") == "codex/etf-evidence-cards-step1"
            and run.get("event") == "pull_request" and run.get("status") == "completed"
            and run.get("conclusion") == "success"
            and any(pr.get("number") == 158 for pr in run.get("pull_requests", [])),
            "unexpected checkpoint producer run")
    require(commit.get("sha") == RESUME_ORIGIN["GITHUB_SHA"]
            and {parent.get("sha") for parent in commit.get("parents", [])} == {RESUME_HEAD, RESUME_BASE},
            "unexpected producer checkout commit")
    require(all(artifact.get(k) == v for k, v in RESUME_ARTIFACT.items()) and artifact.get("expired") is False
            and str(artifact.get("workflow_run", {}).get("id")) == RESUME_ORIGIN["GITHUB_RUN_ID"]
            and artifact.get("workflow_run", {}).get("head_sha") == RESUME_HEAD
            and artifact.get("workflow_run", {}).get("repository_id") == run.get("repository", {}).get("id")
            and artifact.get("workflow_run", {}).get("head_repository_id") == run.get("repository", {}).get("id"),
            "unexpected or expired checkpoint artifact")
    return resume_metadata()


def fetch_resume(destination):
    repository = os.environ["GITHUB_REPOSITORY"]
    def get(path):
        return get_json("https://api.github.com/repos/" + repository + path, os.environ["GH_TOKEN"])
    run = get("/actions/runs/" + RESUME_ORIGIN["GITHUB_RUN_ID"])
    artifact = get("/actions/artifacts/" + str(RESUME_ARTIFACT["id"]))
    commit = get("/git/commits/" + RESUME_ORIGIN["GITHUB_SHA"])
    cards.write_json(destination, verify_resume(run, artifact, commit, repository))


def child(case, folder):
    materials = cards.read_json(folder.parent / "synthetic-materials.json")
    def crash_before_receipt(*args, **kwargs):
        os._exit(86)
    def crash_after_receipt(*args, **kwargs):
        os._exit(86)
    def send(request, timeout):
        payload = json.loads(request.data)
        packet = json.loads(payload["messages"][1]["content"])
        result = {"material_id": packet["material_id"], "title_zh": "合成研究的历史收益",
                  "facts": [{"text_zh": "作者报告历史收益为12%，未扣交易成本。", "evidence_ids": ["p1.s1"]}],
                  "limitations": [], "questions": []}
        return io.BytesIO(json.dumps({"model": "synthetic-generator", "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(result)}}],
                                    "usage": {"total_tokens": 100}}).encode())
    target = patch.object(cards, "call_json", side_effect=crash_before_receipt) if case == "unknown" else patch.object(shadow, "accept_response", side_effect=crash_after_receipt)
    with target:
        shadow.advance(materials, config(case), sha(folder.parent / "synthetic-materials.json"), folder,
                       live=True, max_new_requests=1, api_key="synthetic-noncredential", transport=send)
    raise RuntimeError("fault injection failed to terminate child")


def inventory(folder):
    result = {}
    for path in sorted(folder.rglob("*")):
        require(not path.is_symlink(), "symlink in checkpoint bundle")
        if path.is_file() and path != folder / "bundle.json":
            result[path.relative_to(folder).as_posix()] = sha(path)
    return result


def seed(manifest, folder, source_metadata):
    require(not folder.exists(), "seed requires a new folder")
    folder.mkdir(parents=True)
    manifest_copy = folder / "collection_manifest.json"
    manifest_copy.write_bytes(manifest.read_bytes())
    source = cards.read_json(source_metadata)
    require(str(source["id"]) == SOURCE_RUN, "wrong production source ID")
    cards.write_json(folder / "source.json", source)
    materials = shadow.select_materials(cards.prepare_manifest(cards.read_json(manifest_copy)), 6)
    require(len(materials) == 6, "expected six real source selections")
    cards.write_json(folder / "real-materials.json", materials)
    real = shadow.advance(materials, config("production-offline"), sha(manifest_copy), folder / "real", live=False, max_new_requests=0)
    require(real["budget"]["requests_reserved"] == 0, "real source reserved a request")
    synthetic = [cards.make_material(title="Synthetic portfolio study", url="https://example.org/study", source="synthetic", published="", paragraphs=[TEXT])]
    cards.write_json(folder / "synthetic-materials.json", synthetic)
    for case in ("completed", "unknown"):
        code = subprocess.run([sys.executable, __file__, "crash-child", "--case", case, "--out-dir", str(folder / case)], check=False).returncode
        require(code == 86, "child did not stop at injected crash")
        journal = cards.read_json(folder / case / "run.json")
        attempt = next(iter(journal["attempts"].values()))
        require(attempt["state"] == "reserved", "crash did not leave pending checkpoint")
        receipts = list((folder / case / "receipts").glob("*.json"))
        require(len(receipts) == (1 if case == "completed" else 0), "incorrect crash receipt boundary")
    bundle = {"version": VERSION, "origin": origin(), "source_run_id": SOURCE_RUN,
              "files": inventory(folder), "provider_network_requests": 0, "faults": "synthetic_process_exit_86"}
    cards.write_json(folder / "bundle.json", bundle)
    print(json.dumps({"phase": "seed", "files": len(bundle["files"]), "provider_network_requests": 0}))


def restore(folder, report_path, verified_resume=None):
    bundle = cards.read_json(folder / "bundle.json")
    expected_origin = origin()
    if verified_resume is not None:
        require(verified_resume == resume_metadata(), "cross-run verification metadata mismatch")
        require(origin()["GITHUB_RUN_ID"].isdigit() and int(origin()["GITHUB_RUN_ID"]) > 0
                and origin()["GITHUB_RUN_ID"] != RESUME_ORIGIN["GITHUB_RUN_ID"], "consumer must be a different run")
        expected_origin = RESUME_ORIGIN
    require(bundle.get("version") == VERSION and bundle.get("origin") == expected_origin and bundle.get("source_run_id") == SOURCE_RUN,
            "checkpoint provenance mismatch")
    require(bundle["files"] == inventory(folder), "checkpoint inventory/hash mismatch")
    materials = cards.read_json(folder / "real-materials.json")
    before = cards.read_json(folder / "real/shadow-report.json")
    source_hash = sha(folder / "collection_manifest.json")
    real = shadow.advance(materials, config("production-offline"), source_hash, folder / "real", live=False, max_new_requests=0)
    require(real == before and real["budget"]["requests_reserved"] == 0, "real source replay changed")
    synthetic = cards.read_json(folder / "synthetic-materials.json")
    synthetic_hash = sha(folder / "synthetic-materials.json")
    result = {}
    calls = []
    def forbidden(*args, **kwargs):
        calls.append("unexpected_call")
        raise RuntimeError("provider transport forbidden")
    with patch.object(cards, "call_json", side_effect=forbidden):
        for case in ("completed", "unknown"):
            prior = cards.read_json(folder / case / "run.json")
            recovered = shadow.advance(synthetic, config(case), synthetic_hash, folder / case, live=False, max_new_requests=0)
            attempt = next(iter(cards.read_json(folder / case / "run.json")["attempts"].values()))
            old_attempt = next(iter(prior["attempts"].values()))
            require(attempt["request_key"] == old_attempt["request_key"] and attempt["reserved_tokens"] == old_attempt["reserved_tokens"], "reservation changed")
            require(attempt["state"] == ("parsed" if case == "completed" else "unknown"), "incorrect recovery state")
            require(recovered["budget"]["requests_reserved"] == 1 and recovered["budget"]["output_tokens_reserved"] == 2400, "budget was lost")
            stable = sha(folder / case / "run.json")
            again = shadow.advance(synthetic, config(case), synthetic_hash, folder / case, live=True, max_new_requests=99,
                                   api_key="synthetic-noncredential", transport=forbidden)
            require(again == recovered and stable == sha(folder / case / "run.json"), "repeat replay changed checkpoint")
            result[case] = {"state": attempt["state"], "budget": recovered["budget"], "repeated_replay_identical": True}
    require(not calls, "resume attempted a model request")
    report = {"version": VERSION, "origin": origin(), "source_run_id": SOURCE_RUN, "restored_files_verified": len(bundle["files"]),
              "checkpoint_origin": bundle["origin"], "verified_resume": verified_resume,
              "real_materials": len(materials), "real_replay_identical": True, "provider_network_requests": 0,
              "synthetic_fault_results": result, "delivery_eligible": False,
              "scope": ("cross-workflow-run artifact restoration" if verified_resume is not None else "cross-runner artifact transfer within one workflow run")
                       + "; synthetic crash boundaries, no live provider outage"}
    cards.write_json(report_path, report)
    print(json.dumps(report, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("verify-source", "verify-resume", "seed", "restore", "crash-child"))
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--source-metadata", type=Path)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--resume-metadata", type=Path)
    parser.add_argument("--case", choices=("completed", "unknown"))
    args = parser.parse_args()
    if args.mode == "verify-source":
        fetch_source(args.out_dir / "source.json")
    elif args.mode == "verify-resume":
        fetch_resume(args.out_dir / "resume.json")
    elif args.mode == "seed":
        seed(args.manifest, args.out_dir, args.source_metadata)
    elif args.mode == "restore":
        restore(args.out_dir, args.report, cards.read_json(args.resume_metadata) if args.resume_metadata else None)
    else:
        child(args.case, args.out_dir)


if __name__ == "__main__":
    main()
