"""Compare email alternatives semantically, including figures and prose, not just URLs.

The deterministic renderer is the reference. This checks document content and
approved styles, not pixel layout in every mail client. The preheader is ignored.
"""
from __future__ import annotations

from html.parser import HTMLParser
import re


class ReportText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.parts = []
        self.errors = []
        self.styles = []
        self.inline_styles = []
        self.anchors = []
        self._anchor = None

    def handle_starttag(self, tag, attrs):
        names = [name.lower() for name, _ in attrs]
        if len(names) != len(set(names)):
            self.errors.append("duplicate HTML attributes are not permitted")
        attrs = dict(attrs)
        inherited = self.stack[-1][1] if self.stack else False
        style = re.sub(r"\s+", "", attrs.get("style", "").lower())
        hidden = ("hidden" in attrs or attrs.get("aria-hidden") == "true"
                  or any(x in style for x in ("display:none", "visibility:hidden", "font-size:0", "opacity:0")))
        # The renderer emits one hidden preview outside main. It cannot carry
        # report content: all report text must still match the visible reference.
        ignored = tag in ("head", "script", "style", "title") or hidden
        if hidden and any(parent == "main" for parent, _ in self.stack):
            self.errors.append("report content must not be hidden")
        if tag in ("script", "iframe", "object", "embed") or any(k.lower().startswith("on") for k in attrs):
            self.errors.append("active HTML content is not permitted")
        if tag in ("img", "svg", "canvas", "video", "audio", "input", "link"):
            self.errors.append("unapproved rendered or external content is not permitted")
        if "style" in attrs:
            self.inline_styles.append((tag, re.sub(r"\s+", "", attrs["style"])))
        if tag == "style":
            self.styles.append("")
        if tag == "a" and not attrs.get("href", "").startswith("#"):
            self._anchor = [attrs.get("href", ""), ""]
        for k in ("href", "src"):
            if attrs.get(k, "").strip().lower().startswith(("javascript:", "data:text/html")):
                self.errors.append("active HTML URL is not permitted")
        if tag not in ("meta", "link", "br", "hr", "img", "input", "wbr", "source", "area", "base", "col", "param"):
            self.stack.append((tag, inherited or ignored))

    def handle_endtag(self, tag):
        if tag == "a" and self._anchor is not None:
            self.anchors.append(tuple(self._anchor))
            self._anchor = None
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if self.stack and self.stack[-1][0] == "style":
            self.styles[-1] += data
            compact = re.sub(r"\s+", "", data.lower())
            if any(x in compact for x in ("display:none", "visibility:hidden", "font-size:0", "opacity:0")):
                self.errors.append("stylesheet must not conceal report content")
        if not self.stack or not self.stack[-1][1]:
            self.parts.append(data)
        if self._anchor is not None:
            self._anchor[1] += data

    def normalized(self):
        text = "".join(self.parts)
        # Frozen pre-anchor renderer versions displayed Markdown TOC fragments.
        # Accept their labels, without accepting changed report prose or figures.
        text = re.sub(r"\[([^\]]+)\]\(#[^)\s]+\)", r"\1", text)
        return re.sub(r"\s+", "", text)


def validate_html_alternative(body: str, html_body: str) -> list[str]:
    from daily_reports import markdown_to_email_html, expand_email_html_classes

    expected = ReportText()
    actual = ReportText()
    try:
        expected.feed(markdown_to_email_html(body, ""))
        actual.feed(expand_email_html_classes(html_body))
    except (ValueError, TypeError) as exc:
        return [f"html_body semantic parse failed: {type(exc).__name__}"]
    errors = list(dict.fromkeys(actual.errors))
    if [re.sub(r"\s+", "", s) for s in actual.styles] != [re.sub(r"\s+", "", s) for s in expected.styles] or actual.inline_styles != expected.inline_styles:
        errors.append("html_body styles differ from the approved report renderer")
    if actual.anchors != expected.anchors:
        errors.append("html_body clickable text/source associations differ from metadata.body")
    if actual.normalized() != expected.normalized():
        errors.append("html_body visible report content differs from metadata.body")
    return errors
