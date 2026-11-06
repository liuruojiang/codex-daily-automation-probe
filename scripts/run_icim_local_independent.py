"""IC/IM-only native software gate and post-gate local research runner.

Reuses the authenticated IC/IM portion of an existing prepared snapshot. No
microcap source, consumer, resident, fixture, or acceptance is consulted.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, time as day_time
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import run_local_intraday as shared


def native_module(home):
    path = home / "scripts/realtime_daily_gate.py"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("_independent_icim_native_gate", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def remote_workflow(native):
    repository = "liuruojiang/codex-daily-automation-probe"
    pin = native.run(["gh", "api", "repos/" + repository + "/commits/main", "--jq", ".sha"])
    response = json.loads(native.run(["gh", "api", "repos/" + repository + "/contents/.github/workflows/ic-im-v1-4-daily-digest.yml?ref=" + pin]))
    raw = base64.b64decode(response["content"])
    return dict(automation_pin=pin, workflow_sha256=hashlib.sha256(raw).hexdigest(),
                strategy_pin=native.pin_from_workflow(raw.decode("utf8"), "liuruojiang/ic-im-rolling-arbitrage"))


def bindings(home):
    entries = ["run_icim_local_independent.py", "run_local_intraday.py", "build_local_intraday_report.py", "build_ic_im_v1_4_digest.py"]
    result = {str((Path(__file__).parent / name).resolve()): shared.sha(Path(__file__).parent / name) for name in entries}
    for name in ("realtime_daily_gate.py", "daily_microcap_test_worker.py"):
        path = home / "scripts" / name
        result[str(path)] = shared.sha(path)
    native = native_module(home)
    scripts = Path(__file__).resolve().parent
    for name in native.local_import_closure(scripts, ["build_ic_im_v1_4_digest.py"]):
        result[str((scripts / name).resolve())] = shared.sha(scripts / name)
    return result


def verify_icim(native, prepared, config, expected_pin):
    receipt = shared.read(prepared / "prepared.json")
    if receipt["strategy_pins"]["icim"] != expected_pin:
        raise ValueError("ICIM snapshot differs from actual production workflow pin")
    native.verify_inventory(prepared / "icim", receipt["inventories"]["icim"])
    manifest_path = "docs/ic_im_daily_release_20261023_repairs_v3_r3_manifest.json"
    source = Path(config["icim_source"])
    raw = subprocess.check_output(["git", "-C", str(source), "show", expected_pin + ":" + manifest_path])
    if (prepared / "icim" / manifest_path).read_bytes().replace(b"\r\n", b"\n") != raw.replace(b"\r\n", b"\n"):
        raise ValueError("ICIM manifest does not match immutable strategy pin")
    manifest = json.loads(raw)
    production = {}
    for relative, expected in manifest["files"].items():
        published = subprocess.check_output(["git", "-C", str(source), "show", expected_pin + ":" + relative])
        current = (source / relative).read_bytes()
        if hashlib.sha256(published).hexdigest() != expected or current.replace(b"\r\n", b"\n") != published.replace(b"\r\n", b"\n"):
            raise ValueError("Unverified ICIM production execution file: " + relative)
        production[relative] = hashlib.sha256(current).hexdigest()
    if receipt.get("icim_supplemental_tests") != native.ICIM_SUPPLEMENTAL:
        raise ValueError("ICIM independent five-test inventory differs")
    if receipt.get("icim_supplemental_bindings") != native.icim_supplemental_bindings(prepared / "icim"):
        raise ValueError("ICIM independent five-test dependencies differ")
    return production


def gate(home, prepared, out):
    native = native_module(home)
    config = shared.read(home / "scripts/realtime_daily_gate.json")
    calendar = native.independent_calendar(Path(config["icim_source"]))
    out = shared.isolated_output(out, [Path(config["icim_source"]), prepared])
    result = dict(schema="icim_local_independent_gate_v1", calendar=calendar,
                  started_at=shared.clock().isoformat(), pass_=False, release_for_intraday=False,
                  prepared=str(prepared), prepared_sha256=shared.sha(prepared / "prepared.json"),
                  bindings=bindings(home), groups={})
    result["pass"] = False
    result.pop("pass_")
    if not calendar["is_trading_day"]:
        result.update(status="market_closed", completed_at=shared.clock().isoformat())
        shared.write(out / "gate.json", result)
        return result
    result["workflow"] = remote_workflow(native)
    before = verify_icim(native, prepared, config, result["workflow"]["strategy_pin"])
    result["production_before"] = before
    env = {**native.clean_test_env(), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
           "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "ICIM_REQUIRE_MIGRATION": "0",
           "ICIM_STATE_DIR": str(out / "isolated_state"), "PYTHONPATH": str(prepared / "icim"),
           "HYPOTHESIS_STORAGE_DIRECTORY": str(out / "hypothesis")}
    group = native.subprocess_gate([sys.executable, "-B", "-X", "utf8", str(prepared / "icim/run_ic_im_v1_4_delivery_tests.py"),
                                   "--out-dir", str(out / "icim_results")], prepared / "icim", env,
                                  out / "icim47", "icim47", timeout=195)
    group["pass"] = False
    receipt = out / "icim_results/receipt.json"
    if group["exit_code"] == 0 and receipt.exists():
        evidence = shared.read(receipt)
        group["official_receipt"] = evidence
        group["pass"] = evidence["source_before_equals_after"] and evidence["exit_code"] == 0 and all(evidence["counts"][k] == 0 for k in ("failures", "errors", "skipped"))
    result["groups"]["icim47"] = group
    if not native.UNCONFIRMED_CHILDREN:
        result["groups"]["independent5"] = native.icim_supplemental_group(prepared, out, home / "scripts/daily_microcap_test_worker.py")
    result["production_after"] = verify_icim(native, prepared, config, result["workflow"]["strategy_pin"])
    result["pass"] = (len(result["groups"]) == 2 and all(group["pass"] for group in result["groups"].values())
                      and before == result["production_after"] and bindings(home) == result["bindings"])
    result["completed_at"] = shared.clock().isoformat()
    evidence_files = [out / "icim_results/receipt.json"]
    if "official_receipt" in group:
        evidence_files += [out / "icim_results" / name for name in group["official_receipt"]["artifacts"]]
    if "independent5" in result["groups"]:
        evidence_files += [out / "icim_supplemental5" / name for name in ("regression.xml", "collection.json", "gate.json", "process.json")]
    result["artifacts"] = {str(path.resolve()): shared.sha(path) for path in evidence_files if path.exists()}
    now = shared.clock()
    result["release_for_intraday"] = result["pass"] and now.date().isoformat() == calendar["today"] and day_time(14, 15) <= now.time() < day_time(15)
    shared.write(out / "gate.json", result)
    return result


def verify_group_evidence(evidence):
    groups = evidence.get("groups", {})
    if set(groups) != {"icim47", "independent5"}:
        raise ValueError("Both complete ICIM regression groups are required")
    for name, group in groups.items():
        if group.get("pass") is not True or group.get("exit_code") != 0 or group.get("timed_out"):
            raise ValueError("ICIM regression group failed or timed out: " + name)
        receipt = group.get("official_receipt", {}) if name == "icim47" else group
        counts = receipt.get("counts", {})
        if not counts.get("tests", 0) or any(counts.get(key) != 0 for key in ("failures", "errors", "skipped")):
            raise ValueError("ICIM regression evidence is incomplete or nonzero: " + name)


class IndependentRun(shared.Run):
    def __init__(self, home, gate_path, ledger_source, out, python):
        self.home, self.gate, self.ledger_source = map(lambda value: Path(value).resolve(), (home, gate_path, ledger_source))
        self.config = shared.read(self.home / "scripts/realtime_daily_gate.json")
        self.icim = Path(self.config["icim_source"]).resolve()
        self.native = native_module(self.home)
        self.evidence = shared.read(self.gate)
        self.prepared = Path(self.evidence["prepared"])
        self.day = self.evidence["calendar"]["today"]
        self.anchor = self.evidence["calendar"]["latest_completed_session"]
        self.next_day = self.evidence["calendar"]["next_session"]
        self.gate_sha = shared.sha(self.gate)
        self.strategy = "icim"
        self.python = str(Path(python).resolve())
        self.authorize()
        self.out = shared.isolated_output(out, [self.icim, self.prepared])
        # Reuse the common claim: an existing/unknown two-family attempt must
        # be inspected rather than concurrently starting another ICIM runner.
        self.claim = shared.claim_day(self.home, self.day, self.out)

    def command(self, label, args, cwd, env=None, check_window=True):
        try:
            return super().command(label, args, cwd, env, check_window)
        except RuntimeError:
            record_path = self.out / (label + ".command.json")
            record = shared.read(record_path) if record_path.exists() else {}
            result_dir = self.out / "icim"
            if (label != "icim_realtime" or record.get("status") != "completed" or not record.get("exit_code")
                    or not any((result_dir / name).is_file() for name in ("result.json", "diagnostic_report.md"))):
                raise
            # The original exit code remains in the original command receipt.
            # run_icim still verifies that no canonical ledger byte changed.
            self.partial_runner_exit = record["exit_code"]
            return record

    def render(self, strategy, inputs):
        if not getattr(self, "partial_runner_exit", None):
            return super().render(strategy, inputs)
        try:
            result = super().render(strategy, inputs)
            result.update(status="partial", runner_exit_code=self.partial_runner_exit)
            shared.write(self.out / "icim_ready.json", result)
            return result
        except Exception as exc:
            # Preserve facts even when the old formal consumer cannot interpret
            # this partial shape. Never manufacture machine signals from text.
            directory = self.out / "icim_diagnostic_report"
            directory.mkdir(exist_ok=False)
            source_files = {}
            for name in ("result.json", "diagnostic_report.md"):
                source = self.out / "icim" / name
                if source.is_file():
                    raw = source.read_bytes()
                    (directory / name).write_bytes(raw)
                    source_files[name] = hashlib.sha256(raw).hexdigest()
            summary = (f"IC/IM {self.day}：已取得部分研究信息，原生入口退出码 {self.partial_runner_exit}。\n"
                       "完整目标尚未确认。下附当次原始结果/诊断；未从文字补造信号字段。\n")
            (directory / "summary.md").write_text(summary, encoding="utf8")
            diagnostic = directory / "diagnostic_report.md"
            (directory / "report.md").write_bytes(summary.encode("utf8") + (diagnostic.read_bytes() if diagnostic.exists() else b""))
            shared.write(directory / "manifest.json", dict(schema="icim_original_partial_information_v1", status="partial",
                         runner_exit_code=self.partial_runner_exit, renderer_error=str(exc), source_files=source_files,
                         signal_fields_synthesized=False, actual_publication=False))
            result = dict(event="report_ready_not_published", strategy="icim", status="partial",
                          summary=str(directory / "summary.md"), report=str(directory / "report.md"),
                          manifest=str(directory / "manifest.json"), created_at=shared.clock().isoformat(),
                          actual_publication=False, runner_exit_code=self.partial_runner_exit)
            shared.write(self.out / "icim_ready.json", result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
            return result

    def authorize(self):
        shared.require_window(self.day)
        if shared.sha(self.gate) != self.gate_sha or self.evidence.get("schema") != "icim_local_independent_gate_v1":
            raise ValueError("ICIM independent gate changed or schema differs")
        if not self.evidence.get("pass") or not self.evidence.get("release_for_intraday"):
            raise ValueError("A successful same-window ICIM daily gate is required")
        verify_group_evidence(self.evidence)
        started = datetime.fromisoformat(self.evidence["started_at"])
        if started.date().isoformat() != self.day or started.time() < day_time(14, 15):
            raise ValueError("ICIM daily gate must start after 14:15 on the signal session")
        if bindings(self.home) != self.evidence["bindings"] or shared.sha(self.prepared / "prepared.json") != self.evidence["prepared_sha256"]:
            raise ValueError("ICIM executable/helper/snapshot binding changed")
        if not self.evidence.get("artifacts") or any(shared.sha(Path(name)) != expected for name, expected in self.evidence["artifacts"].items()):
            raise ValueError("ICIM regression JUnit/collection/process evidence changed")
        actual = remote_workflow(self.native)
        if actual["strategy_pin"] != self.evidence["workflow"]["strategy_pin"] or actual["workflow_sha256"] != self.evidence["workflow"]["workflow_sha256"]:
            raise ValueError("ICIM production workflow changed after gate")
        production = verify_icim(self.native, self.prepared, self.config, actual["strategy_pin"])
        if production != self.evidence["production_after"]:
            raise ValueError("ICIM production source changed after gate")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("gate", "run"))
    parser.add_argument("--automation-home", type=Path, required=True)
    parser.add_argument("--prepared", type=Path)
    parser.add_argument("--gate", type=Path)
    parser.add_argument("--ledger-source", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args(argv)
    if args.mode == "gate":
        if not args.prepared:
            parser.error("gate requires --prepared (only its ICIM inventory is read)")
        result = gate(args.automation_home.resolve(), args.prepared.resolve(), args.out.resolve())
        print(json.dumps({"schema": result["schema"], "pass": result["pass"],
                          "release_for_intraday": result["release_for_intraday"],
                          "gate": str(args.out / "gate.json")}, ensure_ascii=False))
        return 0 if result["pass"] or result.get("status") == "market_closed" else 1
    if not args.gate or not args.ledger_source:
        parser.error("run requires --gate and --ledger-source")
    return IndependentRun(args.automation_home, args.gate, args.ledger_source, args.out, args.python).execute()


if __name__ == "__main__":
    raise SystemExit(main())
