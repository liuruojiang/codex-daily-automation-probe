"""Render a complete saved ETF email with a source-reviewed research appendix.

Offline only. Never produces sender metadata, updates history, or grants delivery.
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import hashlib
import html
import json
from pathlib import Path
import urllib.parse

import daily_reports as daily
import etf_content_cards as cards
import etf_editorial_packet as editorial
from etf_candidate_audit import audit_candidates
from etf_preflight import canonical, utc
from validate_etf_delivery import LinkParser, validate as validate_delivery

VERSION = "etf-mail-preview-v1"
INPUT_VERSION = "etf-mail-preview-input-v1"
COMPLETE = "source_review_complete_for_research_preview"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def safe_link(value):
    require(isinstance(value, str), "source URL must be text")
    parsed = urllib.parse.urlsplit(value)
    require(parsed.scheme in ("https", "http") and parsed.hostname and not parsed.username and not parsed.password
            and not any(ord(char) < 33 for char in value), "invalid source URL")
    return value


def frozen_date_gate(material, annotation, manifest, history):
    """Report candidate date/history rules only; never return delivery eligibility."""
    cutoff = utc(manifest["cutoff_utc"])
    today = cutoff.astimezone(daily.BJ).date()
    seen_urls, seen_titles = set(), set()
    for row in history["items"]:
        require(isinstance(row, dict), "invalid frozen history row")
        sent_day = date.fromisoformat(row["sent_date"])
        if today - timedelta(days=daily.ETF_DEDUPE_DAYS) <= sent_day <= today:
            if row.get("url"):seen_urls.add(canonical(row["url"]))
            if row.get("title"):seen_titles.add(daily.norm_title(row["title"]))
    matches = [row for row in manifest["candidates"] if canonical(row["url"]) == canonical(material["url"])
               and daily.norm_title(row["title"]) == daily.norm_title(material["title"])
               and row.get("published") == material["published"]]
    result = {"published": material["published"], "cutoff_utc": manifest["cutoff_utc"],
              "history_duplicate": canonical(material["url"]) in seen_urls or daily.norm_title(material["title"]) in seen_titles,
              "snapshot_identity_found": bool(matches), "date_status": annotation["date_status"],
              "already_in_original": any(canonical(row["url"]) == canonical(material["url"]) for row in manifest["selected_items"])}
    try:
        stamp = utc(material["published"])
    except (ValueError, TypeError):
        return {**result, "window": "publication_unconfirmed", "candidate_gate": "held"}
    if stamp > cutoff:window = "after_cutoff"
    elif stamp >= cutoff - timedelta(hours=daily.ETF_ARTICLE_MAX_AGE_HOURS):window = "primary_window"
    elif any(str(row.get("source_id", "")).startswith("research|") for row in matches) and stamp >= cutoff - timedelta(hours=daily.ETF_ARTICLE_BACKFILL_MAX_AGE_HOURS):window = "research_backfill"
    else:window = "outside_window"
    candidate = (window in ("primary_window", "research_backfill") and not result["history_duplicate"]
                 and matches and annotation["date_status"] == "captured")
    return {**result, "window": window, "candidate_gate": "candidate_only" if candidate else "held"}


def build(bundle, metadata, manifest, history):
    require(isinstance(bundle, dict) and set(bundle) == {"version", "materials", "cards", "reviews", "editors", "annotations", "pending", "appeals"}
            and bundle["version"] == INPUT_VERSION, "invalid preview input bundle")
    require(history.get("phase") == "before_build" and history.get("head_sha") == manifest.get("head_sha"), "history provenance mismatch")
    cutoff = utc(manifest["cutoff_utc"])
    report_date = cutoff.astimezone(daily.BJ).date().isoformat()
    require(metadata.get("subject") == "美股 ETF 与资产配置日报 - " + report_date, "saved subject/report date mismatch")
    errors = validate_delivery(metadata, manifest, history, require_candidate_audit=True)
    require(not errors, "saved delivery integrity failed: " + "; ".join(errors))
    materials = bundle["materials"]
    require(isinstance(materials, list), "materials must be a list")
    ids, urls = set(), set()
    for material in materials:
        cards.check_material(material)
        mid, url = material["material_id"], canonical(safe_link(material["url"]))
        require(mid not in ids and url not in urls, "duplicate material or source URL in preview")
        ids.add(mid);urls.add(url)
    for key in ("cards", "reviews", "editors", "annotations", "pending", "appeals"):
        require(isinstance(bundle[key], dict) and set(bundle[key]) <= ids, "unknown material in " + key)
    require(set(bundle["annotations"]) == ids, "missing source/date annotations")
    require(set(bundle["cards"]) == set(bundle["reviews"]) == set(bundle["editors"]), "card/review/editor coverage mismatch")
    require(set(bundle["cards"]).isdisjoint(bundle["pending"]) and set(bundle["cards"]) | set(bundle["pending"]) == ids,
            "each material needs a reviewed card or an explicit pending reason")
    rows = []
    for material in materials:
        mid = material["material_id"]
        note = bundle["annotations"][mid]
        require(isinstance(note, dict) and set(note) == {"material_id", "date_status", "date_note_zh", "scope_note_zh"}
                and note["material_id"] == mid and note["date_status"] in ("captured", "conflicting", "unconfirmed")
                and cards.chinese(note["date_note_zh"]) and cards.chinese(note["scope_note_zh"]), "invalid source/date annotation")
        row = {"material_id": mid, "source": material["source"], "title": material["title"], "url": material["url"],
               "published": material["published"], "evidence_level": material["evidence_level"], "annotation": note,
               "date_gate": frozen_date_gate(material, note, manifest, history), "delivery_eligible": False}
        if mid in bundle["pending"]:
            reason = bundle["pending"][mid]
            require(cards.chinese(reason), "pending material needs a Chinese reason")
            row.update(status="source_text_required", reason_zh=reason)
        else:
            appeal = bundle["appeals"].get(mid, {})
            require(isinstance(appeal, dict) and set(appeal) in (set(), {"case", "result"}), "invalid appeal input")
            packet = editorial.evaluate(material, bundle["cards"][mid], bundle["reviews"][mid], bundle["editors"][mid],
                                        appeal.get("case"), appeal.get("result"))
            row.update(status=packet["status"], packet_id=packet.get("packet_id"), card_hash=packet["card_hash"],
                       review_hash=packet["review_hash"], external_fact_check=packet["external_fact_check"])
            # Unresolved editorial/model decisions remain visible without claim text.
            if packet["status"] == COMPLETE:row["card"] = bundle["cards"][mid]
        rows.append(row)
    candidate_audit = audit_candidates(manifest, history)
    result = {"version": VERSION, "mode": "unsent_complete_email_preview", "delivery_eligible": False, "sent": False,
              "subject_preview": "【未发送研究样刊】" + metadata["subject"], "snapshot_report_date": report_date,
              "source_run_id": str(manifest["run_id"]), "source_head_sha": manifest["head_sha"],
              "original_body_sha256": manifest["body_sha256"], "original_integrity_errors": [],
              "original_content_audit": {"status": candidate_audit["status"], "failures": len(candidate_audit["failures"]), "gaps": len(candidate_audit["gaps"])},
              "input_sha256": cards.digest(bundle), "original_selected_count": len(manifest["selected_items"]),
              "original_body_modified": False, "history_updates": 0, "inline_article_replacements": 0,
              "appendix_count": sum(row["status"] == COMPLETE for row in rows),
              "pending_count": sum(row["status"] != COMPLETE for row in rows), "items": rows}
    result["preview_id"] = cards.digest(result)
    return result


def render_preview(report, original_body):
    require(hashlib.sha256(original_body.encode()).hexdigest() == report["original_body_sha256"], "original body changed before rendering")
    esc = html.escape
    count = report["appendix_count"]
    intro = ('<section class="preview-banner"><p class="preview-label">未发送 · 研究样刊</p><h1>'
             + esc(report["subject_preview"]) + '</h1><p>基于已保存的 ' + esc(report["snapshot_report_date"])
             + ' 日报，检查完整邮件中的中文解读。下方保留原日报全文、行情日期及原有采集警报。</p>'
             + '<p>本次新增 ' + str(count) + ' 份来源核对稿，' + str(report["pending_count"])
             + ' 项待处理。新增稿只列为研究附录；来源核对不等于外部事实核查，也不赋予当天资讯或发送资格。</p></section>')
    window_labels = {"primary_window": "采集日期字段位于36小时内", "research_backfill": "采集日期字段位于14天研究回溯内",
                     "outside_window": "超出原有日期窗口", "after_cutoff": "晚于原采集截止时间", "publication_unconfirmed": "发布日期不明"}
    appendix = '<section id="research-appendix"><h2 class="report-h2">中文解读研究附录</h2>'
    for row in report["items"]:
        if row["status"] != COMPLETE:continue
        card, gate = row["card"], row["date_gate"]
        appendix += '<article class="research-card"><h3 class="report-h3">' + esc(card["title_zh"]) + '</h3>'
        appendix += '<p class="source-meta">' + esc(row["source"]) + ' · 保存发布日期：' + esc(row["published"] or "未知") + '</p>'
        appendix += '<p class="scope-note">' + esc(row["annotation"]["scope_note_zh"]) + '</p>'
        appendix += '<p class="scope-note">' + esc(row["annotation"]["date_note_zh"]) + '；' + esc(window_labels[gate["window"]])
        if gate["history_duplicate"]:appendix += '；已在发送前历史中出现'
        appendix += '。仅作研究阅读。</p>'
        for field, label in (("facts", "原文观点与研究结果"), ("limitations", "原文限定条件"), ("questions", "后续核对问题")):
            if not card[field]:continue
            appendix += '<p class="claim-label">' + label + '</p><ul class="report-list">'
            for claim in card[field]:
                appendix += '<li class="report-li">' + esc(claim["text_zh"] if isinstance(claim, dict) else claim) + '</li>'
            appendix += '</ul>'
        appendix += '<p class="report-p"><a class="email-link" href="' + esc(safe_link(row["url"]), quote=True) + '">阅读原文</a> · 来源范围已逐项核对；独立事实核查未做。</p></article>'
    appendix += '</section><section><h2 class="report-h2">待处理材料</h2>'
    pending = [row for row in report["items"] if row["status"] != COMPLETE]
    if not pending:appendix += '<p class="report-p">本批暂无待处理材料。</p>'
    for row in pending:
        appendix += '<p class="report-p"><strong>' + esc(row["title"]) + '</strong><br>' + esc(row.get("reason_zh", "尚未完成来源、模型或编辑核对，暂不展示解读。"))
        appendix += '<br>保存发布日期：' + esc(row["published"] or "未知") + ' · <a class="email-link" href="' + esc(safe_link(row["url"]), quote=True) + '">原文入口</a></p>'
    appendix += '</section><div class="report-rule"></div><h2 class="report-h2">原日报全文（保存版本）</h2><p class="report-p">以下为原日报全文。原有摘要和占位表述尚未逐条替换；本页不表示它们已经通过新增编辑核对。</p>'
    rendered = daily.markdown_to_email_html(original_body, "未发送研究样刊；历史快照及研究附录")
    require(rendered.count('<main class="email-main">') == 1, "unexpected email renderer structure")
    css = '<style>.preview-banner{padding:22px;background:#edf3fc;border:1px solid #c8d7ed;border-radius:12px;margin-bottom:24px}.preview-banner h1{font-size:24px;line-height:1.5;margin:8px 0 14px}.preview-banner p{font-size:14px;line-height:1.8;margin:8px 0}.preview-label{font-weight:700;color:#294a7d}.research-card{padding:0 0 18px;margin-bottom:18px;border-bottom:1px solid #e4e7ec}.source-meta,.scope-note{font-size:12px;line-height:1.8;color:#596579;overflow-wrap:anywhere}.claim-label{font-size:14px;font-weight:700;color:#27306b;margin:14px 0 4px}</style>'
    rendered = rendered.replace('</head>', css + '</head>').replace('<main class="email-main">', '<main class="email-main">' + intro + appendix)
    parser = LinkParser();parser.feed(rendered)
    require(all(canonical(row["url"]) in parser.links for row in report["items"]), "preview is missing a clickable source")
    return rendered


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("bundle", "metadata", "manifest", "history-before", "out-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args(argv)
    require(not args.out_dir.exists(), "preview output must be a new directory")
    metadata, manifest, history, bundle = [cards.read_json(path) for path in (args.metadata, args.manifest, args.history_before, args.bundle)]
    report = build(bundle, metadata, manifest, history)
    rendered = render_preview(report, metadata["body"])
    args.out_dir.mkdir(parents=True)
    cards.write_json(args.out_dir / "mail-preview-audit.json", report)
    (args.out_dir / "mail-preview.html").write_text(rendered, encoding="utf-8")
    print(json.dumps({"preview_id": report["preview_id"], "appendix_count": report["appendix_count"],
                      "pending_count": report["pending_count"], "delivery_eligible": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
