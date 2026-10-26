"""Explicit whole-heading excerpts from a fixed public source; research only."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from bs4 import BeautifulSoup

import etf_automatic_mail_preview as automatic
import etf_card_repair as repair
import etf_content_cards as cards
import etf_editorial_packet as editorial
import etf_original_research as original_research
import etf_original_sources as original
import etf_semantic_review as semantic
import etf_source_intake as intake
import etf_research_history as research_history

VERSION = "etf-section-research-v1"
SELECTION_VERSION = "etf-whole-section-selection-v1"
require = original.require


def select_sections(parent, headings, titles):
    cards.check_material(parent)
    positions = {p["id"]: i for i, p in enumerate(parent["paragraphs"])}
    require(isinstance(headings, list) and headings, "explicit top-level headings required")
    offsets, seen_titles = [], set()
    for heading in headings:
        require(set(heading) == {"title", "parent_id"} and heading["parent_id"] in positions
                and heading["title"] not in seen_titles, "ambiguous section heading")
        offset = positions[heading["parent_id"]]
        require(parent["paragraphs"][offset]["text"] == heading["title"] and (not offsets or offset > offsets[-1]),
                "section heading order/text mismatch")
        offsets.append(offset);seen_titles.add(heading["title"])
    require(isinstance(titles, list) and titles and len(titles) == len(set(titles)) and set(titles) <= seen_titles,
            "unknown or duplicate selected section")
    chosen = [i for i, h in enumerate(headings) if h["title"] in titles]
    require([headings[i]["title"] for i in chosen] == titles, "selected sections must retain source order")
    blocks, sections, mapping = [], [], []
    for index in chosen:
        start = offsets[index];stop = offsets[index+1] if index+1 < len(offsets) else len(parent["paragraphs"])
        group = parent["paragraphs"][start:stop]
        sections.append({"title": headings[index]["title"], "start_parent_id": group[0]["id"],
                         "end_parent_id": group[-1]["id"], "next_heading_parent_id": headings[index+1]["parent_id"] if index+1 < len(headings) else None,
                         "paragraph_count": len(group)})
        for paragraph in group:
            blocks.append(paragraph["text"]);mapping.append({"id": "p"+str(len(blocks)), "parent_id": paragraph["id"]})
    material = cards.make_material(title=parent["title"], url=parent["url"], source=parent["source"],
                                   published=parent["published"], paragraphs=blocks, evidence_level="captured_excerpt")
    payload_chars = len(json.dumps(cards.request_payload(material, "PLAN_ONLY", 2400, "disabled"), ensure_ascii=False))
    require(payload_chars <= 60000, "explicit whole-section selection still exceeds input limit")
    selection = {"version": SELECTION_VERSION, "parent_material_id": parent["material_id"], "material_id": material["material_id"],
                 "headings": copy.deepcopy(headings), "selected_titles": titles[:], "sections": sections, "paragraph_map": mapping,
                 "parent_paragraph_count": len(parent["paragraphs"]), "selected_paragraph_count": len(blocks),
                 "selected_text_chars": sum(map(len, blocks)), "generation_payload_chars": payload_chars,
                 "excluded_top_level_sections": [h["title"] for h in headings if h["title"] not in titles],
                 "complete_article_claim": False, "tables_and_figures_extracted": False, "automatic_truncation": False}
    return material, selection


def extract_sections(source_record, parent_id, raw, titles):
    matches = [r["extraction"] for r in source_record["original_sources"] if r["extraction"]["material_id"] == parent_id]
    require(len(matches) == 1, "whole-section parent source mismatch")
    saved = matches[0]
    require(saved["access_scope"] == "public_html_excerpt" and saved["date_status"] == "source_timestamp_with_zone",
            "whole-section source date/access held")
    parent, current = original.extract({"url": saved["url"], "parent_material_id": saved["parent_directory_material_id"]}, raw, saved["extraction_spec"])
    require({k: v for k, v in current.items() if k != "raw_sha256"} == {k: v for k, v in saved.items() if k != "raw_sha256"},
            "whole-section parent body/date changed")
    soup = BeautifulSoup(raw.decode("utf-8-sig"), "html.parser");roots = soup.select(saved["extraction_spec"]["body_selector"])
    require(len(roots) == 1, "whole-section body ambiguous")
    # Only headings represented in the already validated visible-text capture
    # can define a boundary. Hidden/duplicate/unmatched headings fail closed.
    headings = []
    for node in roots[0].find_all("h2"):
        title = " ".join(node.get_text(" ", strip=True).split())
        paragraphs = [p for p in parent["paragraphs"] if p["text"] == title]
        require(len(paragraphs) == 1, "top-level heading not unique in fixed capture")
        headings.append({"title": title, "parent_id": paragraphs[0]["id"]})
    material, selection = select_sections(parent, headings, titles)
    return parent, material, selection


def build(parent, material, record, source_record, prior_resolution, registry):
    require(record["version"] == VERSION and record["source_record_sha256"] == cards.digest(source_record)
            and record["prior_resolution_sha256"] == cards.digest(prior_resolution), "whole-section research context changed")
    require(record["registry_sha256"] == source_record["registry_sha256"] == prior_resolution["registry_sha256"] == cards.digest(registry)
            and record["snapshot"] == registry["snapshot"] == source_record["snapshot"] == prior_resolution["snapshot"],
            "whole-section registry/snapshot changed")
    require(record["delivery_eligible"] is False and record["mail_approvals_added"] == 0
            and record["external_fact_check"] == "not_performed", "whole-section research cannot approve mail or external facts")
    chosen, selection = select_sections(parent, record["selection"]["headings"], record["selection"]["selected_titles"])
    require(selection == record["selection"] and chosen == material, "whole-section boundaries/material changed")
    saved = next(r["extraction"] for r in source_record["original_sources"] if r["extraction"]["material_id"] == parent["material_id"])
    require(saved["date_status"] == "source_timestamp_with_zone" and saved["access_scope"] == "public_html_excerpt"
            and parent["url"] == saved["url"] and parent["published"] == saved["published_raw"]
            and parent["title"] == saved["actual_title"], "whole-section original identity changed")
    entry = record["entry"];mid = material["material_id"]
    require(entry["material_id"] == mid and entry["url"] == material["url"], "whole-section entry identity changed")
    card = automatic.decode_entry(entry, material)["cards"][mid]
    ledger = record["repair"]
    if ledger is not None:
        previous = cards.expand_evidence_ids(material, ledger["parent_card"])
        require(cards.digest(previous) == record["initial_card_sha256"], "whole-section initial card changed")
        require(not semantic.validate_review(material, previous, ledger["parent_review"]), "whole-section original model review changed")
        require(ledger["parent_veto"] is not None, "whole-section original editor veto removed")
        rebuilt = repair.revise(material, previous, ledger["plan"], ledger["parent_veto"])
        require(rebuilt["revision_id"] == ledger["revision_id"] and rebuilt["card"] == card, "whole-section repair changed")
        require(research_history.historical_repair_passed(repair.evaluate(rebuilt, material, entry["review"], ledger["disposition"])),
                "whole-section repair/model gates incomplete")
    else:require(cards.digest(card) == record["initial_card_sha256"], "whole-section changed card lost repair history")
    packet = research_history.historical_packet(material, card, entry["review"], entry["editor"])
    require(packet["status"] == automatic.preview.COMPLETE, "whole-section source editing incomplete")
    return {"version": "etf-section-research-proof-v1", "material_id": mid, "parent_material_id": parent["material_id"],
            "selected_sections": selection["selected_titles"], "selected_paragraph_count": len(material["paragraphs"]),
            "card_hash": cards.digest(card), **research_history.current_fields(packet), "editorial_claim_count": len(entry["editor"]["checks"]),
            "original_registry_entries_preserved": len(registry["entries"]), "automatic_truncation": False,
            "complete_article_claim": False, "external_fact_check": "not_performed", "delivery_eligible": False, "mail_approvals_added": 0}


def probe(source, registry_path, source_record_path, prior_path, record_path, report_path, raw_dir=None):
    require(not report_path.exists(), "whole-section proof output already exists")
    paths = [source/"metadata.json", source/"collection_manifest.json", source/"history_before.json",
             registry_path, source_record_path, prior_path, record_path]
    before = [intake.digest_bytes(p.read_bytes()) for p in paths]
    metadata, manifest, history, registry, source_record, prior, record = [cards.read_json(p) for p in paths]
    saved = next(r["extraction"] for r in source_record["original_sources"] if r["extraction"]["material_id"] == record["selection"]["parent_material_id"])
    url = saved["url"]
    raw = ((raw_dir/hashlib.sha256(url.encode()).hexdigest()/"response.html").read_bytes() if raw_dir is not None else original_research.fetch_source(url))
    parent, material, selection = extract_sections(source_record, saved["material_id"], raw, record["selection"]["selected_titles"])
    require(selection == record["selection"], "reconstructed whole-section selection changed")
    def forbidden(*args, **kwargs):raise AssertionError("whole-section replay must not call a model")
    with patch.object(cards, "call_json", side_effect=forbidden):
        proof = build(parent, material, record, source_record, prior, registry)
        require(build(parent, material, record, source_record, prior, registry) == proof, "whole-section replay differs")
        baseline, body = automatic.build(metadata, manifest, history, registry)
        injected = copy.deepcopy(registry);injected["entries"].append(copy.deepcopy(record["entry"]))
        report, new_body = automatic.build(metadata, manifest, history, injected)
        require(report["replaced_count"] == baseline["replaced_count"] == len(registry["entries"]) and body == new_body
                and report["registry_entries"][-1]["status"] == "unused" and report["registry_entries"][-1]["reason"] == "not_selected",
                "whole-section research entered frozen mail")
    require(before == [intake.digest_bytes(p.read_bytes()) for p in paths], "whole-section inputs changed")
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
