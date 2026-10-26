"""Conservative structure gate for shadow batches, not full-text certification."""
from __future__ import annotations

import re

import etf_content_cards as cards

VERSION = "etf-material-kind-v1"
TIMECODE = re.compile(r"(?<![\w:])(?:\d{1,2}:)?[0-5]?\d:[0-5]\d(?![\d:])")


def units(text: str) -> int:
    """Rough bilingual length units, not model tokens or a paid-token estimate."""
    return len(re.findall(r"[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*", text)) + len(re.findall(r"[\u3400-\u9fff]", text)) // 2


def classify(material: dict) -> dict:
    cards.check_material(material)
    base = {"version": VERSION, "material_id": material["material_id"]}
    if not cards.basic_ready(material):
        return {**base, "kind": "insufficient_material", "eligible": False,
                "reason_zh": "沿用原有材料不足或聚合页门槛，需要补正文或拆分链接。", "metrics": {}}
    text = "\n\n".join(p["text"] for p in material["paragraphs"])
    matches = list(TIMECODE.finditer(text))
    metrics = {"timecodes": len(matches)}
    if len(matches) >= 3:
        segments = [text[m.end():matches[i + 1].start() if i + 1 < len(matches) else len(text)].strip()
                    for i, m in enumerate(matches)]
        heading = [s for s in segments if units(s) <= 28 and len(s) <= 180]
        total = max(1, units(text))
        heading_share = sum(units(s) for s in heading) / total
        outside = text[:matches[0].start()] + "\n" + "\n".join(s for s in segments if s not in heading)
        # Substantial paragraphs can coexist with chapter navigation. A timestamp
        # alone must not disqualify transcripts or articles containing clock times.
        narrative = units(outside) >= 180 and len(re.findall(r"[.!?](?:\s|$)|[。！？]", outside)) >= 3
        metrics.update(short_segments=len(heading), heading_share=round(heading_share, 3),
                       outside_units=units(outside), substantial_outside_prose=narrative)
        if len(heading) / len(segments) >= 0.8 and not narrative:
            dominant = heading_share >= 0.5
            return {**base, "kind": "timeline_dominant" if dominant else "mixed_timeline",
                    "eligible": False, "reason_zh": "多个短时间轴条目且未检测到足够连续正文；先补节目文字稿或文章正文，不能把目录当作实质观点。",
                    "metrics": metrics}
    return {**base, "kind": "readable_excerpt", "eligible": True,
            "reason_zh": "未命中目录结构门槛；仍只是可读节选，未认证全文、内容价值或外部事实。", "metrics": metrics}
