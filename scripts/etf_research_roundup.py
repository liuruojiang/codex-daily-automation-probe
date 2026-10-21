"""Join exact reviewed research versions into one unsent candidate preview."""
from __future__ import annotations

import argparse
import copy
import hashlib
import html
import json
from pathlib import Path
import urllib.request
from unittest.mock import patch

import etf_automatic_mail_preview as automatic
import etf_content_cards as cards
import etf_mail_preview as mail
import etf_original_research as originals
import etf_research_resolution as resolution
import etf_section_research as sections
import etf_source_intake as intake
import etf_supplement_research as paper

VERSION = "etf-research-candidate-index-v1"
FILES = {"intake": "etf-source-intake-audit.json", "paper": "etf-supplement-research-record.json",
         "original": "etf-original-research-records.json", "crypto_resolution": "etf-crypto-research-resolution.json",
         "sections": "etf-futures-section-research.json"}
require = mail.require


def current_entries(records):
    old = records["original"];new = records["crypto_resolution"];replacement = new["entry"]
    require(new["prior_research_sha256"] == cards.digest(old), "roundup supersession context changed")
    matches = [e for e in old["entries"] if e["material_id"] == replacement["material_id"]]
    require(len(matches) == 1 and matches[0]["review"]["overall"] == "revise"
            and old["source_review_status"][replacement["material_id"]] in ("editorial_revision_required", "semantic_resolution_required")
            and new["repair"]["plan"]["parent_card_hash"] == matches[0]["review"]["card_hash"],
            "roundup requires explicit exact held-parent replacement")
    rows = [("paper", records["paper"]["entry"])]
    rows += [("crypto_resolution", replacement) if e["material_id"] == replacement["material_id"] else ("original", e)
             for e in old["entries"]]
    rows.append(("sections", records["sections"]["entry"]))
    ids = [e["material_id"] for _, e in rows]
    urls = [automatic.canonical(mail.safe_link(e["url"])) for _, e in rows]
    require(len(ids) == len(set(ids)) == len(set(urls)), "duplicate research material or source URL")
    return rows


def resolve_entries(records, registry, index):
    require(index["version"] == VERSION and index["record_sha256"] == {role: cards.digest(records[role]) for role in FILES}
            and index["registry_sha256"] == cards.digest(registry), "roundup input fingerprint changed")
    require(index["snapshot"] == registry["snapshot"] and index["delivery_eligible"] is False
            and index["external_fact_check"] == "not_performed", "roundup snapshot/delivery state changed")
    expected = current_entries(records)
    require(isinstance(index["entries"], list) and len(index["entries"]) == len(expected), "roundup entry coverage mismatch")
    for row, (role, entry) in zip(index["entries"], expected):
        require(set(row) == {"record", "material_id", "entry_sha256", "annotation"} and row["record"] == role
                and row["material_id"] == entry["material_id"] and row["entry_sha256"] == cards.digest(entry),
                "roundup exact entry/order changed")
        note = row["annotation"]
        require(set(note) == {"material_id", "date_status", "date_note_zh", "scope_note_zh"}
                and note["material_id"] == entry["material_id"] and note["date_status"] in ("captured", "conflicting", "unconfirmed")
                and cards.chinese(note["date_note_zh"]) and cards.chinese(note["scope_note_zh"]), "invalid roundup source/date annotation")
    return expected


def build(materials, futures_parent, records, registry, index, metadata, manifest, history):
    entries = resolve_entries(records, registry, index)
    intake.validate_audit(records["intake"], manifest, history, registry)
    by_id = {m["material_id"]: m for m in materials}
    require(len(by_id) == len(materials) == len(entries) and set(by_id) == {e["material_id"] for _, e in entries},
            "roundup material coverage mismatch")
    p, old, crypto, selected = (records[k] for k in ("paper", "original", "crypto_resolution", "sections"))
    proofs = {"paper": paper.build(by_id[p["entry"]["material_id"]], p, records["intake"], registry),
              "historical_original": originals.build([by_id[e["material_id"]] for e in old["entries"]], old, p, registry),
              "crypto_resolution": resolution.build(by_id[crypto["entry"]["material_id"]], crypto, old, p, registry),
              "sections": sections.build(futures_parent, by_id[selected["entry"]["material_id"]], selected, p, crypto, registry)}
    bundle = automatic.empty_bundle()
    for row, (_, entry) in zip(index["entries"], entries):
        mid = entry["material_id"];one = automatic.decode_entry(entry, by_id[mid]);bundle["materials"].append(by_id[mid])
        for field in ("cards", "reviews", "editors"):bundle[field].update(one[field])
        bundle["annotations"][mid] = copy.deepcopy(row["annotation"])
    research = mail.build(bundle, metadata, manifest, history)
    require(research["appendix_count"] == len(entries) and research["pending_count"] == 0, "roundup includes incomplete research")
    baseline, email = automatic.build(metadata, manifest, history, registry)
    injected = copy.deepcopy(registry);injected["entries"].extend(copy.deepcopy(e) for _, e in entries)
    test, injected_email = automatic.build(metadata, manifest, history, injected)
    outcomes = test["registry_entries"][len(registry["entries"]):]
    require(test["replaced_count"] == baseline["replaced_count"] == len(registry["entries"])
            and injected_email == email and test["preview_body_sha256"] == baseline["preview_body_sha256"]
            and all(row["status"] != "used" for row in outcomes), "roundup research changed frozen email")
    for row, outcome in zip(research["items"], outcomes):
        row["frozen_mail_reason"] = outcome["reason"]
        row["editorial_claim_count"] = len(bundle["editors"][row["material_id"]]["checks"])
    report = {"version": "etf-research-roundup-proof-v1", "index_sha256": cards.digest(index),
              "snapshot_report_date": research["snapshot_report_date"],
              "research_count": len(entries), "editorial_claim_count": sum(r["editorial_claim_count"] for r in research["items"]),
              "superseded_versions_shown": 0, "explicit_replacements": 1, "source_proofs": proofs,
              "research_items": research["items"], "baseline": baseline, "frozen_gate_counts": {
                  reason: sum(row["reason"] == reason for row in outcomes) for reason in sorted({row["reason"] for row in outcomes})},
              "mail_selected_count": baseline["selected_count"], "mail_replacements_remain": baseline["replaced_count"],
              "mail_retained_count": baseline["retained_count"], "frozen_mail_body_unchanged": True,
              "external_fact_check": "not_performed", "delivery_eligible": False, "sent": False, "history_updates": 0}
    return report, automatic.render(baseline, email)


CSS = '<style>.research-roundup{padding:22px;border:1px solid #cad8ef;border-radius:12px;background:#edf3fc;margin-bottom:24px}.research-roundup h1{font-size:25px;line-height:1.5}.research-roundup p,.research-roundup li{font-size:14px;line-height:1.8}.roundup-card{padding:20px 0;border-top:1px solid #cbd5e2}.roundup-meta{font-size:12px;color:#536579;line-height:1.8}.roundup-table{border-collapse:collapse;width:100%;font-size:12px}.roundup-table td,.roundup-table th{padding:9px;border-bottom:1px solid #cbd5e2;text-align:left;vertical-align:top}.roundup-scroll{overflow:auto}.roundup-card summary{cursor:pointer;font-size:13px;margin:10px 0}</style>'


def render(report, baseline_html):
    esc = html.escape;require(baseline_html.count('<main class="email-main">') == 1, "roundup email structure changed")
    items = report["research_items"]
    intro = '<section class="research-roundup" id="research-candidate-index"><p>第20步 · 未发送候选总览</p><h1>'+str(report['research_count'])+'份独立研究与'+str(report['mail_selected_count'])+'篇邮件候选正文</h1>'
    intro += '<p>保留来源日期字段及各事件口径，不将后补研读刷新为今天的资讯。前半部分展示当前已完成来源核对的研究稿；后半部分展示原'+str(report['mail_selected_count'])+'篇候选正文，其中'+str(report['mail_replacements_remain'])+'篇使用核对稿、'+str(report['mail_retained_count'])+'篇保留。原FAILED警报继续保留。</p>'
    intro += '<p>加密篇采用明确修订后的版本，旧拒绝仍在审核记录中。来源核对只表示忠实转述，外部论文、数据、代码及策略效果没有独立验证；本页无发送资格。</p><div class="roundup-scroll"><table class="roundup-table"><thead><tr><th>研究材料</th><th>保留的日期字段</th><th>原快照日期窗口</th><th>原邮件准入</th></tr></thead><tbody>'
    windows = {"primary_window": "字段位于36小时内", "research_backfill": "符合14天研究回溯", "outside_window": "不在原适用窗口内", "after_cutoff": "晚于原截止", "publication_unconfirmed": "日期未确认"}
    for row in items:
        reason = '原选文URL的后补捕获，版本不符' if row["frozen_mail_reason"] == "stale_material" else '不属于原22条选文'
        intro += '<tr><td>'+esc(row['card']['title_zh'])+'</td><td>'+esc(row['published'] or '未知')+'</td><td>'+windows[row['date_gate']['window']]+'</td><td>'+reason+'</td></tr>'
    intro += '</tbody></table></div><p class="roundup-meta">日期门槛按'+esc(report['snapshot_report_date'])+'原快照核对。位于时间窗口内也不代表通过原始捕获、来源身份或历史门槛。论文日期保留RSS宣布字段；首次提交与版本修订日期分别写于下方注释，不混为首次发表日。</p>'
    for row in items:
        card = row['card'];intro += '<article class="roundup-card"><h2 class="report-h2">'+esc(card['title_zh'])+'</h2>'
        intro += '<p class="roundup-meta">'+esc(row['source'])+' · '+esc(row['published'] or '未知')+' · '+str(row['editorial_claim_count'])+'项编辑核对</p>'
        intro += '<p class="roundup-meta">'+esc(row['annotation']['scope_note_zh'])+'</p><p class="roundup-meta">'+esc(row['annotation']['date_note_zh'])+'</p>'
        intro += '<ul class="report-list">'+''.join('<li class="report-li">'+esc(c['text_zh'])+'</li>' for c in card['facts'])+'</ul>'
        intro += '<details><summary>原文限制与后续核对问题</summary>'
        for field, label in (("limitations", "原文限制"), ("questions", "后续核对问题")):
            if card[field]:intro += '<p><strong>'+label+'</strong></p><ul>'+''.join('<li>'+esc(c['text_zh'] if isinstance(c, dict) else c)+'</li>' for c in card[field])+'</ul>'
        intro += '</details><p><a class="email-link" href="'+esc(mail.safe_link(row['url']),quote=True)+'">阅读原文</a></p></article>'
    intro += '<p class="roundup-meta">以下候选正文复用原邮件渲染结果；没有把上面的研究稿加入发送列表。页面仅完成静态内容/链接/结构检查，视觉及邮件客户端兼容性未验收。</p></section>'
    rendered = baseline_html.replace('</head>', CSS+'</head>').replace('<main class="email-main">', '<main class="email-main">'+intro)
    require(rendered.replace(CSS, '', 1).replace(intro, '', 1) == baseline_html, "roundup changed existing candidate preview")
    return rendered


def load_materials(records, manifest, raw_original_dir=None, raw_paper_html=None):
    require((raw_original_dir is None) == (raw_paper_html is None), "offline roundup requires both original and paper captures")
    original_materials = originals.rebuild_sources(records["paper"], raw_original_dir)
    saved = next(s for s in records["intake"]["supplements"] if s["material_id"] == records["paper"]["entry"]["material_id"])
    if raw_paper_html is not None:raw = raw_paper_html.read_bytes()
    else:
        import re
        require(re.fullmatch(r"https://arxiv\.org/html/\d{4}\.\d{4,5}v\d+", saved["source_url"]), "unexpected roundup paper URL")
        with urllib.request.urlopen(saved["source_url"], timeout=30) as response:
            require(response.geturl() == saved["source_url"], "roundup paper redirected");raw = response.read(4000001)
        require(len(raw) <= 4000000, "roundup paper too large")
    parent = next(m for m in cards.prepare_manifest(manifest) if m["material_id"] == saved["parent_material_id"])
    paper_material, _ = intake.extract_arxiv(parent, raw, saved["source_url"], saved["paragraph_ids"])
    selected = records["sections"]["selection"]
    spec = next(s["extraction"] for s in records["paper"]["original_sources"] if s["extraction"]["material_id"] == selected["parent_material_id"])
    raw = ((raw_original_dir/hashlib.sha256(spec["url"].encode()).hexdigest()/"response.html").read_bytes()
           if raw_original_dir is not None else originals.fetch_source(spec["url"]))
    futures_parent, futures_material, selection = sections.extract_sections(records["paper"], spec["material_id"], raw, selected["selected_titles"])
    require(selection == selected, "roundup whole-section selection changed")
    return [paper_material, *original_materials, futures_material], futures_parent


def probe(source, config, index_path, out_dir, raw_original_dir=None, raw_paper_html=None):
    require(not out_dir.exists(), "roundup output directory already exists")
    paths = [source/"metadata.json", source/"collection_manifest.json", source/"history_before.json",
             config/"etf-shadow-reviewed-registry.json", index_path, *[config/name for name in FILES.values()]]
    before = [intake.digest_bytes(p.read_bytes()) for p in paths]
    metadata, manifest, history, registry, index, *values = [cards.read_json(p) for p in paths]
    records = dict(zip(FILES, values));materials, parent = load_materials(records, manifest, raw_original_dir, raw_paper_html)
    def forbidden(*args, **kwargs):raise AssertionError("roundup must not call a model")
    with patch.object(cards, "call_json", side_effect=forbidden):
        report, baseline = build(materials, parent, records, registry, index, metadata, manifest, history)
        again, repeat = build(materials, parent, records, registry, index, metadata, manifest, history)
        require(report == again and baseline == repeat, "roundup replay differs")
        rendered = render(report, baseline)
    require(before == [intake.digest_bytes(p.read_bytes()) for p in paths], "roundup inputs changed")
    report.update(source_inputs_unchanged=True, replay_identical=True, model_requests=0, live_public_fetch=raw_original_dir is None)
    cards.write_json(out_dir/"research-roundup-proof.json", report)
    (out_dir/"research-candidate-preview.html").write_text(rendered, encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("version", "research_count", "editorial_claim_count", "frozen_gate_counts", "mail_selected_count", "mail_replacements_remain", "mail_retained_count", "source_inputs_unchanged", "replay_identical", "model_requests", "live_public_fetch", "delivery_eligible")}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source-dir", "config-dir", "index", "out-dir"):parser.add_argument("--"+name,type=Path,required=True)
    parser.add_argument("--raw-original-dir", type=Path);parser.add_argument("--raw-paper-html", type=Path);args=parser.parse_args()
    probe(args.source_dir,args.config_dir,args.index,args.out_dir,args.raw_original_dir,args.raw_paper_html)


if __name__ == "__main__":main()
