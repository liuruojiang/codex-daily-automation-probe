"""Complete per-claim source editing for a research preview, never email delivery."""
from __future__ import annotations

import etf_content_cards as cards
import etf_material_kind as material_kind
import etf_semantic_review as semantic
import etf_review_appeal as appeals

VERSION = "etf-editorial-packet-v1"


def evaluate(material: dict, card: dict, review: dict, editor: dict | None,
             appeal_case: dict | None = None, appeal_result: dict | None = None) -> dict:
    cards.check_material(material)
    row = {"version": VERSION, "material_id": material["material_id"], "card_hash": cards.digest(card),
           "review_hash": cards.digest(review), "preview_purpose": "research_sample", "delivery_eligible": False,
           "external_fact_check": "not_performed", "evidence_level": material["evidence_level"]}
    profile = material_kind.classify(material)
    if not profile["eligible"]:
        return {**row, "status": "source_text_required", "material_profile": profile}
    errors = cards.validate_card(material, card)
    if errors or semantic.terminology_issues(card):
        return {**row, "status": "blocked_by_evidence", "errors": errors or [i["rule"] for i in semantic.terminology_issues(card)]}
    errors = semantic.validate_review(material, card, review)
    if errors:return {**row, "status": "review_invalid", "errors": errors}
    model_supported = review["overall"] == "pass"
    appeal_id = None
    if appeal_case is not None or appeal_result is not None:
        if appeal_case is None or appeal_result is None or appeal_case["card_hash"] != row["card_hash"] or appeal_case["review_hash"] != row["review_hash"]:
            raise ValueError("appeal must bind the exact editorial card and review")
        opinion = appeals.evaluate(material, appeal_case, appeal_result)
        appeal_id = appeal_case["case_id"]
        row["appeal_status"] = opinion["status"]
        model_supported = opinion["status"] == "appeal_supports_editorial_recheck"
    if editor is None:
        return {**row, "status": "awaiting_editorial_review"}
    fields = {"material_id", "card_hash", "review_hash", "appeal_case_id", "checks"}
    if not isinstance(editor, dict) or set(editor) != fields or any(editor[k] != row[k] for k in ("material_id", "card_hash", "review_hash")) or editor["appeal_case_id"] != appeal_id:
        raise ValueError("stale or invalid editorial record")
    expected, seen = semantic.claim_map(card), set()
    units = {u["id"]: u["text"] for u in cards.evidence_units(material)}
    if not isinstance(editor["checks"], list):raise ValueError("invalid editorial checks")
    for check in editor["checks"]:
        if not isinstance(check, dict) or set(check) != {"claim_id", "decision", "reason_zh", "evidence_ids"}:
            raise ValueError("invalid editorial check")
        cid = check["claim_id"]
        if not isinstance(cid, str) or cid not in expected or cid in seen or check["decision"] not in ("confirmed", "needs_revision") or not cards.chinese(check["reason_zh"]):
            raise ValueError("unknown, duplicate or invalid editorial claim")
        ids = check["evidence_ids"]
        if not appeals.evidence_ids(ids, set(units) | {"source_title"}, required=check["decision"] == "confirmed" and cid.startswith(("facts.", "limitations."))):
            raise ValueError("invalid editorial evidence")
        if check["decision"] == "confirmed" and cid.startswith(("facts.", "limitations.")):
            own = [ref["quote"] for ref in expected[cid]["citations"]]
            if not any(uid in units and any(units[uid] in quote or quote in units[uid] for quote in own) for uid in ids):
                raise ValueError("editor must check the claim's own quotation")
        seen.add(cid)
    if seen != set(expected):raise ValueError("incomplete editorial claim coverage")
    row["editorial_record"] = editor
    row["original_model_verdict"] = review["overall"]
    row["status"] = ("editorial_revision_required" if any(c["decision"] == "needs_revision" for c in editor["checks"])
                     else "source_review_complete_for_research_preview" if model_supported else "semantic_resolution_required")
    return {"packet_id": cards.digest(row), **row}
