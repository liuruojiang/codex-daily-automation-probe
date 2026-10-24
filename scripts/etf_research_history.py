"""Classify historical research under today's ownership gate without granting approval.

Old model/editor records remain immutable. Only the newly introduced ownership
error can become an explicit held replay; every other identity/schema/evidence
error remains fatal. This module never changes a review or supplies a model pass.
"""
from __future__ import annotations

import etf_content_cards as cards
import etf_material_kind as material_kind
import etf_semantic_review as semantic
import etf_review_appeal as appeals

OWNERSHIP_ERROR = "review_evidence_not_in_claim_citations"
COMPLETE = "source_review_complete_for_research_preview"


def classify_legacy_review(material, card, review):
    cards.check_material(material)
    if not material_kind.classify(material)["eligible"] or cards.validate_card(material, card) or semantic.terminology_issues(card):
        raise ValueError("invalid historical research source/card")
    errors = semantic.validate_review(material, card, review)
    if set(errors) - {OWNERSHIP_ERROR}:
        raise ValueError("invalid historical research review: " + ", ".join(errors))
    reasons = []
    units = {u["id"]: u for u in cards.evidence_units(material)}
    claims = semantic.claim_map(card)
    for check in review["checks"]:
        cid = check["claim_id"]
        if check["verdict"] != "supported" or not cid.startswith(("facts.", "limitations.")):
            continue
        refs = claims[cid]["citations"]
        def owns(uid):
            return any(units[uid]["paragraph_id"] == ref["paragraph_id"]
                       and (units[uid]["text"] in ref["quote"] or ref["quote"] in units[uid]["text"])
                       for ref in refs)
        unowned = [uid for uid in check["evidence_ids"] if not owns(uid)]
        if unowned:
            reasons.append({"code": OWNERSHIP_ERROR, "claim_id": cid,
                            "review_evidence_ids": check["evidence_ids"][:],
                            "unowned_evidence_ids": unowned,
                            "own_citation_evidence_ids": [uid for uid in units if owns(uid)]})
    return {"current_gate_status": "held" if reasons else "passed",
            "current_gate_scope": "review_evidence_ownership", "current_gate_reasons": reasons,
            "historical_review_preserved": True}


def historical_packet(material, card, review, editor):
    """Validate original editing even when today's review gate holds the entry.

    The returned status describes the original recorded editing decision only;
    consumers must also honor current_gate_status. No record is rebound or edited.
    """
    gate = classify_legacy_review(material, card, review)
    identity = {"material_id": material["material_id"], "card_hash": cards.digest(card), "review_hash": cards.digest(review)}
    fields = {"material_id", "card_hash", "review_hash", "appeal_case_id", "checks"}
    if not isinstance(editor, dict) or set(editor) != fields or any(editor[k] != identity[k] for k in identity) or editor["appeal_case_id"] is not None:
        raise ValueError("stale or invalid historical editorial record")
    expected, seen = semantic.claim_map(card), set()
    units = {u["id"]: u["text"] for u in cards.evidence_units(material)}
    if not isinstance(editor["checks"], list):
        raise ValueError("invalid historical editorial checks")
    for check in editor["checks"]:
        if not isinstance(check, dict) or set(check) != {"claim_id", "decision", "reason_zh", "evidence_ids"}:
            raise ValueError("invalid historical editorial check")
        cid = check["claim_id"]
        if not isinstance(cid, str) or cid not in expected or cid in seen or check["decision"] not in ("confirmed", "needs_revision") or not cards.chinese(check["reason_zh"]):
            raise ValueError("unknown, duplicate or invalid historical editorial claim")
        ids = check["evidence_ids"]
        if not appeals.evidence_ids(ids, set(units) | {"source_title"}, required=check["decision"] == "confirmed" and cid.startswith(("facts.", "limitations."))):
            raise ValueError("invalid historical editorial evidence")
        if check["decision"] == "confirmed" and cid.startswith(("facts.", "limitations.")):
            own = [ref["quote"] for ref in expected[cid]["citations"]]
            if not any(uid in units and any(units[uid] in quote or quote in units[uid] for quote in own) for uid in ids):
                raise ValueError("historical editor must check the claim's own quotation")
        seen.add(cid)
    if seen != set(expected):
        raise ValueError("incomplete historical editorial claim coverage")
    status = ("editorial_revision_required" if any(c["decision"] == "needs_revision" for c in editor["checks"])
              else COMPLETE if review["overall"] == "pass" else "semantic_resolution_required")
    return {"status": status, **gate}


def historical_repair_passed(result):
    """Check preserved repair/disposition, while keeping a new M01 hold visible."""
    return result["status"] == "shadow_review_passed" or (
        result["status"] == "review_invalid" and set(result.get("errors", [])) == {OWNERSHIP_ERROR}
        and result.get("editorial_recheck") == "resolved")


def current_fields(packet):
    return {**{k: v for k, v in packet.items() if k != "status"},
            "historical_recorded_status": packet["status"],
            "status": "held" if packet["current_gate_status"] == "held" else packet["status"]}
