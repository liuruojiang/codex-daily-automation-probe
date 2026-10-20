"""Extract explicitly selected public transcript turns into a new excerpt version."""
from __future__ import annotations

import datetime as dt
import hashlib
from html.parser import HTMLParser
import re

import etf_content_cards as cards

VERSION = "etf-transcript-material-v1"
CONTAINER = "single-podcast-content-transcript-content"
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


def normalized(text):
    return " ".join(text.split())


class TranscriptParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.blocks = [], []
        self.container_depth = self.capture = self.date_depth = None
        self.buffer, self.date_buffer = [], []
        self.title = self.date_text = self.canonical = ""
        self.containers = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "link" and attrs.get("rel") == "canonical":
            self.canonical = attrs.get("href", "")
        if tag in VOID:
            if tag == "br" and self.capture:self.buffer.append(" ")
            return
        self.stack.append(tag)
        depth = len(self.stack)
        if attrs.get("id") == CONTAINER:
            self.containers += 1;self.container_depth = depth
        if "single-podcast-heading__content__desc__title-date" in attrs.get("class", "").split():
            self.date_depth = depth
        if (tag == "h1" and not self.title) or (self.container_depth is not None and tag in ("h4", "p")):
            if self.capture:raise ValueError("nested transcript block")
            self.capture = (tag, depth);self.buffer = []

    def handle_startendtag(self, tag, attrs):
        if tag in VOID:self.handle_starttag(tag, attrs)

    def handle_data(self, text):
        if self.stack and self.stack[-1] in ("script", "style"):return
        if self.capture:self.buffer.append(text)
        if self.date_depth is not None:self.date_buffer.append(text)

    def handle_endtag(self, tag):
        if tag in VOID:return
        if not self.stack or self.stack[-1] != tag:
            raise ValueError("unbalanced transcript HTML")
        depth = len(self.stack)
        if self.capture == (tag, depth):
            text = normalized("".join(self.buffer))
            if tag == "h1":self.title = text
            elif text:self.blocks.append((tag, text))
            self.capture = None;self.buffer = []
        if self.date_depth == depth:
            self.date_text = normalized("".join(self.date_buffer));self.date_depth = None
        if self.container_depth == depth:self.container_depth = None
        self.stack.pop()


def seconds(value: str) -> int:
    if not re.fullmatch(r"\d{2}:[0-5]\d:[0-5]\d", value):raise ValueError("expected HH:MM:SS")
    h, m, s = map(int, value.split(":"));return h * 3600 + m * 60 + s


def extract(parent: dict, html_bytes: bytes, windows: list[list[str]]) -> tuple[dict, dict]:
    cards.check_material(parent)
    parser = TranscriptParser();parser.feed(html_bytes.decode("utf-8-sig"));parser.close()
    if parser.stack or parser.containers != 1 or parser.title != parent["title"] or cards.dr.canonical_url(parser.canonical) != cards.dr.canonical_url(parent["url"]):
        raise ValueError("transcript container or source identity mismatch")
    if not windows or any(not isinstance(w, list) or len(w) != 2 or seconds(w[0]) > seconds(w[1]) for w in windows):
        raise ValueError("explicit nonempty transcript windows required")
    turns, current = [], None
    for tag, text in parser.blocks:
        if tag == "h4":
            match = re.fullmatch(r"\[(\d{2}:[0-5]\d:[0-5]\d)\]\s+(.+)", text)
            if not match:raise ValueError("unexpected transcript heading")
            current = {"timestamp": match[1], "speaker": match[2], "paragraphs": []};turns.append(current)
        elif current is not None:
            current["paragraphs"].append(text)
    selected = [t for t in turns if t["speaker"].lower() not in ("intro", "ending") and t["paragraphs"]
                and any(seconds(a) <= seconds(t["timestamp"]) <= seconds(b) for a, b in windows)]
    if not selected:raise ValueError("no substantive turns in selected windows")
    paragraphs = [f'[{t["timestamp"]}] {t["speaker"]}: {text}' for t in selected for text in t["paragraphs"]]
    material = cards.make_material(**{k:parent[k] for k in ("title", "url", "source", "published")},
                                   paragraphs=paragraphs, evidence_level="captured_excerpt")
    if not cards.ready(material):raise ValueError("selected transcript lacks readable evidence")
    date_match = re.search(r"(\d{1,2})\s*\|\s*(\d{1,2})\s*\|\s*(\d{4})", parser.date_text)
    page_date = dt.date(int(date_match[3]), int(date_match[2]), int(date_match[1])).isoformat() if date_match else None
    provenance = {"version": VERSION, "parent_material_id": parent["material_id"], "material_id": material["material_id"],
                  "raw_html_sha256": hashlib.sha256(html_bytes).hexdigest(), "canonical_url": parser.canonical,
                  "visible_episode_date": page_date, "source_date_text": parser.date_text, "selection_windows": windows,
                  "available_turns": len(turns), "selected_turns": [{"timestamp": t["timestamp"], "speaker": t["speaker"], "paragraphs": len(t["paragraphs"])} for t in selected],
                  "complete_transcript_claim": False, "evidence_level": "captured_excerpt"}
    return material, provenance
