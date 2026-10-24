"""Explicit public HTML body selectors; original sources never inherit directory dates."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

import etf_content_cards as cards

VERSION = "etf-original-source-extract-v1"


def require(value, message):
    if not value:raise ValueError(message)


def publication(soup):
    values = [node.get("content") for node in soup.select('meta[property="article:published_time"]')]
    basis = "article:published_time"
    if not values:
        basis = "JSON-LD datePublished"
        def visit(value):
            if isinstance(value, dict):
                if "datePublished" in value:values.append(value["datePublished"])
                for child in value.values():visit(child)
            elif isinstance(value, list):
                for child in value:visit(child)
        for script in soup.select('script[type="application/ld+json"]'):
            try:visit(json.loads(script.string or script.get_text()))
            except (ValueError, TypeError):continue
    if not values:
        basis = "published time element"
        values = [node.get("datetime") for node in soup.select("time.entry-date.published")]
    values = list(dict.fromkeys(value for value in values if isinstance(value, str) and value))
    require(len(values) == 1, "missing or ambiguous original publication date")
    stamp = dt.datetime.fromisoformat(values[0].replace("Z", "+00:00"))
    return {"published_raw": values[0], "date_basis": basis,
            "date_status": "timezone_unconfirmed" if stamp.tzinfo is None else "source_timestamp_with_zone"}


def extract(lead, raw, spec):
    require(set(spec) == {"title_selector", "expected_title", "body_selector"}, "invalid original-source extraction spec")
    page = raw.decode("utf-8-sig");soup = BeautifulSoup(page, "html.parser")
    canonical = [node.get("href", "") for node in soup.select('link[rel="canonical"]')]
    canonical += [node.get("content", "") for node in soup.select('meta[property="og:url"]')]
    require(canonical and {cards.dr.canonical_url(u) for u in canonical} == {cards.dr.canonical_url(lead["url"])}, "original-source canonical mismatch")
    titles = soup.select(spec["title_selector"])
    require(len(titles) == 1, "missing or ambiguous original title")
    title = titles[0].get("content") if titles[0].name == "meta" else titles[0].get_text(" ", strip=True)
    normalize = lambda text: " ".join(text.split())
    require(normalize(title) == normalize(spec["expected_title"]), "original-source title changed")
    date = publication(soup)
    base = {"version": VERSION, "url": lead["url"], "actual_title": title, "parent_directory_material_id": lead["parent_material_id"],
            "raw_sha256": hashlib.sha256(raw).hexdigest(), **date, "extraction_spec": spec,
            "delivery_eligible": False, "mail_approvals_added": 0, "review_status": "pending"}
    # Conservatively stop at identity metadata when a subscription marker exists.
    # No hidden serialized post body, credential, subscriber API or overlay removal.
    restricted = bool(re.search(r'"audience"\s*:\s*"(?:only_paid|only_subscribers)"', page))
    if restricted:return None, {**base, "access_scope": "metadata_only_subscription_marker", "material_id": None,
                                 "paragraph_count": 0, "text_chars": 0, "pending_flags": ["normal_authorized_access_required"]}
    roots = soup.select(spec["body_selector"])
    require(len(roots) == 1, "missing or ambiguous original body")
    root = roots[0]
    require(not root.select("math,.katex"), "explicit mathematical-source extraction required")
    for node in root.select('script,style,nav,form,button,aside,table,figure,svg,img,iframe,[hidden],[aria-hidden="true"],[inert]'):
        node.decompose()
    for node in root.select('[style]'):
        if re.search(r'display\s*:\s*none|visibility\s*:\s*hidden', node.get("style", ""), re.I):node.decompose()
    tags = {"p", "li", "h1", "h2", "h3", "h4", "blockquote", "pre"}
    paragraphs = []
    for node in root.find_all(tags):
        if any(parent.name in tags for parent in node.parents if parent is not root and root in parent.parents):continue
        text = " ".join(node.get_text(" ", strip=True).split())
        if text:paragraphs.append(text)
    require(paragraphs, "original body empty")
    material = cards.make_material(title=title, url=lead["url"], source=urlsplit(lead["url"]).hostname,
                                   published=date["published_raw"], paragraphs=paragraphs, evidence_level="captured_excerpt")
    payload_chars = len(json.dumps(cards.request_payload(material, "PLAN_ONLY", 2400, "disabled"), ensure_ascii=False)) if cards.ready(material) else None
    flags = []
    if date["date_status"] == "timezone_unconfirmed":flags.append("confirm_source_time_zone")
    if payload_chars is not None and payload_chars > 60000:flags.append("explicit_excerpt_selection_required")
    if not cards.ready(material):flags.append("substantive_source_review_required")
    return material, {**base, "access_scope": "public_html_excerpt", "material_id": material["material_id"],
                      "paragraph_count": len(paragraphs), "text_chars": sum(map(len, paragraphs)),
                      "paragraph_text_sha256": cards.digest(paragraphs), "generation_payload_chars": payload_chars,
                      "tables_and_figures_extracted": False, "complete_article_claim": False, "pending_flags": flags}
