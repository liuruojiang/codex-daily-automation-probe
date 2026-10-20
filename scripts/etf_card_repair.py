"""Offline, source-bound revisions of rejected shadow cards; no publishing path."""
from __future__ import annotations

import argparse
import copy
from pathlib import Path

import etf_content_cards as cards
import etf_semantic_review as semantic

VERSION = "etf-card-repair-v1"
EDITABLE = {"title_zh", "facts", "limitations", "questions"}


def revise(material: dict, parent: dict, plan: dict, parent_veto: dict | None = None) -> dict:
    """Apply explicit field replacements, keeping both versions and every issue."""
    cards.check_material(material)
    fields = {"material_id", "parent_card_hash", "parent_veto_hash", "issues", "edits"}
    if not isinstance(plan, dict) or set(plan) != fields:
        raise ValueError("invalid revision plan")
    mid = material["material_id"]
    if plan["material_id"] != mid or parent.get("material_id") != mid or plan["parent_card_hash"] != cards.digest(parent):
        raise ValueError("stale revision parent")
    if plan["parent_veto_hash"] != (cards.digest(parent_veto) if parent_veto is not None else None):
        raise ValueError("missing or stale parent veto")
    if parent_veto is not None:
        semantic.editorial_veto({"material_id": mid}, parent, parent_veto)
    issues = plan["issues"]
    if not isinstance(issues, list) or not issues:
        raise ValueError("revision needs explicit issues")
    issue_ids = set()
    for issue in issues:
        if not isinstance(issue, dict) or set(issue) != {"id", "reason_zh"} or not isinstance(issue["id"], str) or not issue["id"] or issue["id"] in issue_ids or not cards.chinese(issue["reason_zh"]):
            raise ValueError("invalid or duplicate issue")
        issue_ids.add(issue["id"])
    edits = plan["edits"]
    if not isinstance(edits, list) or not edits:
        raise ValueError("revision needs explicit edits")
    revised = copy.deepcopy(parent)
    seen, addressed = set(), set()
    for edit in edits:
        if not isinstance(edit, dict) or set(edit) != {"field", "value", "issue_ids"}:
            raise ValueError("invalid edit")
        field, ids = edit["field"], edit["issue_ids"]
        if not isinstance(field, str) or field not in EDITABLE or field in seen:
            raise ValueError("unknown or repeated edit field")
        if not isinstance(ids, list) or not ids or any(not isinstance(i, str) or i not in issue_ids for i in ids):
            raise ValueError("edit needs known issue ids")
        seen.add(field)
        addressed.update(ids)
        revised[field] = copy.deepcopy(edit["value"])
    if addressed != issue_ids:
        raise ValueError("unaddressed revision issue")
    revised = cards.expand_evidence_ids(material, revised)
    changed = {field: {"before": parent[field], "after": revised[field]} for field in EDITABLE if parent[field] != revised[field]}
    if not changed or set(changed) != seen:
        raise ValueError("no-op edit cannot resolve an issue")
    record = {
        "version": VERSION, "material_id": mid, "parent_card_hash": cards.digest(parent),
        "card_hash": cards.digest(revised), "parent_card": copy.deepcopy(parent), "card": revised,
        "parent_veto": copy.deepcopy(parent_veto), "plan": copy.deepcopy(plan), "changes": changed,
        "parent_evidence_errors": cards.validate_card(material, parent),
        "evidence_errors": cards.validate_card(material, revised),
    }
    return {"revision_id": cards.digest(record), **record}


def evaluate(record: dict, material: dict, review: dict | None = None, disposition: dict | None = None) -> dict:
    # Never trust a hand-edited ledger or precomputed validation result.
    rebuilt = revise(material, record["parent_card"], record["plan"], record["parent_veto"])
    if rebuilt != record:
        raise ValueError("revision ledger mismatch")
    card = record["card"]
    row = semantic.evaluate(material, card, review)
    result = {**row, "revision_id": record["revision_id"], "mode": "shadow_only",
              "external_fact_check": "not_performed", "editorial_recheck": "pending"}
    if disposition is not None:
        fields = {"revision_id", "card_hash", "checks"}
        if not isinstance(disposition, dict) or set(disposition) != fields or disposition["revision_id"] != record["revision_id"] or disposition["card_hash"] != record["card_hash"]:
            raise ValueError("stale or invalid editorial recheck")
        checks = disposition["checks"]
        expected = {i["id"] for i in record["plan"]["issues"]}
        seen = set()
        if not isinstance(checks, list):
            raise ValueError("invalid editorial checks")
        for check in checks:
            if not isinstance(check, dict) or set(check) != {"issue_id", "decision", "reason_zh"}:
                raise ValueError("invalid editorial check")
            iid = check["issue_id"]
            if not isinstance(iid, str) or iid not in expected or iid in seen or check["decision"] not in ("resolved", "needs_revision") or not cards.chinese(check["reason_zh"]):
                raise ValueError("invalid or duplicate editorial issue")
            seen.add(iid)
        if seen != expected:
            raise ValueError("incomplete editorial recheck")
        result["editorial_recheck"] = "resolved" if all(c["decision"] == "resolved" for c in checks) else "needs_revision"
        result["editorial_disposition"] = disposition
    # A recheck can only add a gate; it cannot override a failed model or quote gate.
    if row["status"] == "model_review_passed":
        result["status"] = {"pending": "awaiting_editorial_recheck", "needs_revision": "editorial_revision_required",
                            "resolved": "shadow_review_passed"}[result["editorial_recheck"]]
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("materials", "responses", "plans", "out-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("editorial-vetoes", "reviews", "dispositions"):
        parser.add_argument("--" + name, type=Path)
    args = parser.parse_args(argv)
    materials = cards.read_json(args.materials)
    parents = cards.read_json(args.responses)
    plans = cards.read_json(args.plans)
    vetoes = cards.read_json(args.editorial_vetoes) if args.editorial_vetoes else {}
    reviews = cards.read_json(args.reviews) if args.reviews else {}
    dispositions = cards.read_json(args.dispositions) if args.dispositions else {}
    cards.replay(materials, parents)
    by_id = {m["material_id"]: m for m in materials}
    if not isinstance(plans, dict) or set(plans) != set(parents):
        parser.error("every parent card needs exactly one revision plan")
    if any(not isinstance(v, dict) or set(v) - set(parents) for v in (vetoes, reviews, dispositions)):
        parser.error("unknown card in review inputs")
    records = {mid: revise(by_id[mid], card, plans[mid], vetoes.get(mid)) for mid, card in parents.items()}
    rows = [evaluate(record, by_id[mid], reviews.get(mid), dispositions.get(mid)) for mid, record in records.items()]
    checkpoint = args.out_dir / "revision-records.json"
    if not checkpoint.exists() and (args.out_dir / "responses.json").exists():
        parser.error("existing responses lack a revision ledger; use a separate out-dir")
    if checkpoint.exists() and cards.read_json(checkpoint) != records:
        parser.error("revision context changed; use a separate out-dir")
    cards.write_json(checkpoint, records)
    cards.write_json(args.out_dir / "responses.json", {mid: r["card"] for mid, r in records.items()})
    cards.write_json(args.out_dir / "repair-report.json", {"version": VERSION, "mode": "shadow_only", "items": rows})
    return int(any(r["status"] != "shadow_review_passed" for r in rows))


if __name__ == "__main__":
    raise SystemExit(main())
