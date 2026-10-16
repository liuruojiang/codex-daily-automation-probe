"""Bounded handoff tests: synthetic modules, real disposable files, no live state."""
import base64
from datetime import date, timedelta
import hashlib
import io
import json
from pathlib import Path
import sys
import types
import zipfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ic_im_readiness_handoff as handoff


def write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    state_dir, artifacts = tmp_path / "state", tmp_path / "artifacts"
    (state_dir / "journal").mkdir(parents=True)
    artifacts.mkdir()
    zero = dict(sequence=0, verified_day="2026-10-14", digest="a" * 64,
                previous_digest=None, research_only=True)
    latest = dict(sequence=1, verified_day="2026-10-15", digest="b" * 64,
                  previous_digest=zero["digest"], research_only=True)
    for record in (zero, latest):
        write_json(state_dir / "journal" / f"{record['sequence']:06d}-{record['verified_day']}.json", record)
    write_json(state_dir / "latest.json", latest)
    write_json(state_dir / "migration_record.json", dict(new_ledger=dict(digest=zero["digest"])))
    preflight = dict(strategy_sha="c" * 40, restored_digest=latest["digest"], completed_day="2026-10-15",
                     expected_market_day="2026-10-16", publication_mode="realtime")
    result = dict(status="ok", digest=latest["digest"], sequence=1, verified_day="2026-10-15",
                  market_date="2026-10-16", publication_mode="realtime", report_file="ic_im_v1_4_realtime_signal.md")
    write_json(artifacts / "preflight.json", preflight)
    write_json(artifacts / "result.json", result)
    (artifacts / result["report_file"]).write_text("synthetic research report; no account execution", encoding="utf-8")
    # Deliberately present but forbidden from the archive.
    (artifacts / "mail-transport.json").write_text("TRANSPORT_MUST_NOT_EXPORT", encoding="utf-8")
    (tmp_path / "private.txt").write_text("OUTSIDE_MUST_NOT_EXPORT", encoding="utf-8")
    monkeypatch.setenv("MAIL_PASSWORD", "ENV_MUST_NOT_EXPORT")
    inspected = []

    class FakeStore:
        def __init__(self, path):
            assert Path(path) == state_dir

        def load_latest(self):
            return json.loads((state_dir / "latest.json").read_text(encoding="utf-8"))

        def load_sequence(self, seq):
            inspected.append(seq)
            paths = list((state_dir / "journal").glob(f"{seq:06d}-*.json"))
            assert len(paths) == 1
            return json.loads(paths[0].read_text(encoding="utf-8"))

    def check_anchor(record):
        assert record["research_only"] is True

    def next_session(day):
        while day.weekday() > 4:
            day += timedelta(days=1)
        return day

    fake_state = types.ModuleType("poe_ic_im_v1_4_state")
    fake_state.StateStore = FakeStore
    fake_state.anchors_from_record = check_anchor
    fake_bot = types.ModuleType("poe_ic_im_mainline_v1_4_bot")
    fake_bot._roll_forward_exchange_day = next_session
    monkeypatch.setitem(sys.modules, fake_state.__name__, fake_state)
    monkeypatch.setitem(sys.modules, fake_bot.__name__, fake_bot)
    # main inserts its strategy root; restore the module path after each test.
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(sys, "argv", ["handoff", "--strategy-root", str(tmp_path / "synthetic-strategy"),
                        "--strategy-sha", preflight["strategy_sha"], "--source-artifact", "123",
                        "--state-dir", str(state_dir), "--artifact-dir", str(artifacts)])
    return dict(root=tmp_path, state=state_dir, artifacts=artifacts, preflight=preflight,
                result=result, inspected=inspected, latest=latest)


def invoke_and_decode(capsys):
    handoff.main()
    output = capsys.readouterr().out
    rows = output.splitlines()
    expected_hash = next(row.split("=", 1)[1] for row in rows if row.startswith("ICIM_HANDOFF_SHA256="))
    encoded = "".join(row.split("=", 1)[1] for row in rows if row.startswith("ICIM_HANDOFF_CHUNK="))
    raw = base64.b64decode(encoded, validate=True)
    assert hashlib.sha256(raw).hexdigest() == expected_hash
    assert len(raw) == int(next(row.split("=", 1)[1] for row in rows if row.startswith("ICIM_HANDOFF_END=")))
    return output, raw


@pytest.mark.parametrize("mode", ["realtime", "close"])
def test_only_verified_files_export_with_matching_hashes_and_no_mutation(prepared, capsys, mode):
    item = prepared
    if mode == "close":
        item["preflight"].update(publication_mode="close", expected_market_day="2026-10-15")
        item["result"].update(publication_mode="close_confirmed", market_date="2026-10-15",
                              report_file="ic_im_v1_4_close_signal.md")
        write_json(item["artifacts"] / "preflight.json", item["preflight"])
        write_json(item["artifacts"] / "result.json", item["result"])
        (item["artifacts"] / item["result"]["report_file"]).write_text("synthetic close", encoding="utf-8")
    before = {str(p.relative_to(item["root"])): p.read_bytes() for p in item["root"].rglob("*") if p.is_file()}
    output, raw = invoke_and_decode(capsys)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        manifest = json.loads(archive.read("handoff_manifest.json"))
        names = set(archive.namelist())
        expected = {"ledger/latest.json", "ledger/migration_record.json", "ledger/journal/000000-2026-10-14.json",
                    "ledger/journal/000001-2026-10-15.json", "report/result.json", "report/preflight.json",
                    "report/" + item["result"]["report_file"]}
        assert names == expected | {"handoff_manifest.json"}
        assert set(manifest["files"]) == expected
        assert manifest["source_artifact_id"] == 123
        assert manifest["digest"] == item["latest"]["digest"] and manifest["sequence"] == 1
        assert manifest["publication_mode"] == item["result"]["publication_mode"]
        for name, expected_hash in manifest["files"].items():
            data = archive.read(name)
            assert hashlib.sha256(data).hexdigest() == expected_hash
            prefix, relative = name.split("/", 1)
            source = item["state"] if prefix == "ledger" else item["artifacts"]
            assert data == (source / relative).read_bytes()
            assert b"MUST_NOT_EXPORT" not in data
    assert "ENV_MUST_NOT_EXPORT" not in output
    assert set(item["inspected"]) == {0, 1}
    after = {str(p.relative_to(item["root"])): p.read_bytes() for p in item["root"].rglob("*") if p.is_file()}
    assert before == after


@pytest.mark.parametrize("field,value", [("digest", "d" * 64), ("sequence", 0),
                                        ("verified_day", "2026-10-14"), ("market_date", "2026-10-15"),
                                        ("publication_mode", "close_confirmed"), ("report_file", "../private.txt"),
                                        ("report_file", "/etc/environment"), ("report_file", "mail-transport.json"),
                                        ("report_file", "ic_im_v1_4_close_signal.md"), ("status", "partial")])
def test_mismatched_result_cannot_export(prepared, capsys, field, value):
    item = prepared
    item["result"][field] = value
    write_json(item["artifacts"] / "result.json", item["result"])
    with pytest.raises(AssertionError):
        handoff.main()
    assert "ICIM_HANDOFF_CHUNK=" not in capsys.readouterr().out


@pytest.mark.parametrize("field,value", [("strategy_sha", "d" * 40), ("restored_digest", "d" * 64),
                                        ("completed_day", "2026-10-14"), ("expected_market_day", "2026-10-15"),
                                        ("publication_mode", "close")])
def test_mismatched_preflight_cannot_export(prepared, capsys, field, value):
    item = prepared
    item["preflight"][field] = value
    write_json(item["artifacts"] / "preflight.json", item["preflight"])
    with pytest.raises(AssertionError):
        handoff.main()
    assert "ICIM_HANDOFF_CHUNK=" not in capsys.readouterr().out


@pytest.mark.parametrize("name", ["extra.json", "000002-2026-10-16.json"])
def test_unverified_extra_journal_is_rejected(prepared, capsys, name):
    (prepared["state"] / "journal" / name).write_text('{"private":"MUST_NOT_EXPORT"}', encoding="utf-8")
    with pytest.raises(AssertionError):
        handoff.main()
    assert "ICIM_HANDOFF_CHUNK=" not in capsys.readouterr().out


@pytest.mark.parametrize("mutation", ["broken_chain", "wrong_session", "bad_migration", "invalid_anchor"])
def test_full_chain_and_migration_must_validate(prepared, capsys, mutation):
    item = prepared
    journal = item["state"] / "journal" / "000001-2026-10-15.json"
    record = json.loads(journal.read_text(encoding="utf-8"))
    if mutation == "broken_chain": record["previous_digest"] = "d" * 64
    elif mutation == "wrong_session": record["verified_day"] = "2026-10-16"
    elif mutation == "invalid_anchor": record["research_only"] = False
    elif mutation == "bad_migration":
        write_json(item["state"] / "migration_record.json", dict(new_ledger=dict(digest="d" * 64)))
    write_json(journal, record)
    with pytest.raises(AssertionError):
        handoff.main()
    assert "ICIM_HANDOFF_CHUNK=" not in capsys.readouterr().out


def test_missing_source_artifact_and_advanced_ledger_fail_closed(prepared, monkeypatch, capsys):
    arguments = list(sys.argv)
    arguments[arguments.index("--source-artifact") + 1] = "0"
    monkeypatch.setattr(sys, "argv", arguments)
    with pytest.raises(AssertionError): handoff.main()
    arguments[arguments.index("--source-artifact") + 1] = "123"
    latest = dict(prepared["latest"], digest="d" * 64)
    write_json(prepared["state"] / "latest.json", latest)
    with pytest.raises(AssertionError): handoff.main()
    assert "ICIM_HANDOFF_CHUNK=" not in capsys.readouterr().out
