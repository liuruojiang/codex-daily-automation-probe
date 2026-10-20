"""Rebuild a separately reviewed later supplement; never add frozen mail approval."""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
from pathlib import Path
import re
import urllib.request
from unittest.mock import patch

import etf_automatic_mail_preview as automatic
import etf_card_repair as repair
import etf_content_cards as cards
import etf_editorial_packet as editorial
import etf_semantic_review as semantic
import etf_source_intake as intake

VERSION = "etf-supplement-research-v1"


def build(material, record, intake_audit, registry):
    require = automatic.preview.require
    require(record["version"] == VERSION and record["intake_audit_sha256"] == cards.digest(intake_audit), "supplement intake context changed")
    require(record["snapshot"] == intake_audit["snapshot"] and record["registry_sha256"] == cards.digest(registry) == intake_audit["registry_sha256"], "supplement registry/snapshot changed")
    require(record["delivery_eligible"] is False and record["frozen_mail_admission"] == "held_later_capture", "supplement cannot approve frozen mail")
    cards.check_material(material);mid = material["material_id"]
    saved = next(s for s in intake_audit["supplements"] if s["material_id"] == mid)
    require(material["published"] == saved["original_published"], "supplement publication field changed")
    entry, ledger = record["entry"], record["repair"]
    require(entry["material_id"] == mid and automatic.canonical(entry["url"]) == automatic.canonical(material["url"]), "supplement entry identity changed")
    card = automatic.decode_entry(entry, material)["cards"][mid]
    parent = automatic.decode_entry({**entry, "card": ledger["parent_card"]}, material)["cards"][mid]
    require(not semantic.validate_review(material, parent, ledger["parent_review"]), "invalid original supplement review")
    rebuilt = repair.revise(material, parent, ledger["plan"], ledger["parent_veto"])
    require(rebuilt["revision_id"] == ledger["revision_id"] and rebuilt["card"] == card, "supplement revision mismatch")
    require(repair.evaluate(rebuilt, material, entry["review"], ledger["disposition"])["status"] == "shadow_review_passed", "supplement repair gates incomplete")
    packet = editorial.evaluate(material, card, entry["review"], entry["editor"])
    require(packet["status"] == automatic.preview.COMPLETE, "supplement source editing incomplete")
    leads, captured = intake_audit["source_leads"], record["original_sources"]
    require(len(captured) == len(leads), "original source coverage mismatch")
    for lead, row in zip(leads, captured):
        source = row["extraction"]
        require(row["requested_url"] == source["url"] == lead["url"] and row["response_status"] == 200, "original source capture mismatch")
        require(source["parent_directory_material_id"] == lead["parent_material_id"] and source["raw_sha256"] == row["body_sha256"], "original source provenance mismatch")
        require(dt.datetime.fromisoformat(row["finished_utc"]) >= dt.datetime.fromisoformat(row["started_utc"]) > dt.datetime.fromisoformat(record["snapshot"]["cutoff_utc"]), "original source capture time invalid")
        require(source["delivery_eligible"] is False and source["review_status"] == "pending" and source["mail_approvals_added"] == 0, "unreviewed original source claimed approval")
        require(source["date_basis"] in ("article:published_time", "JSON-LD datePublished", "published time element"), "directory publication date inherited")
        stamp = dt.datetime.fromisoformat(source["published_raw"].replace("Z", "+00:00"))
        require(source["date_status"] == ("timezone_unconfirmed" if stamp.tzinfo is None else "source_timestamp_with_zone"), "original source time zone guessed")
        require(source["access_scope"] in ("public_html_excerpt", "metadata_only_subscription_marker"), "unknown source scope")
        if source["access_scope"] == "metadata_only_subscription_marker":
            require(source["material_id"] is None and source["paragraph_count"] == source["text_chars"] == 0, "restricted source became body evidence")
        else:
            require(isinstance(source["material_id"], str) and source["paragraph_count"] > 0 and source["complete_article_claim"] is False, "invalid public excerpt")
    return {"version": "etf-supplement-research-proof-v1", "material_id": mid, "card_hash": cards.digest(card),
            "status": packet["status"], "editorial_claim_count": len(entry["editor"]["checks"]),
            "resolved_writing_issues": len(ledger["disposition"]["checks"]), "original_model_overall": ledger["parent_review"]["overall"],
            "original_editor_veto_preserved": ledger["parent_veto"] is not None,
            "original_sources_captured": len(captured), "public_excerpts_prepared": sum(s["extraction"]["material_id"] is not None for s in captured),
            "metadata_only_sources": sum(s["extraction"]["material_id"] is None for s in captured),
            "external_fact_check": "not_performed", "delivery_eligible": False, "frozen_mail_admission": "held_later_capture"}


def probe(source, registry_path, intake_path, record_path, report_path, raw_html=None):
    require = automatic.preview.require
    require(not report_path.exists(), "supplement proof output already exists")
    paths = [source/"metadata.json", source/"collection_manifest.json", source/"history_before.json", registry_path, intake_path, record_path]
    before = [intake.digest_bytes(p.read_bytes()) for p in paths]
    metadata, manifest, history, registry, audit, record = [cards.read_json(p) for p in paths]
    intake.validate_audit(audit, manifest, history, registry)
    saved = next(s for s in audit["supplements"] if s["material_id"] == record["entry"]["material_id"])
    if raw_html is not None:raw = raw_html.read_bytes()
    else:
        require(re.fullmatch(r"https://arxiv\.org/html/\d{4}\.\d{4,5}v\d+", saved["source_url"]), "unexpected supplement URL")
        with urllib.request.urlopen(saved["source_url"], timeout=30) as response:
            require(response.geturl() == saved["source_url"], "unexpected source redirect");raw = response.read(4000001)
        require(len(raw) <= 4000000, "supplement too large")
    parent = next(m for m in cards.prepare_manifest(manifest) if m["material_id"] == saved["parent_material_id"])
    material, _ = intake.extract_arxiv(parent, raw, saved["source_url"], saved["paragraph_ids"])
    require(material["material_id"] == saved["material_id"], "supplement body changed")
    def forbidden(*args, **kwargs):raise AssertionError("supplement replay must not call a model")
    with patch.object(cards, "call_json", side_effect=forbidden):
        proof = build(material, record, audit, registry)
        require(build(material, record, audit, registry) == proof, "supplement replay differs")
        injected = copy.deepcopy(registry);injected["entries"].append(copy.deepcopy(record["entry"]))
        report, _ = automatic.build(metadata, manifest, history, injected)
        require(report["replaced_count"] == len(registry["entries"]) and report["reason_counts"].get("stale_material") == 1, "reviewed later supplement entered frozen mail")
    require(before == [intake.digest_bytes(p.read_bytes()) for p in paths], "supplement inputs changed")
    proof.update(source_inputs_unchanged=True, reviewed_registry_unchanged=True, replay_identical=True, model_requests=0,
                 live_public_fetch=raw_html is None, synthetic_frozen_gate_test="stale_material", mail_replacements_remain=report["replaced_count"])
    cards.write_json(report_path, proof);print(json.dumps(proof))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source-dir", "registry", "intake-audit", "record", "report"):parser.add_argument("--"+name, type=Path, required=True)
    parser.add_argument("--raw-html", type=Path);args = parser.parse_args()
    probe(args.source_dir, args.registry, args.intake_audit, args.record, args.report, args.raw_html)


if __name__ == "__main__":main()
