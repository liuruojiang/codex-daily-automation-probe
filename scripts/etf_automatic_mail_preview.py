"""Offline automatic shadow preview with explicit per-article coverage."""
from __future__ import annotations

import argparse
from collections import Counter
import html
import json
from pathlib import Path

import daily_reports as daily
import etf_content_cards as cards
import etf_mail_preview as preview
import etf_mail_replace_preview as replacement
from etf_preflight import canonical

VERSION = "etf-automatic-mail-preview-v1"
REGISTRY_VERSION = "etf-reviewed-preview-registry-v1"
REASONS = {"replaced": "已使用来源核对稿", "no_reviewed_card": "暂无可用核对稿，保留原文",
           "registry_snapshot_mismatch": "登记稿与本轮采集版本不同，保留原文",
           "ambiguous_registry_entries": "同一原文有多条登记稿，保留原文",
           "stale_material": "稿件材料与本轮原始捕获不符，保留原文",
           "invalid_review_record": "稿件或审核记录无效，保留原文",
           "review_not_complete": "来源、模型或编辑核对尚未完成，保留原文",
           "date_history_held": "日期或历史去重门槛未通过，保留原文",
           "identity_or_block_held": "原文身份或正文块未能唯一匹配，保留原文"}


def empty_bundle():
    return {"version": preview.INPUT_VERSION, "materials": [], "cards": {}, "reviews": {}, "editors": {},
            "annotations": {}, "pending": {}, "appeals": {}}


def snapshot_identity(manifest, history):
    return {"run_id": str(manifest["run_id"]), "head_sha": manifest["head_sha"], "cutoff_utc": manifest["cutoff_utc"],
            "body_sha256": manifest["body_sha256"], "history_sha256": cards.digest(history)}


def decode_entry(entry, material):
    preview.require(isinstance(entry, dict) and set(entry) == {"material_id", "url", "card", "review", "editor", "annotation"}, "invalid registry entry fields")
    compact = entry["card"]
    preview.require(isinstance(compact, dict) and set(compact) == {"material_id", "title_zh", "facts", "limitations", "questions"}
                    and compact["material_id"] == material["material_id"] and isinstance(compact["title_zh"], str)
                    and isinstance(compact["questions"], list) and all(isinstance(q, str) for q in compact["questions"]), "invalid compact card")
    for field in ("facts", "limitations"):
        preview.require(isinstance(compact[field], list) and all(isinstance(claim, dict) and set(claim) == {"text_zh", "evidence_ids"}
                        and isinstance(claim["text_zh"], str) and isinstance(claim["evidence_ids"], list)
                        and all(isinstance(uid, str) for uid in claim["evidence_ids"])
                        for claim in compact[field]), "compact claims require source sentence IDs")
    card = cards.expand_evidence_ids(material, compact)
    bundle = empty_bundle();mid = material["material_id"]
    bundle["materials"] = [material];bundle["cards"][mid] = card;bundle["reviews"][mid] = entry["review"]
    bundle["editors"][mid] = entry["editor"];bundle["annotations"][mid] = entry["annotation"]
    return bundle


def build(metadata, manifest, history, registry=None):
    baseline = preview.build(empty_bundle(), metadata, manifest, history)
    snapshot = snapshot_identity(manifest, history)
    if registry is not None:
        preview.require(isinstance(registry, dict) and set(registry) == {"version", "snapshot", "entries"}
                        and registry["version"] == REGISTRY_VERSION and isinstance(registry["entries"], list), "invalid registry envelope")
    state = "not_configured" if registry is None else "matched" if registry["snapshot"] == snapshot else "stale"
    entries = registry["entries"] if registry is not None else []
    captured = {m["material_id"]: m for m in cards.prepare_manifest(manifest)}
    by_url, entry_results = {}, []
    for index, entry in enumerate(entries):
        outcome = {"index": index, "status": "unused", "reason": "not_selected"}
        try:
            url = canonical(preview.safe_link(entry["url"]))
            by_url.setdefault(url, []).append((index, entry))
            outcome["url"] = url
        except (KeyError, ValueError, TypeError):
            outcome.update(status="rejected", reason="invalid_review_record")
        entry_results.append(outcome)
    combined = empty_bundle();rows = []
    for item in manifest["selected_items"]:
        url = canonical(item["url"])
        row = {"title": item["title"], "source": item["source"], "url": item["url"], "published": item["published"],
               "status": "original_retained", "reason": "no_reviewed_card"}
        matches = by_url.get(url, [])
        if matches:
            if state == "stale":row["reason"] = "registry_snapshot_mismatch"
            elif len(matches) != 1:row["reason"] = "ambiguous_registry_entries"
            else:
                _, entry = matches[0]
                mid = entry.get("material_id")
                material = captured.get(mid) if isinstance(mid, str) else None
                if material is None or canonical(material["url"]) != url:
                    row["reason"] = "stale_material"
                else:
                    try:
                        one = decode_entry(entry, material)
                        opinion = preview.build(one, metadata, manifest, history)["items"][0]
                        row["packet_status"] = opinion["status"]
                        if opinion["status"] != preview.COMPLETE:row["reason"] = "review_not_complete"
                        elif opinion["date_gate"]["candidate_gate"] != "candidate_only":row["reason"] = "date_history_held"
                        else:
                            try:
                                replacement.build(one, metadata, manifest, history)
                            except (KeyError, ValueError, TypeError):
                                row["reason"] = "identity_or_block_held"
                            else:
                                combined["materials"].extend(one["materials"])
                                for field in ("cards", "reviews", "editors", "annotations"):combined[field].update(one[field])
                                row.update(status="replaced", reason="replaced", material_id=material["material_id"], packet_id=opinion["packet_id"])
                    except (KeyError, ValueError, TypeError, StopIteration):
                        row["reason"] = "invalid_review_record"
            for index, _ in matches:
                entry_results[index].update(status="used" if row["status"] == "replaced" else "rejected", reason=row["reason"])
        rows.append(row)
    # Re-run the strict adapter for the complete accepted set. A global failure
    # is fatal; it must not be disguised as successful fallback or partial output.
    if combined["materials"]:
        changes, rendered = replacement.build(combined, metadata, manifest, history)
        count = changes["inline_article_replacements"]
        body_hash = changes["replaced_body_sha256"]
    else:
        changes, count, body_hash = None, 0, manifest["body_sha256"]
        rendered = daily.markdown_to_email_html(metadata["body"], "未发送自动影子预览；本轮无合格替换稿")
    preview.require(count == sum(row["status"] == "replaced" for row in rows), "coverage/replacement count mismatch")
    report = {"version": VERSION, "mode": "unsent_automatic_shadow_preview", "delivery_eligible": False, "sent": False,
              "snapshot": snapshot, "registry_status": state, "registry_sha256": cards.digest(registry) if registry is not None else None,
              "original_metadata_sha256": cards.digest(metadata), "selected_count": len(rows), "replaced_count": count,
              "retained_count": len(rows)-count, "reason_counts": dict(Counter(row["reason"] for row in rows)),
              "original_body_sha256": manifest["body_sha256"], "preview_body_sha256": body_hash, "history_updates": 0,
              "article_urls_unchanged": True, "market_data_changed": False, "original_content_audit": baseline["original_content_audit"],
              "items": rows, "registry_entries": entry_results, "replacement_audit": changes}
    report["preview_id"] = cards.digest(report)
    return report, rendered


def render(report, rendered_email):
    esc = html.escape
    header = '<section class="automatic-summary"><p>未发送 · 自动影子预览</p><h1>本轮中文解读覆盖情况</h1><p>原日报 ' + str(report["selected_count"])
    header += ' 篇；已替换 ' + str(report["replaced_count"]) + ' 篇，保留原文 ' + str(report["retained_count"]) + ' 篇。</p>'
    header += '<p>保留原文的文章尚未完成本轮新的来源核对。旧稿、无效审核或日期门槛不符均不能替换；原行情及采集警报继续保留。本页没有发送资格。</p>'
    if report["registry_status"] == "stale":header += '<p>登记稿件与本轮采集版本不一致，本轮不复用这些旧稿。</p>'
    header += '</section><section><h2 class="report-h2">逐条处理清单</h2><div class="table-wrap"><table class="report-table"><thead><tr><th class="report-th">文章</th><th class="report-th">本轮处理</th></tr></thead><tbody>'
    for row in report["items"]:
        header += '<tr><td class="report-td"><a class="email-link" href="' + esc(preview.safe_link(row["url"]), quote=True) + '">' + esc(row["title"]) + '</a></td>'
        header += '<td class="report-td">' + esc(REASONS[row["reason"]]) + '</td></tr>'
    header += '</tbody></table></div></section><div class="report-rule"></div><h2 class="report-h2">完整影子样稿（未发送）</h2>'
    css = '<style>.automatic-summary{padding:20px;background:#edf3fc;border:1px solid #cad8ef;border-radius:12px;font-size:14px;line-height:1.8}.automatic-summary h1{font-size:24px;line-height:1.5}</style>'
    replacement.main_content(rendered_email)
    return rendered_email.replace('</head>', css + '</head>').replace('<main class="email-main">', '<main class="email-main">' + header)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("metadata", "manifest", "history-before", "out-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--registry", type=Path)
    args = parser.parse_args(argv)
    preview.require(not args.out_dir.exists(), "automatic preview output must be a new directory")
    metadata, manifest, history = [cards.read_json(p) for p in (args.metadata, args.manifest, args.history_before)]
    registry = cards.read_json(args.registry) if args.registry else None
    report, email = build(metadata, manifest, history, registry)
    page = render(report, email)
    args.out_dir.mkdir(parents=True)
    cards.write_json(args.out_dir / "automatic-mail-coverage.json", report)
    (args.out_dir / "automatic-mail-preview.html").write_text(page, encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("preview_id", "registry_status", "selected_count", "replaced_count", "retained_count", "delivery_eligible")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
