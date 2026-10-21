"""A separate revision of held research, preserving the old rejection and mail."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from unittest.mock import patch

import etf_automatic_mail_preview as automatic
import etf_card_repair as repair
import etf_content_cards as cards
import etf_editorial_packet as editorial
import etf_original_research as previous
import etf_original_sources as original
import etf_semantic_review as semantic
import etf_source_intake as intake
import etf_research_history as research_history

VERSION = "etf-research-resolution-v1"
require = original.require


def build(material, record, prior, source_record, registry):
    require(record["version"] == VERSION and prior["version"] == previous.VERSION
            and record["prior_research_sha256"] == cards.digest(prior)
            and record["source_record_sha256"] == prior["parent_research_sha256"] == cards.digest(source_record),
            "resolution prior research changed")
    require(record["registry_sha256"] == prior["registry_sha256"] == source_record["registry_sha256"] == cards.digest(registry)
            and record["snapshot"] == prior["snapshot"] == source_record["snapshot"] == registry["snapshot"],
            "resolution registry/snapshot changed")
    require(record["delivery_eligible"] is False and record["mail_approvals_added"] == 0
            and record["external_fact_check"] == "not_performed", "resolution cannot approve mail or external facts")
    cards.check_material(material);mid = material["material_id"]
    saved = next(s["extraction"] for s in previous.selected_sources(source_record) if s["extraction"]["material_id"] == mid)
    require(material["title"] == saved["actual_title"] and material["url"] == saved["url"]
            and material["published"] == saved["published_raw"] and material["evidence_level"] == "captured_excerpt"
            and cards.digest([p["text"] for p in material["paragraphs"]]) == saved["paragraph_text_sha256"],
            "resolution original source identity changed")
    matches = [e for e in prior["entries"] if e["material_id"] == mid]
    require(len(matches) == 1, "resolution needs one exact prior entry")
    parent_entry = matches[0];parent = automatic.decode_entry(parent_entry, material)["cards"][mid]
    research_history.classify_legacy_review(material, parent, parent_entry["review"])
    require(parent_entry["review"]["overall"] == "revise", "resolution must preserve a valid original rejection")
    old_packet = research_history.historical_packet(material, parent, parent_entry["review"], parent_entry["editor"])
    require(old_packet["status"] in ("editorial_revision_required", "semantic_resolution_required")
            and old_packet["status"] == prior["source_review_status"][mid], "prior held status changed")
    entry, ledger = record["entry"], record["repair"]
    require(entry["material_id"] == mid and entry["url"] == material["url"], "resolution entry identity changed")
    current = automatic.decode_entry(entry, material)["cards"][mid]
    require(ledger["parent_veto"] is not None, "resolution original editor veto removed")
    rebuilt = repair.revise(material, parent, ledger["plan"], ledger["parent_veto"])
    require(rebuilt["revision_id"] == ledger["revision_id"] and rebuilt["card"] == current, "resolution revision changed")
    failed = {r["claim_id"] for r in parent_entry["review"]["checks"] if r["verdict"] != "supported"}
    issue_ids = {i["id"] for i in ledger["plan"]["issues"]};seen_issues, seen_claims = set(), set()
    for row in record["issue_claims"]:
        require(set(row) == {"issue_id", "claim_ids", "action"} and row["issue_id"] in issue_ids
                and row["issue_id"] not in seen_issues and row["action"] == "revise"
                and isinstance(row["claim_ids"], list) and row["claim_ids"] and len(row["claim_ids"]) == len(set(row["claim_ids"]))
                and set(row["claim_ids"]) <= failed and not set(row["claim_ids"]) & seen_claims,
                "invalid rejected-claim resolution mapping")
        seen_issues.add(row["issue_id"]);seen_claims.update(row["claim_ids"])
    require(seen_issues == issue_ids and seen_claims == failed, "not every original rejection was addressed")
    old_claims, new_claims = semantic.claim_map(parent), semantic.claim_map(current)
    require(all(cid in new_claims and new_claims[cid] != old_claims[cid] for cid in failed),
            "rejected claims must actually change before a fresh review")
    require(research_history.historical_repair_passed(repair.evaluate(rebuilt, material, entry["review"], ledger["disposition"])),
            "resolution issue/model gates incomplete")
    packet = research_history.historical_packet(material, current, entry["review"], entry["editor"])
    require(packet["status"] == automatic.preview.COMPLETE, "resolution source editing incomplete")
    effective_complete = 1 + sum(status == automatic.preview.COMPLETE for other, status in prior["source_review_status"].items() if other != mid)
    return {"version": "etf-research-resolution-proof-v1", "material_id": mid,
            "parent_card_hash": cards.digest(parent), "card_hash": cards.digest(current),
            "original_model_overall": parent_entry["review"]["overall"], "original_held_status": old_packet["status"],
            "original_refused_claims": sorted(failed), "resolved_writing_issues": len(issue_ids),
            "current_model_overall": entry["review"]["overall"], **research_history.current_fields(packet),
            "editorial_claim_count": len(entry["editor"]["checks"]), "original_registry_entries_preserved": len(registry["entries"]),
            "historical_effective_original_research_complete": effective_complete,
            "historical_effective_original_research_held": len(prior["source_review_status"]) - effective_complete,
            "original_current_gate_status": old_packet["current_gate_status"],
            "original_current_gate_reasons": old_packet["current_gate_reasons"],
            "prior_record_unchanged": True, "previous_paper_research_preserved": True,
            "external_fact_check": "not_performed", "delivery_eligible": False, "mail_approvals_added": 0}


def rebuild_material(source_record, mid, raw_dir=None):
    selected = [s for s in previous.selected_sources(source_record) if s["extraction"]["material_id"] == mid]
    require(len(selected) == 1, "resolution source selection mismatch")
    return previous.rebuild_sources({"original_sources": selected}, raw_dir)[0]


def probe(source, registry_path, source_record_path, prior_path, record_path, report_path, raw_dir=None):
    require(not report_path.exists(), "resolution proof output already exists")
    paths = [source/"metadata.json", source/"collection_manifest.json", source/"history_before.json",
             registry_path, source_record_path, prior_path, record_path]
    before = [intake.digest_bytes(p.read_bytes()) for p in paths]
    metadata, manifest, history, registry, source_record, prior, record = [cards.read_json(p) for p in paths]
    material = rebuild_material(source_record, record["entry"]["material_id"], raw_dir)
    def forbidden(*args, **kwargs):raise AssertionError("resolution replay must not call a model")
    with patch.object(cards, "call_json", side_effect=forbidden):
        proof = build(material, record, prior, source_record, registry)
        require(build(material, record, prior, source_record, registry) == proof, "resolution replay differs")
        baseline, body = automatic.build(metadata, manifest, history, registry)
        injected = copy.deepcopy(registry);injected["entries"].append(copy.deepcopy(record["entry"]))
        report, new_body = automatic.build(metadata, manifest, history, injected)
        require(report["replaced_count"] == baseline["replaced_count"] == len(registry["entries"])
                and new_body == body and report["registry_entries"][-1]["status"] == "unused"
                and report["registry_entries"][-1]["reason"] == "not_selected", "resolved research entered frozen mail")
    require(before == [intake.digest_bytes(p.read_bytes()) for p in paths], "resolution inputs changed")
    proof.update(source_inputs_unchanged=True, replay_identical=True, model_requests=0, live_public_fetch=raw_dir is None,
                 synthetic_frozen_gate_test="not_selected", mail_replacements_remain=report["replaced_count"], frozen_mail_body_unchanged=True)
    cards.write_json(report_path, proof);print(json.dumps(proof))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source-dir", "registry", "source-record", "prior-record", "record", "report"):
        parser.add_argument("--"+name, type=Path, required=True)
    parser.add_argument("--raw-dir", type=Path);args = parser.parse_args()
    probe(args.source_dir, args.registry, args.source_record, args.prior_record, args.record, args.report, args.raw_dir)


if __name__ == "__main__":main()
