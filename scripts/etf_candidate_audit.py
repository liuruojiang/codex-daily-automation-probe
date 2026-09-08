"""Independent pre-send omission checks; never imports production scoring.

This is a conservative rule-based second opinion, not a claim of exhaustive
semantic/source coverage. Unknowns stay in a visible review queue (PARTIAL).
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import date, timedelta, timezone

from etf_preflight import canonical, utc


POLICY = "etf-candidates-v1"
TOPIC = re.compile(r"\b(?:portfolio|allocation|diversification|concentration|trend|momentum|factor|duration|cvar|kelly|backtest|look.ahead|trading strateg|capacity|crowding|covariance|correlation|etfs?|rebalancing|managed futures|retirement|withdrawal)\w*\b|资产配置|量化|趋势|分散|回测|养老金", re.I)
NOISE = re.compile(r"subscribe (?:now|today|to)|sign (?:up|in)|log in|accept (?:all )?cookies|privacy policy|all rights reserved|buy now|limited.time offer|sponsored content|click here|javascript (?:is )?required", re.I)
EXCLUDED_PRODUCT = re.compile(r"\b(?:single.stock|leveraged|inverse etf|yieldmax|weeklypay|incomemax|kurv|meme stock)\b", re.I)


def prose_evidence(value: str) -> bool:
    """Content structure only: no investment-keyword sentence score."""
    text = re.sub(r"<(script|style|nav|footer|header|noscript)\b[^>]*>.*?</\1\s*>", " ", value, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    sentences = {s.strip().lower() for s in re.split(r"[.!?。！？](?:\s+|$)", text)
                 if len(s.strip()) >= 60 and not NOISE.search(s)}
    words = re.findall(r"[a-z]{2,}|[\u4e00-\u9fff]", " ".join(sentences).lower())
    return len(sentences) >= 2 and len(set(words)) >= 30


def audit_candidates(manifest: dict, history_before: dict | None = None) -> dict:
    failures: list[dict] = []
    gaps: list[dict] = []
    decisions: list[dict] = []

    def result() -> dict:
        return {"status": "FAILED" if failures else "PARTIAL" if gaps else "PASS",
                "failures": failures, "gaps": gaps, "decisions": decisions}

    if manifest.get("schema_version") != 2 or manifest.get("decision_policy") != POLICY:
        failures.append({"reason": "candidate_ledger_missing_or_unsupported"})
        return result()
    try:
        cutoff = utc(manifest["cutoff_utc"])
    except (KeyError, ValueError, TypeError):
        failures.append({"reason": "invalid_candidate_cutoff"})
        return result()
    if history_before is None or history_before.get("phase") != "before_build" or history_before.get("head_sha") != manifest.get("head_sha"):
        failures.append({"reason": "candidate_history_provenance_missing"})
        return result()
    today = cutoff.astimezone(timezone(timedelta(hours=8))).date()
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    for row in history_before.get("items", []):
        stamp = str(row.get("sent_date", ""))
        try:
            sent_day = date.fromisoformat(stamp)
        except ValueError:
            failures.append({"reason": "history_record_date_unknown", "url": row.get("url", "")})
            continue
        if today - timedelta(days=365) <= sent_day <= today:
            seen_urls.add(canonical(row.get("url", "")))
            seen_titles.add(re.sub(r"\W+", " ", row.get("title", "").lower()).strip())
    candidates = manifest.get("candidates")
    if not isinstance(candidates, list):
        failures.append({"reason": "candidate_ledger_missing"})
        return result()
    selected = {canonical(row["url"]) for row in manifest.get("selected_items", [])}
    def article_identity(row: dict) -> tuple:
        # A URL alone does not establish that two source captures describe the
        # same selected publication. Keep conflicting dates/evidence visible.
        try:
            published = utc(row["published"])
        except (KeyError, ValueError, TypeError):
            published = None
        return (canonical(row.get("url", "")), row.get("source", ""),
                re.sub(r"\W+", " ", row.get("title", "").lower()).strip(), published)

    selected_identities = {article_identity(row) for row in manifest.get("selected_items", [])}
    research_representatives = {
        article_identity(row): row.get("source_id") for row in candidates
        if str(row.get("source_id", "")).startswith("research|")
        and article_identity(row) in selected_identities
    }
    selected_titles = {re.sub(r"\W+", " ", row.get("title", "").lower()).strip(): canonical(row["url"])
                       for row in manifest.get("selected_items", []) if row.get("title")}
    captured = {canonical(row.get("url", "")) for row in candidates}
    for url in sorted(selected - captured):
        failures.append({"reason": "selected_without_capture", "url": url})
    counts = Counter(row.get("source_id") for row in candidates)
    sources = manifest.get("configured_sources", [])
    audits = manifest.get("source_audit", [])
    audit_ids = [row.get("source_id") for row in audits]
    if not sources or len(audit_ids) != len(set(audit_ids)) or set(audit_ids) != set(sources):
        failures.append({"reason": "source_inventory_incomplete"})
    for row in audits:
        sid = row.get("source_id")
        if row.get("evidence", {}).get("captured_count") != counts[sid]:
            failures.append({"reason": "captured_candidate_count_mismatch", "source": sid})
        if row.get("coverage") != "complete":
            gaps.append({"reason": "source_coverage_unconfirmed", "source": sid, "detail": row.get("status")})
    for row in candidates:
        url = canonical(row.get("url", ""))
        detail = {"url": url, "source": row.get("source", ""), "title": row.get("title", ""), "published": row.get("published", "")}
        shown = url in selected
        pipeline = row.get("pipeline_decision", {})
        reason = pipeline.get("reason")
        if not reason:
            failures.append({"reason": "candidate_decision_missing", **detail})
        if row.get("source_id") not in sources:
            failures.append({"reason": "candidate_source_unconfigured", **detail})
        try:
            published = utc(row["published"])
        except (KeyError, ValueError, TypeError):
            (failures if shown else gaps).append({"reason": "candidate_publication_unconfirmed", **detail})
            decisions.append({"decision": "publication_review", **detail})
            continue
        title = re.sub(r"\W+", " ", row.get("title", "").lower()).strip()
        if url in seen_urls or (title and title in seen_titles):
            if shown:
                failures.append({"reason": "duplicate_item_rendered", **detail})
            decisions.append({"decision": "previously_sent", **detail})
            continue
        if published > cutoff:
            if shown:
                failures.append({"reason": "after_cutoff_item_rendered", **detail})
            decisions.append({"decision": "after_cutoff", **detail})
            continue
        if not shown and title and title in selected_titles:
            decisions.append({"decision": "same_run_title_duplicate", "represented_by": selected_titles[title], **detail})
            continue
        if (shown and str(row.get("source_id", "")).startswith(("fixed_feed|", "fixed_page|"))
                and article_identity(row) in research_representatives):
            # The research capture is independently checked below, including
            # the 14-day limit and enriched prose. A second fixed-source capture
            # is not a second rendered article in the strict 36-hour section.
            decisions.append({"decision": "same_run_research_capture_duplicate",
                              "represented_by": research_representatives[article_identity(row)], **detail})
            continue
        primary = published >= cutoff - timedelta(hours=36)
        forum = str(row.get("source_id", "")).startswith(("forum|", "reddit|"))
        if not primary:
            # Preserve separate research and community backfill, not the fixed
            # blogs/podcasts' strict 36-hour publication window.
            allowed_backfill = (str(row.get("source_id", "")).startswith("research|") or forum) and published >= cutoff - timedelta(days=14)
            if shown and not allowed_backfill:
                failures.append({"reason": "stale_item_rendered", **detail})
            decisions.append({"decision": ("forum_backfill" if forum else "research_backfill") if shown else "outside_primary_window", **detail})
            if not shown or not allowed_backfill:
                continue
        text = row.get("enrichment", {}).get("summary") or row.get("summary", "")
        readable = prose_evidence(text)
        strong_topic = TOPIC.search(row.get("title", "")) or len(set(TOPIC.findall(text.lower()))) >= 3
        # An independent, explicit exclusion on the article's subject. A term
        # in a collection's child text must not exclude the entire collection.
        if not shown and not row.get("is_aggregator") and EXCLUDED_PRODUCT.search(row.get("title", "")):
            decisions.append({"decision": "excluded_product_subject", **detail})
            continue
        if shown:
            if not forum and not readable:
                failures.append({"reason": "selected_evidence_requires_review", **detail})
            decisions.append({"decision": "rendered", **detail})
        elif primary and not forum and (row.get("independent_eligible") is True or (strong_topic and readable)):
            # Do NOT trust eligible=False, a low pipeline score, or an arbitrary
            # editorial_capacity string to excuse a contrary independent finding.
            failures.append({"reason": "unexplained_priority_omission", "pipeline_reason": reason, **detail})
            decisions.append({"decision": "priority_omission", **detail})
        else:
            gaps.append({"reason": "candidate_needs_independent_review", "pipeline_reason": reason, **detail})
            decisions.append({"decision": "review_required", **detail})
        if primary and row.get("is_aggregator") and not row.get("children_checked"):
            gaps.append({"reason": "aggregator_children_not_checked", **detail})
    return result()
