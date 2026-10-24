"""Later public-source supplements, kept separate from frozen mail evidence."""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import urllib.request
from urllib.parse import urlsplit
from unittest.mock import patch

import etf_automatic_mail_preview as automatic
import etf_content_cards as cards

VERSION = "etf-source-intake-audit-v1"
STATUSES = {"http_access_blocked", "outline_only", "aggregate_listing", "short_source_post", "captions_empty", "body_supplement_pending"}
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


def digest_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def targets(manifest, registry):
    captured = cards.prepare_manifest(manifest)
    covered = {automatic.canonical(e["url"]) for e in registry["entries"]}
    out = []
    for item in manifest["selected_items"]:
        if automatic.canonical(item["url"]) in covered:continue
        material = max((m for m in captured if automatic.canonical(m["url"]) == automatic.canonical(item["url"])),
                       key=lambda m: sum(len(p["text"]) for p in m["paragraphs"]))
        out.append({"parent_material_id": material["material_id"], **{k: material[k] for k in ("title", "url", "source", "published")}})
    return out


class LeadParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0;self.container = None;self.containers = 0
        self.canonical = set();self.links = [];self.active = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "link" and attrs.get("rel") == "canonical":self.canonical.add(attrs.get("href", ""))
        if tag == "meta" and attrs.get("property") == "og:url":self.canonical.add(attrs.get("content", ""))
        if tag == "div":
            self.depth += 1
            if attrs.get("id") == "qo-mashup":self.containers += 1;self.container = self.depth
        if self.container is not None and tag == "a" and "qo-title" in attrs.get("class", "").split():
            self.active = [attrs.get("href", ""), []]

    def handle_data(self, text):
        if self.active is not None:self.active[1].append(text)

    def handle_endtag(self, tag):
        if tag == "a" and self.active is not None:
            self.links.append((self.active[0], " ".join("".join(self.active[1]).split())));self.active = None
        if tag == "div":
            if self.container == self.depth:self.container = None
            self.depth -= 1


def extract_leads(parent, raw):
    cards.check_material(parent)
    parser = LeadParser();parser.feed(raw.decode("utf-8-sig"));parser.close()
    require = automatic.preview.require
    require(parser.containers == 1 and {automatic.canonical(u) for u in parser.canonical} == {automatic.canonical(parent["url"])}, "lead source identity mismatch")
    leads, seen = [], set()
    for url, title in parser.links:
        parts = urlsplit(url)
        require(parts.scheme == "https" and parts.hostname and "." in parts.hostname and parts.username is None and
                parts.password is None and parts.port in (None, 443) and title, "unsafe or empty source lead")
        canonical = automatic.canonical(url)
        require(canonical not in seen, "duplicate source lead");seen.add(canonical)
        leads.append({"url": url, "title": title, "parent_material_id": parent["material_id"], "publication_date": None,
                      "date_status": "unverified", "status": "new_source_needs_capture_and_review", "delivery_eligible": False})
    require(leads, "no source leads captured")
    return leads


class ArxivParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.blocks, self.title_parts = [], [], []
        self.articles = 0
        self.article_depth = self.block_depth = self.title_depth = self.math_depth = None
        self.block_id, self.parts = None, []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in VOID:
            if tag == "br" and self.block_depth:self.parts.append(" ")
            return
        self.stack.append(tag);depth = len(self.stack)
        classes = attrs.get("class", "").split()
        if tag == "article" and "ltx_document" in classes:
            self.articles += 1;self.article_depth = depth
        if self.article_depth is None:return
        if tag == "h1" and "ltx_title_document" in classes:
            if self.title_depth is not None:raise ValueError("nested document title")
            self.title_depth = depth
        if self.block_depth and tag in ("p", "div", "li"):self.parts.append(" ")
        if "ltx_para" in classes and self.block_depth is None:
            self.block_depth = depth;self.block_id = attrs.get("id");self.parts = []
        if tag == "math" and self.block_depth:
            if "alttext" not in attrs:raise ValueError("math source alttext missing")
            self.parts.append(" $" + attrs["alttext"] + "$ ");self.math_depth = depth

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:self.handle_endtag(tag)

    def handle_data(self, text):
        if any(t in ("script", "style") for t in self.stack):return
        if self.title_depth:self.title_parts.append(text)
        if self.block_depth and not self.math_depth:self.parts.append(text)

    def handle_endtag(self, tag):
        if tag in VOID:return
        if not self.stack or self.stack[-1] != tag:raise ValueError("unbalanced source HTML")
        depth = len(self.stack)
        if self.block_depth and tag in ("p", "div", "li"):self.parts.append(" ")
        if self.math_depth == depth:self.math_depth = None
        if self.block_depth == depth:
            self.blocks.append((self.block_id, " ".join("".join(self.parts).split())));self.block_depth = None
        if self.title_depth == depth:self.title_depth = None
        if self.article_depth == depth:self.article_depth = None
        self.stack.pop()


def extract_arxiv(parent, raw, source_url, paragraph_ids):
    cards.check_material(parent)
    require = automatic.preview.require
    original = re.fullmatch(r"https://arxiv\.org/abs/(\d{4}\.\d{4,5})(?:v\d+)?", parent["url"])
    linked = re.fullmatch(r"https://arxiv\.org/html/(\d{4}\.\d{4,5})v(\d+)", source_url)
    require(original is not None and linked is not None and original[1] == linked[1], "arxiv source identity mismatch")
    page = raw.decode("utf-8-sig")
    require("arXiv:" + linked[1] + "v" + linked[2] in page, "arxiv version marker absent")
    parser = ArxivParser();parser.feed(page);parser.close()
    normalize = lambda s: " ".join(s.split())
    require(not parser.stack and parser.articles == 1 and normalize("".join(parser.title_parts)) == normalize(parent["title"]), "arxiv title/container mismatch")
    require(isinstance(paragraph_ids, list) and paragraph_ids and all(isinstance(p, str) for p in paragraph_ids)
            and len(set(paragraph_ids)) == len(paragraph_ids), "explicit unique paragraph IDs required")
    available = [key for key, _ in parser.blocks]
    require(len(available) == len(set(available)) and set(paragraph_ids) <= set(available), "source paragraph missing or duplicated")
    selected = [(key, text) for key, text in parser.blocks if key in paragraph_ids]
    require([key for key, _ in selected] == paragraph_ids and all(text for _, text in selected), "source paragraph order/contents mismatch")
    material = cards.make_material(**{k: parent[k] for k in ("title", "url", "source", "published")},
                                   paragraphs=[text for _, text in selected], evidence_level="captured_excerpt")
    require(cards.ready(material), "supplement is not readable evidence")
    provenance = {"parent_material_id": parent["material_id"], "material_id": material["material_id"], "source_url": source_url,
                  "raw_sha256": digest_bytes(raw), "paragraph_ids": paragraph_ids,
                  "paragraph_text_sha256": cards.digest([text for _, text in selected]), "paper_version": linked[2],
                  "available_paragraph_count": len(available), "selected_paragraph_count": len(selected),
                  "math_source": "MathML alttext", "tables_and_figures_extracted": False,
                  "paragraph_scope": "outermost ltx_para including complete descendant text",
                  "complete_paper_claim": False, "evidence_level": "captured_excerpt", "original_published": parent["published"]}
    return material, provenance


def validate_audit(audit, manifest, history, registry):
    require = automatic.preview.require
    require(audit["version"] == VERSION and audit["snapshot"] == automatic.snapshot_identity(manifest, history), "intake snapshot mismatch")
    require(audit["registry_sha256"] == cards.digest(registry), "reviewed registry changed during intake")
    expected = targets(manifest, registry)
    require(audit["targets"] == expected and len(audit["items"]) == len(expected), "intake target coverage mismatch")
    require(audit["delivery_eligible"] is False and audit["mail_approvals_added"] == 0 and audit["model_requests"] == 0, "intake cannot approve mail")
    cutoff = dt.datetime.fromisoformat(manifest["cutoff_utc"])
    for target, row in zip(expected, audit["items"]):
        require(row["parent_material_id"] == target["parent_material_id"] and row["requested_url"] == target["url"], "intake receipt identity mismatch")
        require(row["status"] in STATUSES and cards.chinese(row["reason_zh"]) and cards.chinese(row["next_action_zh"]), "invalid intake disposition")
        require(dt.datetime.fromisoformat(row["finished_utc"]) >= dt.datetime.fromisoformat(row["started_utc"]) > cutoff, "later capture timestamp invalid")
        require(type(row["bytes"]) is int and 0 <= row["bytes"] <= 4000000 and re.fullmatch(r"[0-9a-f]{64}", row["body_sha256"]), "invalid capture digest/size")
        require(row["response_status"] in (401, 403) if row["status"] == "http_access_blocked" else row["response_status"] == 200, "HTTP outcome/disposition mismatch")
    supplements = audit["supplements"]
    mids = {m["material_id"] for m in cards.prepare_manifest(manifest)}
    require(len({s["parent_material_id"] for s in supplements}) == len(supplements), "duplicate supplement")
    pending = {row["parent_material_id"] for row in audit["items"] if row["status"] == "body_supplement_pending"}
    require({s["parent_material_id"] for s in supplements} == pending, "supplement coverage mismatch")
    require(all(s["material_id"] not in mids and s["delivery_eligible"] is False and s["review_status"] == "pending"
                and s["frozen_mail_admission"] == "held_later_capture" for s in supplements), "new source cannot inherit frozen approval")


def probe(source, registry_path, audit_path, report_path, raw_html=None):
    require = automatic.preview.require
    require(not report_path.exists(), "intake proof output already exists")
    paths = [source/"metadata.json", source/"collection_manifest.json", source/"history_before.json", registry_path, audit_path]
    before = [digest_bytes(p.read_bytes()) for p in paths]
    metadata, manifest, history, registry, audit = [cards.read_json(p) for p in paths]
    validate_audit(audit, manifest, history, registry)
    require(len(audit["supplements"]) == 1, "this probe expects one fixed arxiv supplement")
    saved = audit["supplements"][0]
    if raw_html is not None:raw = raw_html.read_bytes()
    else:
        require(re.fullmatch(r"https://arxiv\.org/html/\d{4}\.\d{4,5}v\d+", saved["source_url"]), "unexpected supplement endpoint")
        with urllib.request.urlopen(saved["source_url"], timeout=30) as response:
            require(response.geturl() == saved["source_url"], "unexpected supplement redirect")
            raw = response.read(4000001)
        require(len(raw) <= 4000000, "supplement too large")
    parent = next(m for m in cards.prepare_manifest(manifest) if m["material_id"] == saved["parent_material_id"])
    def forbidden(*args, **kwargs):raise AssertionError("intake must not call a model")
    with patch.object(cards, "call_json", side_effect=forbidden):
        material, provenance = extract_arxiv(parent, raw, saved["source_url"], saved["paragraph_ids"])
        require(material["material_id"] == saved["material_id"] and provenance["paragraph_text_sha256"] == saved["paragraph_text_sha256"], "supplement text changed; new version/review required")
        require(extract_arxiv(parent, raw, saved["source_url"], saved["paragraph_ids"])[0] == material, "intake replay differs")
        # Explicit synthetic registration: it must fail at the frozen-source gate,
        # before a new capture could inherit a model/editor approval from elsewhere.
        injected = copy.deepcopy(registry)
        injected["entries"].append({**copy.deepcopy(registry["entries"][0]), "material_id": material["material_id"], "url": material["url"]})
        report, _ = automatic.build(metadata, manifest, history, injected)
        require(report["replaced_count"] == len(registry["entries"]) and report["reason_counts"].get("stale_material") == 1, "later capture entered frozen mail")
    require(before == [digest_bytes(p.read_bytes()) for p in paths], "intake inputs changed")
    proof = {"version": "etf-source-intake-proof-v1", "source_inputs_unchanged": True, "reviewed_registry_unchanged": True,
             "target_count": len(audit["items"]), "supplements_rebuilt": 1, "material_id": material["material_id"],
             "selected_paragraph_count": len(material["paragraphs"]), "live_public_fetch": raw_html is None,
             "new_fetch_raw_sha256": digest_bytes(raw), "selected_text_matches_saved_capture": True,
             "offline_replay_identical": True, "model_requests": 0, "synthetic_frozen_gate_test": "stale_material",
             "mail_replacements_remain": report["replaced_count"], "delivery_eligible": False}
    cards.write_json(report_path, proof);print(json.dumps(proof))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source-dir", "registry", "audit", "report"):parser.add_argument("--"+name, type=Path, required=True)
    parser.add_argument("--raw-html", type=Path)
    args = parser.parse_args()
    probe(args.source_dir, args.registry, args.audit, args.report, args.raw_html)


if __name__ == "__main__":main()
