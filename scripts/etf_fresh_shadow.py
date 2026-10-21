"""Replay one pinned new production snapshot without model calls or delivery."""
from __future__ import annotations

import argparse
import copy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import urllib.request

import etf_automatic_mail_preview as automatic
import etf_content_cards as cards
import etf_mail_preview as mail

VERSION = "etf-fresh-shadow-record-v1"
CORE = ("metadata.json", "collection_manifest.json", "history_before.json")
require = mail.require


def verify_source(run, artifact, pin, repository, default_branch):
    require(re.fullmatch(r"[\w.-]+/[\w.-]+", repository) is not None, "invalid repository")
    require(repository == pin["repository"] and default_branch == pin["branch"], "source repository/branch changed")
    require(run["id"] == pin["run_id"] and run["run_attempt"] == pin["run_attempt"]
            and run["path"] == pin["workflow_path"] and run["head_sha"] == pin["head_sha"]
            and run["head_branch"] == default_branch and run["event"] == pin["event"]
            and run["event"] in ("schedule", "workflow_dispatch")
            and run["status"] == "completed" and run["conclusion"] == "success", "untrusted source run")
    for field in ("repository", "head_repository"):
        require(run[field]["id"] == pin["repository_id"] and run[field]["full_name"] == repository,
                "source repository identity mismatch")
    linked = artifact["workflow_run"]
    require(artifact["id"] == pin["artifact_id"] and artifact["name"] == pin["artifact_name"]
            and artifact["expired"] is False and artifact["digest"] == "sha256:" + pin["artifact_sha256"]
            and linked["id"] == pin["run_id"] and linked["head_sha"] == pin["head_sha"]
            and linked["head_branch"] == default_branch
            and linked["repository_id"] == linked["head_repository_id"] == pin["repository_id"],
            "untrusted source artifact")
    return {"verified": True, "source": copy.deepcopy(pin), "model_calls": 0, "sent": False}


def read_source(folder, record):
    values = []
    for name in CORE:
        raw = (folder / name).read_bytes()
        require(hashlib.sha256(raw).hexdigest() == record["input_sha256"][name], "fresh source bytes changed: " + name)
        values.append(json.loads(raw))
    return values


def build(metadata, manifest, history, record, old_registry, old_index):
    require(record["version"] == VERSION and record["delivery_eligible"] is False, "invalid fresh record")
    require(cards.digest(old_registry) == record["old_registry_sha256"]
            and cards.digest(old_index) == record["old_index_sha256"], "historical records changed")
    identity = automatic.snapshot_identity(manifest, history);pin = record["source"]
    require(identity == record["registry"]["snapshot"] and identity["run_id"] == str(pin["run_id"])
            and identity["head_sha"] == pin["head_sha"], "fresh snapshot identity mismatch")
    require(datetime.fromisoformat(identity["cutoff_utc"]) > datetime.fromisoformat(old_registry["snapshot"]["cutoff_utc"]),
            "fresh source is not newer")
    materials = {m["material_id"]: m for m in cards.prepare_manifest(manifest)}
    entries = record["registry"]["entries"]
    require(len(entries) == len({e["material_id"] for e in entries}) == len({e["url"] for e in entries})
            and [e["material_id"] for e in entries] == record["protocol"]["selection"], "fresh sample coverage mismatch")
    claims = 0
    for entry in entries:
        mid = entry["material_id"]
        require(mid in materials, "fresh material missing")
        one = automatic.decode_entry(entry, materials[mid])
        packet = mail.build(one, metadata, manifest, history)
        require(packet["appendix_count"] == 1 and packet["pending_count"] == 0, "fresh editorial review incomplete")
        claims += len(entry["editor"]["checks"])
    fresh, email = automatic.build(metadata, manifest, history, record["registry"])
    absent, _ = automatic.build(metadata, manifest, history)
    old, _ = automatic.build(metadata, manifest, history, old_registry)
    # Explicitly synthetic same-URL tests: the actual dates have no overlapping selected URL.
    aligned = copy.deepcopy(old_registry);aligned["entries"] = aligned["entries"][:1]
    aligned["entries"][0]["url"] = entries[0]["url"]
    same_url, _ = automatic.build(metadata, manifest, history, aligned)
    aligned["snapshot"] = copy.deepcopy(identity)
    rebound, _ = automatic.build(metadata, manifest, history, aligned)
    require(fresh["replaced_count"] == len(entries) and absent["replaced_count"] == old["replaced_count"] == 0
            and old["registry_status"] == "stale" and same_url["reason_counts"].get("registry_snapshot_mismatch") == 1
            and rebound["reason_counts"].get("stale_material") == 1, "fresh/old isolation proof failed")
    for held in (absent, old, same_url, rebound):
        require(held["preview_body_sha256"] == identity["body_sha256"], "held records altered original body")
    require({k: fresh[k] for k in record["expected"]} == record["expected"], "fresh replay outcome drift")
    report = {"version": VERSION, "source": pin, "coverage": fresh, "editorial_claim_count": claims,
              "actual_old_registry": old, "no_registry": absent,
              "synthetic_same_url_old_snapshot": same_url, "synthetic_rebound_old_material": rebound,
              "historical_inputs_unchanged": True, "replay_model_calls": 0,
              "initial_model_usage": record["initial_model_usage"], "delivery_eligible": False, "sent": False}
    page = automatic.render(fresh, email)
    note = ('<section class="automatic-summary"><h1>第21步：新快照小批量验收</h1>'
            '<p>2026-10-21 日报 · 32条选文中核对2篇摘要、保留其余30条原文。旧10篇稿在此快照复用0篇。</p>'
            '<p>来源为已完成的正式采集记录39518483125；本页是未发送的影子候选邮件。'
            '原始采集审计仍为 FAILED：16项警报、132项缺口，不代表整封日报已验收。</p>'
            '<p>两篇均仅依据本轮捕获摘要，日期沿用RSS交叉收录公告字段，未认证首次发表日期。'
            '捕获文本中重复的摘要与引句不是独立证据。实验结论为作者报告，尚未核验全文、数据或回测。</p>'
            '<p>已逐项核对16项标题、事实、限制与追问；4次模型请求，供应商报告9741总token。'
            '本页回放不调用模型。仅完成静态结构与链接检查，尚未做邮件客户端视觉验收。</p></section>')
    page = page.replace('<main class="email-main">', '<main class="email-main">' + note)
    return report, page


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("GitHub metadata redirect refused")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("verify-source", "replay"))
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("--source-dir", type=Path)
    parser.add_argument("--old-registry", type=Path)
    parser.add_argument("--old-index", type=Path)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args(argv);record = cards.read_json(args.record)
    require(not args.out_dir.exists(), "fresh proof output must be new")
    if args.command == "verify-source":
        pin = record["source"];repo = os.environ["GITHUB_REPOSITORY"]
        require(re.fullmatch(r"[\w.-]+/[\w.-]+", repo) is not None
                and all(isinstance(pin[k], int) and pin[k] > 0 for k in ("run_id", "artifact_id")), "invalid source endpoint")
        opener = urllib.request.build_opener(NoRedirect())
        def fetch(path):
            request = urllib.request.Request("https://api.github.com/repos/" + repo + "/actions/" + path,
                headers={"Authorization": "Bearer " + os.environ["GH_TOKEN"], "Accept": "application/vnd.github+json",
                         "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "etf-fresh-shadow"})
            with opener.open(request, timeout=30) as response:return json.load(response)
        proof = verify_source(fetch("runs/" + str(pin["run_id"])), fetch("artifacts/" + str(pin["artifact_id"])),
                              pin, repo, os.environ["DEFAULT_BRANCH"])
        args.out_dir.mkdir(parents=True);cards.write_json(args.out_dir / "source-verification.json", proof)
    else:
        metadata, manifest, history = read_source(args.source_dir, record)
        proof, page = build(metadata, manifest, history, record, cards.read_json(args.old_registry), cards.read_json(args.old_index))
        args.out_dir.mkdir(parents=True);cards.write_json(args.out_dir / "fresh-shadow-proof.json", proof)
        (args.out_dir / "fresh-shadow-preview.html").write_text(page, encoding="utf-8")
        print(json.dumps({"selected": proof["coverage"]["selected_count"], "replaced": proof["coverage"]["replaced_count"],
                          "editorial_claims": proof["editorial_claim_count"], "model_calls": 0, "sent": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
