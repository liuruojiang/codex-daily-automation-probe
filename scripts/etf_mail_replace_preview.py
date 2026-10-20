"""Replace selected saved-email article blocks for an unsent comparison only."""
from __future__ import annotations

import argparse
import copy
import hashlib
import html
import json
from pathlib import Path
import re

import daily_reports as daily
import etf_content_cards as cards
import etf_mail_preview as preview
from etf_candidate_audit import audit_candidates
from etf_preflight import canonical, rendered_urls, utc
from validate_etf_delivery import validate as validate_delivery

VERSION = "etf-mail-replace-preview-v1"


def text_hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def single_line(value):
    preview.require(isinstance(value, str) and value.strip() and not any(ord(c) < 32 for c in value), "preview field must be nonempty single-line text")
    return value


def article_blocks(body):
    """Only level-3 item headings with exactly one explicit article link."""
    boundaries = list(re.finditer(r"(?m)^(?:#{1,3} .+|---+\s*)$", body))
    result = {}
    for index, match in enumerate(boundaries):
        if not match.group().startswith("### "):
            continue
        end = boundaries[index + 1].start() if index + 1 < len(boundaries) else len(body)
        block = body[match.start():end]
        links = re.findall(r"(?m)^- (?:链接|原文链接)：\s*(https?://\S+)\s*$", block)
        for url in links:
            result.setdefault(canonical(url), []).append({"start": match.start(), "end": end, "text": block, "link_count": len(links)})
    return result


def replacement_block(row, original):
    card, note = row["card"], row["annotation"]
    # Cards remain unchanged; rendering must not permit new structural lines.
    prefix = re.match(r"^### (\d+\.\s*)", original)
    heading = "### " + (prefix.group(1) if prefix else "") + single_line(card["title_zh"])
    lines = [heading, "- 来源：" + single_line(row["source"]), "- 发布时间（保存字段）：" + single_line(row["published"]),
             "- 链接：" + preview.safe_link(row["url"]), "", "**原文观点与研究结果（来源范围核对）**："]
    for claim in card["facts"]:lines.append("- " + single_line(claim["text_zh"]))
    if card["limitations"]:
        lines += ["", "**原文限定条件**："]
        for claim in card["limitations"]:lines.append("- " + single_line(claim["text_zh"]))
    if card["questions"]:
        lines += ["", "**后续核对问题**："]
        for question in card["questions"]:lines.append("- " + single_line(question))
    lines += ["", "**阅读范围**：" + single_line(note["scope_note_zh"]),
              "", "**日期说明**：" + single_line(note["date_note_zh"]),
              "", "本条为来源范围核对后的研究解读；外部事实未独立核查。"]
    trailing = re.search(r"\n*\Z", original).group()
    return "\n".join(lines) + trailing


def build(bundle, metadata, manifest, history):
    source_report = preview.build(bundle, metadata, manifest, history)
    rows = source_report["items"]
    preview.require(rows and not bundle["pending"], "replacement preview needs complete selected cards only")
    # Identity and exact captured evidence must both match, not merely the URL.
    captured = {material["material_id"]: material for material in cards.prepare_manifest(manifest)}
    selected = manifest["selected_items"]
    materials = {m["material_id"]: m for m in bundle["materials"]}
    blocks = article_blocks(metadata["body"])
    edits = []
    for row in rows:
        mid = row["material_id"]
        preview.require(row["status"] == preview.COMPLETE and "card" in row, "source/model/editor review must be complete")
        preview.require(row["date_gate"]["candidate_gate"] == "candidate_only" and row["date_gate"]["already_in_original"],
                        "replacement fails frozen date/history/selection gate")
        preview.require(captured.get(mid) == materials[mid], "replacement material is not an exact frozen capture")
        identity = [item for item in selected if canonical(item["url"]) == canonical(row["url"])
                    and daily.norm_title(item["title"]) == daily.norm_title(row["title"])
                    and item["source"] == row["source"] and utc(item["published"]) == utc(row["published"])]
        preview.require(len(identity) == 1, "selected article identity mismatch")
        matches = blocks.get(canonical(row["url"]), [])
        preview.require(len(matches) == 1 and matches[0]["link_count"] == 1, "article block is missing or ambiguous")
        old = matches[0]
        new = replacement_block(row, old["text"])
        preview.require(new != old["text"], "replacement must change the article")
        edits.append({"material_id": mid, "url": row["url"], "packet_id": row["packet_id"], "card_hash": row["card_hash"],
                      "review_hash": row["review_hash"], "date_gate": row["date_gate"], "start": old["start"], "end": old["end"],
                      "before": old["text"], "after": new, "before_sha256": text_hash(old["text"]), "after_sha256": text_hash(new)})
    edits.sort(key=lambda edit: edit["start"])
    pieces, unchanged, cursor = [], [], 0
    for edit in edits:
        preview.require(edit["start"] >= cursor, "overlapping replacement blocks")
        piece = metadata["body"][cursor:edit["start"]]
        pieces += [piece, edit["after"]]
        unchanged.append(piece)
        cursor = edit["end"]
    unchanged.append(metadata["body"][cursor:]);pieces.append(unchanged[-1])
    new_body = "".join(pieces)
    # Reconstruct both versions from the same unchanged regions.
    original_again = "".join(unchanged[i] + edit["before"] for i, edit in enumerate(edits)) + unchanged[-1]
    preview.require(original_again == metadata["body"], "unchanged-region reconstruction failed")
    preview.require(rendered_urls(new_body) == rendered_urls(metadata["body"]), "article URL set changed")
    new_manifest = copy.deepcopy(manifest)
    new_manifest["body_sha256"] = text_hash(new_body)
    new_metadata = {**copy.deepcopy(metadata), "body": new_body,
                    "html_body": daily.markdown_to_email_html(new_body, "未发送正文替换样稿；保留原采集截点")}
    errors = validate_delivery(new_metadata, new_manifest, history, require_candidate_audit=True)
    preview.require(not errors, "replacement delivery integrity failed: " + "; ".join(errors))
    prior_audit, after_audit = audit_candidates(manifest, history), audit_candidates(new_manifest, history)
    preview.require(after_audit == prior_audit, "preview changed original source warning decisions")
    report = {"version": VERSION, "mode": "unsent_inline_replacement_preview", "delivery_eligible": False, "sent": False,
              "snapshot_report_date": source_report["snapshot_report_date"], "source_run_id": source_report["source_run_id"],
              "source_head_sha": source_report["source_head_sha"], "input_sha256": source_report["input_sha256"],
              "original_body_sha256": manifest["body_sha256"], "replaced_body_sha256": new_manifest["body_sha256"],
              "original_manifest_sha256": cards.digest(manifest), "original_history_sha256": cards.digest(history),
              "original_metadata_sha256": cards.digest(metadata), "inline_article_replacements": len(edits), "history_updates": 0,
              "selected_article_count": len(manifest["selected_items"]), "article_urls_unchanged": True,
              "unchanged_regions_sha256": [text_hash(piece) for piece in unchanged], "market_data_changed": False,
              "original_content_audit": source_report["original_content_audit"], "delivery_integrity_errors": [], "replacements": edits}
    report["preview_id"] = cards.digest(report)
    return report, new_metadata["html_body"]


def main_content(rendered):
    marker = '<main class="email-main">'
    preview.require(rendered.count(marker) == 1 and rendered.count('</main>') == 1, "unexpected email renderer structure")
    return rendered.split(marker, 1)[1].split('</main>', 1)[0]


def render_comparison(report, replaced_html):
    esc = html.escape
    banner = '<section class="preview-banner"><p>未发送 · 正文替换试跑</p><h1>正文改造前后对照</h1><p>原日报日期：' + esc(report["snapshot_report_date"])
    banner += '。以下对照后附完整替换样稿；日期和去重均按原采集截点检查，行情没有重新抓取。</p><p>本次仅替换 ' + str(report["inline_article_replacements"])
    banner += ' 条。其余文章、行情和原有采集警报完整保留。来源忠实度核对不等于外部事实已证实，本页未获发送资格。</p></section>'
    compare = '<section><h2 class="report-h2">逐条前后对照</h2>'
    for index, edit in enumerate(report["replacements"], 1):
        before = main_content(daily.markdown_to_email_html(edit["before"], ""))
        after = main_content(daily.markdown_to_email_html(edit["after"], ""))
        compare += '<section class="comparison"><h3 class="report-h3">' + str(index) + '. <a class="email-link" href="' + esc(preview.safe_link(edit["url"]), quote=True)
        compare += '">对应原文</a></h3><div class="comparison-grid"><div class="comparison-before"><p class="comparison-label">改造前 · 原日报</p>' + before
        compare += '</div><div class="comparison-after"><p class="comparison-label">改造后 · 来源核对稿</p>' + after + '</div></div></section>'
    compare += '</section><div class="report-rule"></div><h2 class="report-h2">完整替换样稿（未发送）</h2>'
    css = '<style>.preview-banner{background:#edf3fc;border:1px solid #cad8ef;padding:20px;border-radius:12px;font-size:14px;line-height:1.8}.preview-banner h1{font-size:24px;line-height:1.5}.comparison-grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:16px}.comparison-before,.comparison-after{padding:16px;background:#f7f8fb;border:1px solid #e4e7ec;border-radius:8px;min-width:0;overflow-wrap:anywhere}.comparison-after{background:#f2f8f4}.comparison-label{font-weight:700;color:#27306b}.comparison .report-h3{font-size:16px}@media(max-width:700px){.comparison-grid{grid-template-columns:1fr}}</style>'
    return replaced_html.replace('</head>', css + '</head>').replace('<main class="email-main">', '<main class="email-main">' + banner + compare)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("bundle", "metadata", "manifest", "history-before", "out-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args(argv)
    preview.require(not args.out_dir.exists(), "replacement output must be a new directory")
    inputs = [cards.read_json(path) for path in (args.bundle, args.metadata, args.manifest, args.history_before)]
    report, replaced_html = build(*inputs)
    rendered = render_comparison(report, replaced_html)
    args.out_dir.mkdir(parents=True)
    cards.write_json(args.out_dir / "replacement-audit.json", report)
    (args.out_dir / "replacement-preview.html").write_text(rendered, encoding="utf-8")
    print(json.dumps({"preview_id": report["preview_id"], "inline_article_replacements": report["inline_article_replacements"], "delivery_eligible": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
