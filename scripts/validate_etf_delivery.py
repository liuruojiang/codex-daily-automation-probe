"""Validate delivery integrity; disclosed editorial omissions are advisory."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

from etf_preflight import canonical, rendered_urls
from etf_candidate_audit import audit_candidates


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            for name, value in attrs:
                if name.lower() == "href" and value and value.startswith(("https://", "http://")):
                    self.links.add(canonical(value))


def validate(metadata: dict, manifest: dict, history_before: dict | None = None, *, require_candidate_audit: bool = False) -> list[str]:
    errors: list[str] = []
    body = metadata.get("body")
    if not isinstance(body, str) or not body.strip():
        return ["metadata.body must contain the complete nonempty report"]
    if manifest.get("body_sha256") != hashlib.sha256(body.encode("utf-8")).hexdigest():
        errors.append("body_sha256 does not match the exact email text")
    if "attachment" not in metadata or metadata["attachment"] is not None:
        errors.append("attachment must explicitly be null")
    actual = rendered_urls(body)
    raw_links = re.findall(r"(?m)^- (?:链接|原文链接)：\s*(https?://\S+)\s*$", body)
    if len(raw_links) != len(actual):
        errors.append("the same article is rendered more than once")
    for field in ("selected_items", "history_added_items"):
        records = manifest.get(field)
        if not isinstance(records, list) or any(not isinstance(row, dict) or not isinstance(row.get("url"), str) or not row["url"].startswith(("http://", "https://")) for row in records):
            errors.append(f"{field} must be an explicit list of URL-bearing items")
            continue
        recorded = {canonical(row["url"]) for row in records}
        if len(records) != len(recorded):
            errors.append(f"{field} contains duplicate articles")
        if recorded != actual:
            missing = sorted(actual - recorded)
            extra = sorted(recorded - actual)
            errors.append(f"{field} differs from the actual email article links; missing={missing}, extra={extra}")
    html = metadata.get("html_body")
    if not isinstance(html, str) or not html.strip():
        errors.append("html_body must contain the complete report")
    else:
        parser = LinkParser()
        try:
            parser.feed(html)
        except Exception as exc:
            errors.append(f"html_body cannot be parsed: {type(exc).__name__}")
        missing = actual - parser.links
        if missing:
            errors.append(f"html_body is missing clickable article links: {sorted(missing)}")
    if require_candidate_audit or history_before is not None:
        candidate_result = audit_candidates(manifest, history_before)
        advisory = {"unexplained_priority_omission", "selected_evidence_requires_review", "history_record_date_unknown"}
        errors.extend(f"candidate audit: {row['reason']} {row.get('url', '')}"
                      for row in candidate_result["failures"] if row["reason"] not in advisory)
        if candidate_result["status"] != "PASS":
            marker = f"发送前缺漏检查：{candidate_result['status']}"
            if marker not in body or marker not in (html or ""):
                errors.append("unresolved candidate/source coverage must be disclosed in both email bodies")
            for row in candidate_result["failures"]:
                if row["reason"] in advisory and row.get("url"):
                    url = canonical(row["url"])
                    if url not in {canonical(u) for u in re.findall(r"https?://[^\s<>]+", body)} or (isinstance(html, str) and url not in parser.links):
                        errors.append(f"candidate warning must include a clickable source: {url}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("metadata", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--history-before", type=Path)
    args = parser.parse_args()
    manifest_path = args.manifest or args.metadata.with_name("collection_manifest.json")
    candidate_result = None
    try:
        metadata = json.loads(args.metadata.read_text(encoding="utf-8-sig"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        history_path = args.history_before or args.metadata.with_name("history_before.json")
        history_before = json.loads(history_path.read_text(encoding="utf-8-sig"))
        if not isinstance(metadata, dict) or not isinstance(manifest, dict):
            raise ValueError("metadata and manifest must be JSON objects")
        errors = validate(metadata, manifest, history_before, require_candidate_audit=True)
        candidate_result = audit_candidates(manifest, history_before)
        args.metadata.with_name("candidate_audit.json").write_text(json.dumps(candidate_result, ensure_ascii=False, indent=2), encoding="utf-8")
    except (OSError, ValueError, TypeError) as exc:
        errors = [f"cannot validate delivery artifacts: {exc}"]
    warnings = candidate_result["failures"] + candidate_result["gaps"] if candidate_result else []
    status = "FAILED" if errors else "PASS_WITH_WARNINGS" if warnings else "PASS"
    print(json.dumps({"status": status, "errors": errors,
                      "candidate_audit_status": candidate_result["status"] if candidate_result else None,
                      "warning_count": len(warnings)}, ensure_ascii=False, indent=2))
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as summary:
            summary.write(f"## ETF delivery validation: {status}\n\n")
            if candidate_result:
                summary.write(f"Content audit: **{candidate_result['status']}**; {len(warnings)} findings. Disclosed content warnings do not block delivery.\n\n")
                for row in candidate_result["failures"]:
                    summary.write(f"- {row['reason']}: {row.get('title', '')} {row.get('url', '')}\n")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
