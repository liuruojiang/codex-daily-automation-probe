"""Bounded, resumable shadow batch. Never sends mail or approves publication."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import html
import json
import os
from pathlib import Path
import urllib.parse

import etf_content_cards as cards
import etf_semantic_review as semantic
import etf_material_kind as material_kind

VERSION = "etf-shadow-run-v2"


@contextmanager
def run_lock(folder: Path):
    """OS-released lock: a killed process cannot leave a permanent busy flag."""
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "run.lock").open("a+b") as handle:
        handle.seek(0)
        if os.fstat(handle.fileno()).st_size == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise RuntimeError("shadow directory already in use") from None
        else:
            import fcntl
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise RuntimeError("shadow directory already in use") from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def select_materials(materials: list[dict], limit: int) -> list[dict]:
    cards.replay(materials, {})
    if limit <= 0:
        raise ValueError("positive material limit required")
    # Keep the richest captured revision per URL, without upgrading its level.
    unique = {}
    for material in sorted(materials, key=lambda m: m["material_id"]):
        url = cards.dr.canonical_url(material["url"])
        score = (material_kind.classify(material)["eligible"], cards.basic_ready(material), sum(len(p["text"]) for p in material["paragraphs"]))
        if url not in unique or score > unique[url][0]:
            unique[url] = (score, material)
    return sorted((v[1] for v in unique.values()), key=lambda m: (not material_kind.classify(m)["eligible"], m["material_id"]))[:limit]


def validate_config(config: dict) -> None:
    fields = {"run_label", "endpoint", "generator", "reviewer", "thinking", "max_requests", "max_reserved_output_tokens", "generation_tokens", "review_tokens", "max_input_chars"}
    if set(config) != fields:
        raise ValueError("invalid shadow configuration fields")
    if any(not isinstance(config[k], str) or not config[k].strip() for k in ("run_label", "endpoint", "generator", "reviewer")) or config["thinking"] not in (None, "enabled", "disabled"):
        raise ValueError("invalid model configuration")
    url = urllib.parse.urlsplit(config["endpoint"])
    if url.scheme != "https" or not url.netloc or url.username or url.password or url.query or url.fragment:
        raise ValueError("explicit HTTPS endpoint without credentials/query required")
    if any(type(config[k]) is not int or config[k] < 0 for k in ("max_requests", "max_reserved_output_tokens")) or any(type(config[k]) is not int or config[k] <= 0 for k in ("generation_tokens", "review_tokens", "max_input_chars")):
        raise ValueError("invalid cumulative request limits")


def request_key(payload: dict, config: dict) -> str:
    return cards.digest({"version": VERSION, "endpoint": config["endpoint"], "payload": payload})


def payload_for(stage: str, material: dict, journal: dict) -> dict:
    config = journal["config"]
    if stage == "generate":
        return cards.request_payload(material, config["generator"], config["generation_tokens"], config["thinking"])
    card = journal["attempts"]["generate:" + material["material_id"]]["output"]
    return semantic.review_payload(material, card, config["reviewer"], config["review_tokens"])


def accept_response(attempt: dict, raw: dict, material: dict, stage: str, journal: dict) -> None:
    attempt.update(usage=raw.get("usage"), reported_model=raw.get("model"))
    try:
        output = cards.model_card(raw, material if stage == "generate" else None)
        errors = cards.validate_card(material, output) if stage == "generate" else semantic.validate_review(
            material, journal["attempts"]["generate:" + material["material_id"]]["output"], output)
        attempt.update(state="parsed", output=output, validation_errors=errors)
    except Exception as exc:
        attempt.update(state="failed", error_type=type(exc).__name__)


def advance(materials: list[dict], config: dict, source_hash: str, out_dir: Path, *,
            live: bool, max_new_requests: int, api_key: str = "", transport=None) -> dict:
    """All reservations survive resume; unknown requests retain their budget."""
    cards.replay(materials, {})
    validate_config(config)
    if max_new_requests < 0:
        raise ValueError("invalid invocation request limit")
    if live and not api_key:
        raise ValueError("live run requires ETF_SHADOW_API_KEY")
    context = {"version": VERSION, "config": config, "source_sha256": source_hash,
               "materials": materials, "generation_prompt": cards.SYSTEM_PROMPT,
               "review_prompt": semantic.REVIEW_PROMPT, "validator": cards.VALIDATOR_VERSION,
               "review_version": semantic.VERSION, "material_policy": material_kind.VERSION}
    context_id = cards.digest(context)
    with run_lock(out_dir):
        checkpoint = out_dir / "run.json"
        if checkpoint.exists():
            journal = cards.read_json(checkpoint)
            if journal.get("context_id") != context_id or journal.get("config") != config:
                raise ValueError("shadow context changed; use a separate directory")
        else:
            if any(p.name != "run.lock" for p in out_dir.iterdir()):
                raise ValueError("nonempty shadow directory has no checkpoint")
            journal = {"version": VERSION, "context_id": context_id, "config": config,
                       "source_sha256": source_hash, "attempts": {}}
            cards.write_json(out_dir / "materials.json", materials)
            cards.write_json(checkpoint, journal)
        used_now = 0
        for material in materials:
            if not material_kind.classify(material)["eligible"]:
                continue
            mid = material["material_id"]
            for stage in ("generate", "review"):
                if stage == "review":
                    previous = journal["attempts"].get("generate:" + mid, {})
                    if previous.get("state") != "parsed" or previous["validation_errors"] or semantic.terminology_issues(previous["output"]):
                        break
                payload = payload_for(stage, material, journal)
                key = request_key(payload, config)
                slot = stage + ":" + mid
                attempt = journal["attempts"].get(slot)
                if attempt is not None:
                    if attempt["request_key"] != key:
                        raise ValueError("cached request no longer matches its source/card")
                    if attempt["state"] == "reserved":
                        receipt = out_dir / "receipts" / (key + ".json")
                        try:
                            raw = cards.read_json(receipt) if receipt.exists() else {}
                        except (OSError, ValueError):
                            raw = {}
                        if isinstance(raw, dict) and raw.get("state") == "completed" and raw.get("request_key") == key and raw.get("material_id") == mid and isinstance(raw.get("response"), dict):
                            accept_response(attempt, raw["response"], material, stage, journal)
                        else:
                            attempt.update(state="unknown", error_type="OutcomeUnknown")
                        cards.write_json(checkpoint, journal)
                    continue
                if not live:
                    continue
                if len(json.dumps(payload, ensure_ascii=False)) > config["max_input_chars"]:
                    journal["attempts"][slot] = {"request_key": key, "state": "blocked_input", "reserved_tokens": 0,
                                                 "error_type": "InputLimitExceeded"}
                    cards.write_json(checkpoint, journal)
                    continue
                reservations = [a for a in journal["attempts"].values() if a.get("reserved_tokens", 0)]
                if used_now >= max_new_requests or len(reservations) >= config["max_requests"] or sum(a["reserved_tokens"] for a in reservations) + payload["max_completion_tokens"] > config["max_reserved_output_tokens"]:
                    continue
                # Persist before any network operation. A crash consumes this
                # reservation even if we cannot establish whether it was sent.
                attempt = {"request_key": key, "state": "reserved", "reserved_tokens": payload["max_completion_tokens"]}
                journal["attempts"][slot] = attempt
                cards.write_json(checkpoint, journal)
                used_now += 1
                try:
                    raw = cards.call_json(payload, out_dir / "receipts", endpoint=config["endpoint"], api_key=api_key,
                                          namespace=VERSION, material_id=mid, transport=transport)
                    accept_response(attempt, raw, material, stage, journal)
                except Exception as exc:
                    attempt.update(state="unknown", error_type=type(exc).__name__)
                cards.write_json(checkpoint, journal)
        report = build_report(materials, journal)
        cards.write_json(out_dir / "shadow-report.json", report)
        responses = {m["material_id"]: journal["attempts"]["generate:" + m["material_id"]]["output"] for m in materials
                     if journal["attempts"].get("generate:" + m["material_id"], {}).get("state") == "parsed"}
        reviews = {m["material_id"]: journal["attempts"]["review:" + m["material_id"]]["output"] for m in materials
                   if journal["attempts"].get("review:" + m["material_id"], {}).get("state") == "parsed"
                   and not journal["attempts"]["review:" + m["material_id"]]["validation_errors"]}
        cards.write_json(out_dir / "responses.json", responses)
        cards.write_json(out_dir / "reviews.json", reviews)
        (out_dir / "shadow-preview.html").write_text(render_html(materials, responses, report), encoding="utf-8")
        return report


def build_report(materials: list[dict], journal: dict) -> dict:
    rows = []
    for material in materials:
        mid = material["material_id"]
        row = {"material_id": mid, "title": material["title"], "url": material["url"], "errors": []}
        row["material_profile"] = profile = material_kind.classify(material)
        gen = journal["attempts"].get("generate:" + mid)
        rev = journal["attempts"].get("review:" + mid)
        if not profile["eligible"]:
            row["status"] = "insufficient_material" if profile["kind"] == "insufficient_material" else "source_text_required"
        elif gen is None:
            row["status"] = "awaiting_generation"
        elif gen["state"] != "parsed":
            row.update(status="generation_failed", errors=[gen.get("error_type", gen["state"])])
        else:
            row = {**row, **semantic.evaluate(material, gen["output"], rev.get("output") if rev and rev["state"] == "parsed" else None)}
            if row["status"] == "awaiting_semantic_review" and rev:
                row.update(status="review_failed", errors=[rev.get("error_type", rev["state"])])
        row["editorial_review"] = "pending"
        rows.append(row)
    attempts = list(journal["attempts"].values())
    reserved = [a for a in attempts if a.get("reserved_tokens", 0)]
    usage = [a["usage"] for a in reserved if isinstance(a.get("usage"), dict)
             and type(a["usage"].get("total_tokens")) is int and a["usage"]["total_tokens"] >= 0]
    return {"version": VERSION, "context_id": journal["context_id"], "mode": "shadow_only",
            "external_fact_check": "not_performed", "editorial_review": "pending", "items": rows,
            "budget": {"requests_reserved": len(reserved), "max_requests": journal["config"]["max_requests"],
                       "output_tokens_reserved": sum(a["reserved_tokens"] for a in reserved),
                       "max_reserved_output_tokens": journal["config"]["max_reserved_output_tokens"],
                       "provider_reported_total_tokens": sum(u.get("total_tokens", 0) for u in usage),
                       "usage_missing_requests": len(reserved) - len(usage),
                       "unknown_outcomes": sum(a["state"] == "unknown" for a in reserved),
                       "monetary_cap": "not_established"}}


def render_html(materials: list[dict], responses: dict, report: dict) -> str:
    esc = html.escape
    labels = {"model_review_passed": "模型原文复核通过，待编辑审阅", "semantic_revision_required": "语义需修订",
              "blocked_by_evidence": "引用/数字拒绝", "blocked_by_terminology": "术语拒绝", "review_invalid": "复核协议无效",
              "review_failed": "复核未完成", "awaiting_semantic_review": "待模型复核", "generation_failed": "生成未完成",
              "awaiting_generation": "待生成", "insufficient_material": "材料不足", "source_text_required": "疑似目录，需补正文"}
    budget = report["budget"]
    summary = '<section><h2>本批次执行记录</h2><p>累计请求预留：' + str(budget["requests_reserved"]) + '/' + str(budget["max_requests"]) + '；累计输出token预留：' + str(budget["output_tokens_reserved"]) + '/' + str(budget["max_reserved_output_tokens"]) + '。</p><p>未知结果不自动重试。上限限制请求数与配置的输出长度，不是精确美元预算；实际输入也计费，提供方用量可能缺失。所有稿件仍待编辑审阅及外部事实核验。</p><ul>'
    for row in report["items"]:
        summary += '<li>' + esc(row["title"]) + '：' + labels[row["status"]] + (' · ' + esc(', '.join(row["errors"])) if row["errors"] else '') + '</li>'
    summary += '</ul></section>'
    rendered = cards.render_html(cards.replay(materials, responses))
    return rendered.replace('<h1>资产日报中文解读对照稿</h1>', '<h1>资产日报独立影子运行</h1>' + summary)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["plan", "run", "replay"])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--materials", type=Path)
    group.add_argument("--manifest", type=Path)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--run-label", required=True)
    parser.add_argument("--limit", type=int, default=6)
    parser.add_argument("--max-requests", type=int, default=4)
    parser.add_argument("--max-new-requests", type=int, default=1)
    parser.add_argument("--max-reserved-output-tokens", type=int, default=11200)
    parser.add_argument("--generation-tokens", type=int, default=2400)
    parser.add_argument("--review-tokens", type=int, default=3200)
    parser.add_argument("--max-input-chars", type=int, default=60000)
    parser.add_argument("--thinking", choices=["enabled", "disabled"])
    args = parser.parse_args(argv)
    if args.max_requests < 0 or args.max_reserved_output_tokens < 0 or min(args.generation_tokens, args.review_tokens, args.max_input_chars, args.limit) <= 0 or not args.run_label.strip():
        parser.error("invalid limits or run label")
    source = args.materials or args.manifest
    materials = cards.read_json(source) if args.materials else cards.prepare_manifest(cards.read_json(source))
    selected = select_materials(materials, args.limit)
    config = {"run_label": args.run_label, "endpoint": os.environ.get("ETF_SHADOW_API_URL", ""),
              "generator": os.environ.get("ETF_SHADOW_GENERATOR", ""), "reviewer": os.environ.get("ETF_SHADOW_REVIEWER", ""),
              **{k: getattr(args, k) for k in ("max_requests", "max_reserved_output_tokens", "generation_tokens", "review_tokens", "max_input_chars", "thinking")}}
    if not all(config[k] for k in ("endpoint", "generator", "reviewer")):
        parser.error("configure ETF_SHADOW_API_URL, ETF_SHADOW_GENERATOR and ETF_SHADOW_REVIEWER")
    report = advance(selected, config, hashlib.sha256(source.read_bytes()).hexdigest(), args.out_dir,
                     live=args.mode == "run", max_new_requests=args.max_new_requests,
                     api_key=os.environ.get("ETF_SHADOW_API_KEY", ""))
    counts = {s: sum(r["status"] == s for r in report["items"]) for s in sorted({r["status"] for r in report["items"]})}
    print(json.dumps({"budget": report["budget"], "counts": counts}))
    return int(any(r["status"] != "model_review_passed" for r in report["items"]))


if __name__ == "__main__":
    raise SystemExit(main())
