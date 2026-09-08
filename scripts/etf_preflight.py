"""Read-only, independently testable integrity audit for the 08:00 ETF summary.

Input is a run-bound collection manifest, pre-build history, and sent metadata.
This module deliberately does not re-use production selection or scoring gates.
Historical live re-fetches cannot establish complete historical coverage.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def utc(value: str) -> datetime:
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("Timestamp has no explicit timezone")
    return stamp.astimezone(timezone.utc)


def canonical(value: str) -> str:
    parts = urlsplit(value.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith("utm_")]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), urlencode(query), ""))


def rendered_urls(body: str) -> set[str]:
    # Only actual item blocks, never generic links in audit, source list or TOC.
    return {canonical(value) for value in re.findall(r"(?m)^- (?:链接|原文链接)：\s*(https?://\S+)\s*$", body)}


def audit(manifest: dict, metadata: dict, history_before: dict, run: dict) -> dict:
    gaps: list[dict] = []
    failures: list[dict] = []
    decisions: list[dict] = []

    def gap(reason: str, **detail: object) -> None:
        gaps.append({"reason": reason, **detail})

    def fail(reason: str, **detail: object) -> None:
        failures.append({"reason": reason, **detail})

    if not manifest:
        return {"status": "PARTIAL", "gaps": [{"reason": "legacy_email_missing_collection_snapshot"}], "failures": [], "decisions": []}
    try:
        cutoff = utc(run["build_started_at"])
        manifest_cutoff = utc(manifest["cutoff_utc"])
    except (KeyError, ValueError, TypeError):
        return {"status": "PARTIAL", "gaps": [{"reason": "invalid_or_naive_cutoff"}], "failures": [], "decisions": []}
    if manifest_cutoff != cutoff:
        gap("cutoff_mismatch")
    if not run.get("run_id") or not run.get("head_sha") or str(manifest.get("run_id")) != str(run.get("run_id")) or manifest.get("head_sha") != run.get("head_sha"):
        gap("run_provenance_mismatch")
    if run.get("conclusion") != "success" or run.get("send_gmail") != "success":
        gap("send_not_confirmed")
    body = metadata.get("body", "")
    if not isinstance(body, str):
        body = ""
    if hashlib.sha256(body.encode("utf-8")).hexdigest() != manifest.get("body_sha256"):
        gap("sent_body_hash_mismatch")
    if "attachment" not in metadata or metadata.get("attachment") is not None:
        gap("unexpected_attachment")
    expected_date = cutoff.astimezone(timezone(timedelta(hours=8))).date().isoformat()
    if metadata.get("subject") != f"美股 ETF 与资产配置日报 - {expected_date}":
        gap("subject_report_date_mismatch")
    if manifest.get("capture_mode") != "build_snapshot":
        gap("historical_live_refetch_cannot_prove_complete_coverage")
    if history_before.get("head_sha") != run.get("head_sha") or history_before.get("phase") != "before_build":
        gap("pre_build_history_provenance_missing")
    # Never remove all same-day history: run HEAD is already the pre-build state.
    seen: set[str] = set()
    sent_titles: set[str] = set()
    earliest = cutoff.astimezone(timezone(timedelta(hours=8))).date() - timedelta(days=365)
    for record in history_before.get("items", []):
        try:
            sent_date = datetime.fromisoformat(record["sent_date"]).date()
        except (KeyError, ValueError, TypeError):
            gap("history_record_date_unknown", url=record.get("url", ""))
            continue
        if earliest <= sent_date <= cutoff.astimezone(timezone(timedelta(hours=8))).date():
            seen.add(canonical(record.get("url", "")))
            sent_titles.add(re.sub(r"\W+", " ", record.get("title", "").lower()).strip())
    configured = manifest.get("configured_sources", [])
    if not configured:
        gap("configured_source_inventory_missing")
    expected_sources = run.get("configured_sources")
    if expected_sources is None:
        gap("run_head_source_inventory_unverified")
    elif set(configured) != set(expected_sources):
        gap("run_head_source_inventory_mismatch", missing=sorted(set(expected_sources) - set(configured)), unexpected=sorted(set(configured) - set(expected_sources)))
    source_map = {entry.get("source_id", entry.get("source")): entry for entry in manifest.get("source_audit", [])}
    for source_id in configured:
        entry = source_map.get(source_id)
        if not entry:
            gap("source_not_checked", source=source_id)
        elif entry.get("coverage") != "complete" or not entry.get("evidence"):
            gap("source_coverage_unconfirmed", source=source_id, detail=entry.get("status"))
    displayed = rendered_urls(body)
    selected = {canonical(item["url"]) for item in manifest.get("selected_items", [])}
    for url in selected - displayed:
        fail("selected_but_not_rendered", url=url)
    for url in displayed - selected:
        gap("rendered_but_missing_from_selection_manifest", url=url)
    for item in manifest.get("history_added_items", []):
        url = canonical(item["url"])
        if url not in displayed:
            fail("unrendered_item_written_to_sent_history", url=url)
    for item in manifest.get("candidates", []):
        url = canonical(item["url"])
        detail = {"url": url, "title": item.get("title", ""), "source": item.get("source", ""), "published": item.get("published", "")}
        try:
            published = utc(item["published"])
        except (KeyError, ValueError, TypeError):
            gap("candidate_publication_unconfirmed", **detail)
            continue
        if published > cutoff:
            decisions.append({"decision": "after_cutoff", **detail})
            if url in displayed:
                fail("after_cutoff_item_rendered", **detail)
            continue
        if published < cutoff - timedelta(hours=36):
            decisions.append({"decision": "outside_primary_window", **detail})
            continue
        title_key = re.sub(r"\W+", " ", item.get("title", "").lower()).strip()
        if url in seen or (title_key and title_key in sent_titles):
            decisions.append({"decision": "previously_sent", **detail})
            if url in displayed:
                fail("duplicate_item_rendered", **detail)
            continue
        if item.get("is_aggregator") and item.get("children_checked") is not True:
            gap("aggregator_children_not_checked", **detail)
        eligible = item.get("independent_eligible")
        if eligible is True and url not in displayed:
            if item.get("exclusion_reason") == "editorial_capacity" and item.get("exclusion_evidence"):
                decisions.append({"decision": "documented_editorial_capacity", **detail})
            else:
                fail("confirmed_omission", **detail)
        elif eligible is None and url not in displayed:
            gap("candidate_needs_independent_review", pipeline_reason=item.get("exclusion_reason", ""), **detail)
        else:
            decisions.append({"decision": "rendered" if url in displayed else "excluded", **detail})
    return {"status": "FAILED" if failures else "PARTIAL" if gaps else "PASS", "cutoff_utc": cutoff.isoformat(), "failures": failures, "gaps": gaps, "decisions": decisions}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, help="Omit for legacy artifacts; result is PARTIAL.")
    for name in ("metadata", "history-before", "run"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    inputs = []
    for name in ("manifest", "metadata", "history_before", "run"):
        path = getattr(args, name)
        inputs.append(json.loads(path.read_text(encoding="utf-8-sig")) if path and path.exists() else {})
    print(json.dumps(audit(*inputs), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

