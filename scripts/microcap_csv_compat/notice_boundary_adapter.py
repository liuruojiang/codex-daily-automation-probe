"""Exact reviewed non-ST late addition; preserves all original source gates."""
import hashlib
import importlib
import importlib.abc
import importlib.machinery
import json
from pathlib import Path
import sys

CERT_SHA = "79e957bb3dcfd5b09927da67f917ae1a6d03ae5478baeff28ec85cbe5014f318"
ADDED_ID = "AN202611011831295457"

def digest(raw):
    return hashlib.sha256(raw).hexdigest()

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")

def wrap_comparator(module):
    original = module._boundary_precision_drift
    def compare(previous, current):
        if previous.keys() == current.keys():
            return original(previous, current)
        if set(previous) - set(current) or set(current) - set(previous) != {ADDED_ID}:
            return original(previous, current)  # Original fail-closed rejection.
        raw = Path(__file__).with_name("exact_non_st_addition_certificate.json").read_bytes()
        if digest(raw) != CERT_SHA:
            raise module.StNoticeEvidenceError("Reviewed non-ST addition certificate changed")
        cert = json.loads(raw)
        row = current[ADDED_ID]
        stable = {k: v for k, v in row.items() if k != "display_time"}
        if digest(canonical(stable)) != cert["native_without_display_time_sha256"]:
            raise module.StNoticeEvidenceError("Reviewed non-ST addition native row changed")
        # Reuse exact existing subsecond/day/15:00 boundary checks for this row.
        original({ADDED_ID: cert["native_row"]}, {ADDED_ID: row})
        scope = importlib.import_module("scripts.st_history_scope")
        base = importlib.import_module("microcap_top100_mom16_biweekly_live_v2_0")
        if (digest(Path(scope.__file__).read_bytes()) != cert["selector_source_sha256"]
                or digest(Path(base.__file__).read_bytes()) != cert["classifier_source_sha256"]):
            raise module.StNoticeEvidenceError("Reviewed non-ST classification source changed")
        if (scope._daily_notice_row(row, "601611", cert["boundary_day"], {}) is not None
                or base.freq_mod.classify_st_notice_risk_actions(row["title"]) != {}):
            raise module.StNoticeEvidenceError("Reviewed addition is not non-ST")
        common = {k: v for k, v in current.items() if k != ADDED_ID}
        result = original(previous, common)
        precision_count = result["changed_event_count"]
        changed = sorted([k for k in previous if previous[k] != common[k]] + [ADDED_ID])
        result.update(policy="exact_reviewed_non_st_addition_plus_original_precision_v1",
            precision_changed_event_count=precision_count,
            changed_event_count=len(changed),
            changed_event_ids_sha256=digest(json.dumps(changed, separators=(",", ":")).encode()),
            reviewed_non_st_added_ids=[ADDED_ID], removed_event_ids=[],
            certificate_sha256=CERT_SHA, adapter_canonical_lf_sha256=digest(Path(__file__).read_bytes().replace(b"\r\n", b"\n")),
            added_native_row_sha256=digest(canonical(row)),
            boundary_native_content_unchanged=False)
        return result
    module._boundary_precision_drift = compare

class _Loader:
    def __init__(self, original):
        self.original = original
    def create_module(self, spec):
        return self.original.create_module(spec)
    def exec_module(self, module):
        self.original.exec_module(module)
        wrap_comparator(module)
    def __getattr__(self, name):
        return getattr(self.original, name)

class _Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname != "scripts.st_notice_refresh":
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path, target)
        if spec is not None:
            spec.loader = _Loader(spec.loader)
        return spec

def install():
    sys.meta_path.insert(0, _Finder())
