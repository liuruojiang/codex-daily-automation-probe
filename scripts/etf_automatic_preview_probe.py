"""Acceptance probe against a saved real collection; all faults are synthetic."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import urllib.request
from unittest.mock import patch

import etf_automatic_mail_preview as automatic
import etf_content_cards as cards
from etf_preflight import canonical
from validate_etf_delivery import LinkParser


def run(source, registry_path, out):
    automatic.preview.require(not out.exists(), "probe requires a new output directory")
    inputs = [source / name for name in ("metadata.json", "collection_manifest.json", "history_before.json")]
    before = [hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs]
    registry = cards.read_json(registry_path)
    broken = copy.deepcopy(registry);broken["entries"][0]["editor"]["card_hash"] = "synthetic-stale-editor"
    stale = copy.deepcopy(registry);stale["snapshot"]["body_sha256"] = "0" * 64
    rejected = copy.deepcopy(registry)
    first = rejected["entries"][0]
    first["review"]["overall"] = "revise"
    next(check for check in first["review"]["checks"] if check["claim_id"] == "facts.0")["verdict"] = "needs_revision"
    first["editor"]["review_hash"] = cards.digest(first["review"])
    cases = {"normal": (registry, 2, {"replaced": 2, "no_reviewed_card": 20}),
             "invalid_editor": (broken, 1, {"replaced": 1, "invalid_review_record": 1, "no_reviewed_card": 20}),
             "old_snapshot": (stale, 0, {"registry_snapshot_mismatch": 2, "no_reviewed_card": 20}),
             "model_rejection": (rejected, 1, {"replaced": 1, "review_not_complete": 1, "no_reviewed_card": 20}),
             "no_registry": (None, 0, {"no_reviewed_card": 22})}
    out.mkdir(parents=True)
    results, calls = {}, []
    def forbidden(*args, **kwargs):
        calls.append("unexpected network/model call")
        raise AssertionError("automatic preview must stay offline")
    with patch.object(cards, "call_json", side_effect=forbidden), patch.object(urllib.request, "urlopen", side_effect=forbidden):
        for name, (candidate, expected, reasons) in cases.items():
            arguments = ["--metadata", str(inputs[0]), "--manifest", str(inputs[1]), "--history-before", str(inputs[2]), "--out-dir", str(out/name)]
            if candidate is not None:
                path = out / (name + "-registry.json");cards.write_json(path, candidate)
                arguments += ["--registry", str(path)]
            automatic.main(arguments)
            report = cards.read_json(out/name/"automatic-mail-coverage.json")
            page = (out/name/"automatic-mail-preview.html").read_text(encoding="utf-8")
            automatic.preview.require(report["selected_count"] == 22 and report["replaced_count"] == expected
                                      and report["reason_counts"] == reasons and report["delivery_eligible"] is False, "unexpected scenario coverage")
            automatic.preview.require(report["original_content_audit"] == {"status": "FAILED", "failures": 7, "gaps": 119}, "original warnings changed")
            if expected == 0:
                automatic.preview.require(report["preview_body_sha256"] == report["original_body_sha256"], "zero-coverage body changed")
            parser = LinkParser();parser.feed(page)
            automatic.preview.require(all(canonical(row["url"]) in parser.links for row in report["items"])
                                      and "发送前缺漏检查：FAILED" in page, "preview links/warnings missing")
            metadata, manifest, history = [cards.read_json(path) for path in inputs]
            replay, email = automatic.build(metadata, manifest, history, candidate)
            automatic.preview.require(replay == report and automatic.render(replay, email) == page, "non-deterministic offline replay")
            results[name] = {"selected_count": 22, "replaced_count": expected, "retained_count": 22-expected,
                             "reason_counts": reasons, "preview_id": report["preview_id"], "replay_identical": True}
    after = [hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs]
    automatic.preview.require(before == after and not calls, "source files changed or network used")
    proof = {"version": "etf-automatic-preview-probe-v1", "source_inputs_unchanged": True, "network_and_model_calls": 0,
             "delivery_eligible": False, "faults": "explicit synthetic modifications, not actual provider results", "cases": results}
    cards.write_json(out/"automatic-preview-proof.json", proof)
    print(json.dumps({"probe": proof["version"], "source_inputs_unchanged": True, "network_and_model_calls": 0,
                      "delivery_eligible": False, "cases": {name: row["replaced_count"] for name, row in results.items()}}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    run(args.source_dir, args.registry, args.out_dir)


if __name__ == "__main__":
    main()
