import copy
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from advisory import build_advisory, render_advisory_text
from monitor import METHOD_VERSION, render_html, render_text
from restore_previous import restore, verified_report
from test_advisory import sample
from weekly_view import monthly_update


class WeeklyViewTests(unittest.TestCase):
    def test_fast_changes_precede_monthly_inputs_in_both_formats(self):
        report = sample()
        old = copy.deepcopy(report)
        old["generated_at_utc"] = "2026-10-12T06:00:00+00:00"
        old["supplemental"]["sp500_price_momentum"].update(as_of="2026-10-09", momentum_1m=.01,
                                                               realized_vol_20d_annualized=.1)
        report["supplemental"]["sp500_price_momentum"]["realized_vol_20d_annualized"] = .2
        report["advisory"] = build_advisory(report, old)
        for render in (render_text, render_html):
            text = render(report)
            self.assertLess(text.index("本周涨跌"), text.index("月度慢指标"))
            self.assertIn("较上期-0.72个百分点", text)
            self.assertIn("较上期+10.00个百分点", text)
            self.assertIn("本期未更新", text)

    def test_new_month_same_month_revision_and_missing_baseline_are_distinct(self):
        now = sample()["core"]["cape_boundary_10y"]
        self.assertIn("上期缺失", monthly_update(now, {}))
        old = dict(now, as_of="2026-08")
        self.assertIn("参考月变化", monthly_update(now, old))
        old = dict(now, valuation_ma12=38)
        self.assertIn("同月读数修订", monthly_update(now, old))
        self.assertIn("本期未更新", monthly_update(now, now))

    def test_same_market_date_cannot_claim_weekly_delta(self):
        report = sample()
        text = render_advisory_text(build_advisory(report, copy.deepcopy(report)))
        self.assertIn("上期不可比或缺失", text)
        self.assertNotIn("较上期+0.00个百分点", text)


class DurableSnapshotTests(unittest.TestCase):
    def archive(self, report=None, delivered=True, corrupt=False):
        report = report or sample()
        report["method_version"] = METHOD_VERSION
        body = render_text(report)
        html = render_html(report)
        meta = {"subject": "每周股票动量边界监控", "body": body, "html_body": html}
        meta_bytes = json.dumps(meta).encode()
        receipt = {"status": "smtp_accepted", "message_id": "<verified@example.com>",
                   "accepted_at": report["generated_at_utc"],
                   "metadata_sha256": hashlib.sha256(meta_bytes).hexdigest(),
                   "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
                   "subject_sha256": hashlib.sha256(meta["subject"].encode()).hexdigest()}
        if corrupt:
            receipt["body_sha256"] = "wrong"
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as archive:
            for name, value in (("report.json", json.dumps(report)), ("metadata.json", meta_bytes),
                                ("report.txt", body), ("report.html", html)):
                archive.writestr(name, value)
            if delivered:
                archive.writestr("delivery-receipt.json", json.dumps(receipt))
        return out.getvalue()

    def test_weekly_cache_expiry_has_no_effect_and_only_delivered_report_is_restored(self):
        now = datetime(2026, 10, 26, 6, 30, tzinfo=timezone.utc)
        runs = [{"id": i, "created_at": "2026-10-19T05:59:00Z", "event": "workflow_dispatch",
                 "head_branch": "main", "path": ".github/workflows/equity-momentum-boundary-weekly.yml",
                 "html_url": f"https://github.com/example/repo/actions/runs/{i}"} for i in (2, 1)]
        def api(endpoint, binary=False):
            if "/workflows/" in endpoint:
                return {"workflow_runs": runs}
            if "/runs/" in endpoint:
                i = int(endpoint.split("/runs/")[1].split("/")[0])
                return {"artifacts": [{"id": i, "name": f"equity-momentum-boundary-{i}", "expired": False}]}
            return self.archive(delivered="/artifacts/1/" in endpoint)
        with tempfile.TemporaryDirectory() as folder, patch("restore_previous.api", side_effect=api):
            result = restore("example/repo", Path(folder), now, METHOD_VERSION)
            self.assertEqual(result["status"], "restored")
            self.assertEqual(result["run_id"], 1)
            self.assertEqual(len(result["attempts"]), 1)
            self.assertTrue((Path(folder) / "report.json").exists())

    def test_preview_bad_receipt_future_stale_same_week_and_method_change_are_rejected(self):
        now = datetime(2026, 10, 26, 6, 30, tzinfo=timezone.utc)
        cases = [self.archive(delivered=False), self.archive(corrupt=True)]
        for generated in (now, now + timedelta(days=1), now - timedelta(days=15)):
            report = sample()
            report["generated_at_utc"] = generated.isoformat()
            cases.append(self.archive(report))
        for payload in cases:
            with self.assertRaises((ValueError, KeyError)):
                verified_report(payload, now, METHOD_VERSION)
        with self.assertRaises(ValueError):
            verified_report(self.archive(), now, "changed-method")

    def test_read_failure_records_reason_without_breaking_current_report(self):
        with tempfile.TemporaryDirectory() as folder, patch("restore_previous.api", side_effect=OSError):
            result = restore("example/repo", Path(folder), datetime.now(timezone.utc), METHOD_VERSION)
            self.assertEqual(result["status"], "unavailable")
            self.assertFalse((Path(folder) / "report.json").exists())
            self.assertTrue((Path(folder) / "restore.json").exists())

    def test_malformed_snapshot_dates_and_components_are_rejected(self):
        now = datetime(2026, 10, 26, 6, 30, tzinfo=timezone.utc)
        for change in ({"generated_at_utc": None}, {"core": {"cape_boundary_10y": None}}, {"sources": [None]}):
            report = sample()
            report.update(change)
            # Build the damaged archive independently of the current renderer.
            with zipfile.ZipFile(io.BytesIO(self.archive())) as source:
                entries = {name: source.read(name) for name in source.namelist()}
            report["method_version"] = METHOD_VERSION
            entries["report.json"] = json.dumps(report).encode()
            out = io.BytesIO()
            with zipfile.ZipFile(out, "w") as archive:
                for name, data in entries.items():
                    archive.writestr(name, data)
            with self.assertRaises(ValueError):
                verified_report(out.getvalue(), now, METHOD_VERSION)


if __name__ == "__main__":
    unittest.main()
