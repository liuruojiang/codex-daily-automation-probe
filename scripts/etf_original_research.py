"""Replay original-source research against fixed capture identities, never mail."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import urllib.request
from unittest.mock import patch

import etf_automatic_mail_preview as automatic
import etf_card_repair as repair
import etf_content_cards as cards
import etf_editorial_packet as editorial
import etf_original_sources as original
import etf_semantic_review as semantic
import etf_source_intake as intake

VERSION = "etf-original-research-v1"
require = original.require


def selected_sources(parent):
    return [row for row in parent["original_sources"]
            if row["extraction"]["material_id"] is not None and not row["extraction"]["pending_flags"]]


def build(materials, record, parent, registry):
    require(record["version"] == VERSION and record["parent_research_sha256"] == cards.digest(parent), "parent research changed")
    require(record["registry_sha256"] == cards.digest(registry) == parent["registry_sha256"]
            and record["snapshot"] == parent["snapshot"] == registry["snapshot"], "original research snapshot/registry changed")
    require(record["delivery_eligible"] is False and record["mail_approvals_added"] == 0
            and record["external_fact_check"] == "not_performed", "research cannot approve mail or external facts")
    sources = selected_sources(parent)
    ids = {s["extraction"]["material_id"] for s in sources}
    by_id = {m["material_id"]: m for m in materials}
    entries = {e["material_id"]: e for e in record["entries"]}
    require(len(ids) == len(sources) == len(materials) == len(by_id) == len(record["entries"]) == len(entries)
            and set(by_id) == set(entries) == ids, "original research coverage mismatch")
    require(set(record["repairs"]) <= ids, "unknown research repair")
    require(set(record["source_review_status"]) == set(record["initial_card_sha256"]) == ids, "original decision coverage mismatch")
    rows = []
    for source in sources:
        saved = source["extraction"];mid = saved["material_id"];material = by_id[mid];entry = entries[mid]
        cards.check_material(material)
        require(saved["access_scope"] == "public_html_excerpt" and saved["date_status"] == "source_timestamp_with_zone"
                and saved["tables_and_figures_extracted"] is False and saved["complete_article_claim"] is False,
                "source scope is not an eligible fixed excerpt")
        require(material["url"] == saved["url"] == entry["url"] and material["title"] == saved["actual_title"]
                and material["published"] == saved["published_raw"]
                and cards.digest([p["text"] for p in material["paragraphs"]]) == saved["paragraph_text_sha256"],
                "original source body/date identity changed")
        card = automatic.decode_entry(entry, material)["cards"][mid]
        ledger = record["repairs"].get(mid)
        if ledger is not None:
            previous = cards.expand_evidence_ids(material, ledger["parent_card"])
            require(cards.digest(previous) == record["initial_card_sha256"][mid], "original card identity changed")
            require(ledger["parent_evaluation"] == semantic.evaluate(material, previous, ledger["parent_review"]),
                    "original research decision changed")
            require(ledger["parent_veto"] is not None, "original research editor veto removed")
            rebuilt = repair.revise(material, previous, ledger["plan"], ledger["parent_veto"])
            require(rebuilt["card"] == card and rebuilt["revision_id"] == ledger["revision_id"], "original research revision changed")
            repair_result = repair.evaluate(rebuilt, material, entry["review"], ledger["disposition"])
            require(repair_result["status"] in ("shadow_review_passed", "semantic_revision_required", "editorial_revision_required"),
                    "original research issue/model gates incomplete")
        else:
            require(cards.digest(card) == record["initial_card_sha256"][mid], "changed card lost its repair history")
        packet = editorial.evaluate(material, card, entry["review"], entry["editor"])
        if ledger is not None and packet["status"] == automatic.preview.COMPLETE:
            require(repair_result["status"] == "shadow_review_passed", "original research issue/model gates incomplete")
        require(packet["status"] in (automatic.preview.COMPLETE, "semantic_resolution_required", "editorial_revision_required")
                and packet["status"] == record["source_review_status"][mid], "original research source editing incomplete or status inflated")
        rows.append({"material_id": mid, "url": material["url"], "published": material["published"],
                     "card_hash": cards.digest(card), "status": packet["status"],
                     "editorial_claim_count": len(entry["editor"]["checks"]),
                     "repaired": ledger is not None,
                     "model_overall": entry["review"]["overall"],
                     "unresolved_model_claims": [r["claim_id"] for r in entry["review"]["checks"] if r["verdict"] != "supported"],
                     "original_evidence_errors": ledger["parent_evaluation"].get("errors", []) if ledger else [],
                     "resolved_writing_issues": len(ledger["disposition"]["checks"]) if ledger else 0})
    return {"version": "etf-original-research-proof-v1", "items": rows,
            "research_completed": sum(r["status"] == automatic.preview.COMPLETE for r in rows),
            "research_held": sum(r["status"] != automatic.preview.COMPLETE for r in rows),
            "repaired_cards": sum(r["repaired"] for r in rows),
            "editorial_claim_count": sum(r["editorial_claim_count"] for r in rows),
            "original_registry_entries_preserved": len(registry["entries"]),
            "previous_paper_research_preserved": True, "external_fact_check": "not_performed",
            "delivery_eligible": False, "mail_approvals_added": 0}


def fetch_source(url):
    automatic.preview.safe_link(url)
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; ETFSourceIntake/1.0)",
                                                  "Accept": "text/html,application/xhtml+xml"})
    with urllib.request.urlopen(request, timeout=30) as response:
        require(response.geturl() == url, "original source redirected")
        raw = response.read(4000001)
    require(len(raw) <= 4000000, "original source too large")
    return raw


def rebuild_sources(parent, raw_dir=None):
    materials = []
    for row in selected_sources(parent):
        saved = row["extraction"];url = saved["url"]
        raw = ((raw_dir / hashlib.sha256(url.encode()).hexdigest() / "response.html").read_bytes()
               if raw_dir is not None else fetch_source(url))
        material, extraction = original.extract({"url": url, "parent_material_id": saved["parent_directory_material_id"]},
                                                raw, saved["extraction_spec"])
        # Navigation, ads and other excluded HTML may change; never silently
        # replace source paragraphs, publication date or sentence identities.
        require({k: v for k, v in extraction.items() if k != "raw_sha256"}
                == {k: v for k, v in saved.items() if k != "raw_sha256"}, "original source extraction changed")
        materials.append(material)
    return materials


def probe(source, registry_path, parent_path, record_path, report_path, raw_dir=None):
    require(not report_path.exists(), "original research proof output already exists")
    paths = [source/"metadata.json", source/"collection_manifest.json", source/"history_before.json", registry_path, parent_path, record_path]
    before = [intake.digest_bytes(p.read_bytes()) for p in paths]
    metadata, manifest, history, registry, parent, record = [cards.read_json(p) for p in paths]
    materials = rebuild_sources(parent, raw_dir)
    def forbidden(*args, **kwargs):raise AssertionError("research replay must not call a model")
    with patch.object(cards, "call_json", side_effect=forbidden):
        proof = build(materials, record, parent, registry)
        require(build(materials, record, parent, registry) == proof, "original research replay differs")
        baseline, body = automatic.build(metadata, manifest, history, registry)
        injected = copy.deepcopy(registry);injected["entries"].extend(copy.deepcopy(record["entries"]))
        report, injected_body = automatic.build(metadata, manifest, history, injected)
        require(report["replaced_count"] == baseline["replaced_count"] == len(registry["entries"])
                and body == injected_body and all(r["reason"] == "not_selected" and r["status"] == "unused"
                                                for r in report["registry_entries"][-len(materials):]),
                "unselected original research entered frozen mail")
    require(before == [intake.digest_bytes(p.read_bytes()) for p in paths], "original research inputs changed")
    proof.update(source_inputs_unchanged=True, replay_identical=True, model_requests=0,
                 live_public_fetch=raw_dir is None, synthetic_frozen_gate_test="not_selected",
                 mail_replacements_remain=report["replaced_count"], frozen_mail_body_unchanged=True)
    cards.write_json(report_path, proof);print(json.dumps(proof))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source-dir", "registry", "parent-record", "record", "report"):
        parser.add_argument("--"+name, type=Path, required=True)
    parser.add_argument("--raw-dir", type=Path);args = parser.parse_args()
    probe(args.source_dir, args.registry, args.parent_record, args.record, args.report, args.raw_dir)


if __name__ == "__main__":main()
