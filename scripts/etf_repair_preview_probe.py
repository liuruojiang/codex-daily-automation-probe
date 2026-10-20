"""Rebuild reviewed repairs against the saved source, without paid requests."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import urllib.request
from unittest.mock import patch

import etf_automatic_mail_preview as automatic
import etf_card_repair as repair
import etf_content_cards as cards
import etf_editorial_packet as editorial
import etf_semantic_review as semantic

VERSION = "etf-reviewed-repair-audit-v1"


def build(audit, registry, manifest, history):
    require = automatic.preview.require
    require(isinstance(audit, dict) and set(audit) == {"version", "snapshot", "parent_registry_sha256",
            "preserved_entry_sha256", "records", "review_batch"} and audit["version"] == VERSION, "invalid repair audit")
    require(registry["version"] == automatic.REGISTRY_VERSION and
            audit["snapshot"] == registry["snapshot"] == automatic.snapshot_identity(manifest, history), "repair snapshot mismatch")
    saved, records, entries = audit["preserved_entry_sha256"], audit["records"], registry["entries"]
    require(isinstance(saved, list) and saved and isinstance(records, list) and records and
            len(entries) == len(saved) + len(records), "repair coverage mismatch")
    require([cards.digest(entry) for entry in entries[:len(saved)]] == saved, "preserved registry entries changed")
    parent = {**registry, "entries": entries[:len(saved)]}
    require(cards.digest(parent) == audit["parent_registry_sha256"], "parent registry mismatch")
    new = entries[len(saved):]
    mids = [record["material_id"] for record in records]
    require(all(isinstance(mid, str) for mid in mids) and len(set(mids)) == len(mids) and
            set(mids) == {entry["material_id"] for entry in new}, "duplicate or unknown repair record")
    materials = {m["material_id"]: m for m in cards.prepare_manifest(manifest)}
    by_id = {entry["material_id"]: entry for entry in new}
    require(len(by_id) == len(new), "duplicate repaired entry")
    rows = []
    for record in records:
        require(set(record) == {"material_id", "revision_id", "parent_card", "parent_review", "parent_veto", "plan",
                "card_hash", "review_hash", "disposition", "editor_hash"}, "invalid repair record")
        mid = record["material_id"]
        require(mid in materials, "repair material absent from current capture")
        material, entry = materials[mid], by_id[mid]
        require(automatic.canonical(entry["url"]) == automatic.canonical(material["url"]), "repair source URL mismatch")
        old = automatic.decode_entry({**entry, "card": record["parent_card"]}, material)["cards"][mid]
        require(not cards.validate_card(material, old) and
                not semantic.validate_review(material, old, record["parent_review"]), "invalid parent review/card")
        rebuilt = repair.revise(material, old, record["plan"], record["parent_veto"])
        card = automatic.decode_entry(entry, material)["cards"][mid]
        require(rebuilt["revision_id"] == record["revision_id"] and rebuilt["card_hash"] == record["card_hash"] and
                rebuilt["card"] == card, "repair ledger or current card mismatch")
        require(cards.digest(entry["review"]) == record["review_hash"] and
                cards.digest(entry["editor"]) == record["editor_hash"], "repair current review/editor mismatch")
        opinion = repair.evaluate(rebuilt, material, entry["review"], record["disposition"])
        require(opinion["status"] == "shadow_review_passed", "repair issue/model gates incomplete")
        packet = editorial.evaluate(material, card, entry["review"], entry["editor"])
        require(packet["status"] == automatic.preview.COMPLETE, "repair per-claim editing incomplete")
        rows.append({"material_id": mid, "revision_id": rebuilt["revision_id"], "parent_card_hash": rebuilt["parent_card_hash"],
                     "card_hash": rebuilt["card_hash"], "original_model_overall": record["parent_review"]["overall"],
                     "original_editor_veto_preserved": record["parent_veto"] is not None,
                     "changed_fields": sorted(rebuilt["changes"]), "resolved_issue_count": len(record["disposition"]["checks"]),
                     "editorial_claim_count": len(entry["editor"]["checks"]), "status": "source_review_complete_for_research_preview"})
    return {"version": "etf-repair-preview-proof-v1", "snapshot": audit["snapshot"], "parent_entries_preserved": len(saved),
            "repaired_entries_verified": len(rows), "items": rows, "delivery_eligible": False,
            "external_fact_check": "not_performed"}


def run(source, registry_path, audit_path, report_path):
    automatic.preview.require(not report_path.exists(), "repair proof output already exists")
    paths = [source/"collection_manifest.json", source/"history_before.json", registry_path, audit_path]
    before = [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]
    manifest, history, registry, audit = [cards.read_json(path) for path in paths]
    def forbidden(*args, **kwargs):
        raise AssertionError("repair proof must remain offline")
    with patch.object(cards, "call_json", side_effect=forbidden), patch.object(urllib.request, "urlopen", side_effect=forbidden):
        proof = build(audit, registry, manifest, history)
        automatic.preview.require(build(audit, registry, manifest, history) == proof, "repair replay differs")
    automatic.preview.require(before == [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths], "repair inputs changed")
    proof.update(source_inputs_unchanged=True, network_and_model_calls=0, replay_identical=True)
    cards.write_json(report_path, proof)
    print(json.dumps({k: proof[k] for k in ("version", "parent_entries_preserved", "repaired_entries_verified",
                      "source_inputs_unchanged", "network_and_model_calls", "replay_identical", "delivery_eligible")}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source-dir", "registry", "audit", "report"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    run(args.source_dir, args.registry, args.audit, args.report)


if __name__ == "__main__":
    main()
