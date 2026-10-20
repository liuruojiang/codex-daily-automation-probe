import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"scripts"))
import etf_content_cards as cards
import etf_source_intake as intake
import etf_automatic_mail_preview as automatic
from test_etf_automatic_mail_preview import two_articles, registry_for

TEXT = ("The authors study a controlled synthetic research environment and compare estimated densities with known simulator outcomes. "
        "They explain that market prices alone cannot establish whether unobserved distributions are accurate. "
        "The study keeps its synthetic and market evaluation targets separate and does not claim general investment performance. ")
URL = "https://arxiv.org/html/2607.12345v3"


def source_fixture():
    parent = cards.make_material(title="Synthetic density research", url="https://arxiv.org/abs/2607.12345", source="synthetic",
                                 published="2026-10-19T04:00:00+00:00", paragraphs=[TEXT*2])
    page = ('<html><body><div>arXiv:2607.12345v3</div><article class="ltx_document">'
            '<h1 class="ltx_title_document">Synthetic density research</h1>'
            '<div class="ltx_para" id="S1.p1"><p>'+TEXT+'</p></div>'
            '<div class="ltx_para" id="S2.p1"><p>'+TEXT+'</p></div></article></body></html>')
    return parent, page.encode()


def audit_fixture():
    inputs = two_articles();registry = registry_for(inputs);registry["entries"] = registry["entries"][:1]
    manifest, history = inputs[2:];targets = intake.targets(manifest, registry)
    t = targets[0]
    row = {"parent_material_id": t["parent_material_id"], "requested_url": t["url"],
           "started_utc": "2026-10-21T00:00:00+00:00", "finished_utc": "2026-10-21T00:00:01+00:00",
           "bytes": 10, "body_sha256": "b"*64, "response_status": 200, "status": "outline_only",
           "reason_zh": "仅有节目提纲。", "next_action_zh": "需取得可读的节目正文。"}
    audit = {"version": intake.VERSION, "snapshot": automatic.snapshot_identity(manifest, history),
             "registry_sha256": cards.digest(registry), "targets": targets, "items": [row], "supplements": [],
             "delivery_eligible": False, "mail_approvals_added": 0, "model_requests": 0}
    return audit, manifest, history, registry


class SourceIntakeTests(unittest.TestCase):
    def test_new_source_preserves_date_and_never_claims_complete_paper(self):
        parent, raw = source_fixture();before = copy.deepcopy(parent)
        material, record = intake.extract_arxiv(parent, raw, URL, ["S1.p1", "S2.p1"])
        self.assertEqual(parent, before)
        self.assertEqual(material["published"], parent["published"])
        self.assertNotEqual(material["material_id"], parent["material_id"])
        self.assertEqual(len(material["paragraphs"]), 2)
        self.assertFalse(record["complete_paper_claim"])
        self.assertFalse(record["tables_and_figures_extracted"])
        self.assertEqual(material["evidence_level"], "captured_excerpt")

    def test_nested_paragraphs_and_math_keep_complete_text_once(self):
        parent, raw = source_fixture()
        raw = raw.replace(b'<p>'+TEXT.encode()+b'</p></div>', b'<p>'+TEXT.encode()+b'</p><div class="ltx_para" id="child"><p>Nested evidence <math alttext="x^2"><mi>DROP_MATHML_RENDERING</mi></math> remains.</p></div></div>', 1)
        material, _ = intake.extract_arxiv(parent, raw, URL, ["S1.p1", "S2.p1"])
        text = material["paragraphs"][0]["text"]
        self.assertEqual(text.count("Nested evidence"), 1)
        self.assertIn("$x^2$", text)
        self.assertNotIn("DROP_MATHML_RENDERING", text)

    def test_wrong_title_version_or_paper_cannot_inherit_identity(self):
        parent, raw = source_fixture()
        for candidate, url in ((raw.replace(b"Synthetic density research", b"Other source"), URL),
                               (raw.replace(b"12345v3", b"12345v2"), URL),
                               (raw, URL.replace("12345", "99999")),
                               (raw.replace(b'</body>', b'<article class="ltx_document"></article></body>'), URL)):
            with self.subTest(url=url), self.assertRaises(ValueError):intake.extract_arxiv(parent, candidate, url, ["S1.p1"])

    def test_explicit_paragraph_selection_cannot_be_missing_duplicate_or_reordered(self):
        parent, raw = source_fixture()
        for ids in ([], ["absent"], ["S1.p1", "S1.p1"], ["S2.p1", "S1.p1"]):
            with self.subTest(ids=ids), self.assertRaises(ValueError):intake.extract_arxiv(parent, raw, URL, ids)

    def test_malformed_html_missing_math_and_duplicate_ids_fail_closed(self):
        parent, raw = source_fixture()
        for value in (raw.replace(b'</article>', b''), raw.replace(b'</p>', b'<math><mi>x</mi></math></p>', 1),
                      raw.replace(b'id="S2.p1"', b'id="S1.p1"')):
            with self.subTest(), self.assertRaises(ValueError):intake.extract_arxiv(parent, value, URL, ["S1.p1"])

    def test_directory_leads_get_independent_unknown_dates_and_no_approval(self):
        parent = cards.make_material(title="Directory", url="https://example.org/list", source="synthetic", published="2026-10-19", paragraphs=[TEXT])
        raw = b'<meta property="og:url" content="https://example.org/list"><a class="qo-title" href="javascript:ignored">Outside</a><div id="qo-mashup"><div><a class="qo-title" href="https://research.example.org/paper">Independent paper</a></div></div>'
        leads = intake.extract_leads(parent, raw)
        self.assertEqual(len(leads), 1)
        self.assertIsNone(leads[0]["publication_date"])
        self.assertEqual(leads[0]["date_status"], "unverified")
        self.assertFalse(leads[0]["delivery_eligible"])

    def test_unsafe_leads_or_wrong_directory_identity_are_rejected(self):
        parent = cards.make_material(title="Directory", url="https://example.org/list", source="synthetic", published="", paragraphs=[TEXT])
        for url in ("javascript:alert(1)", "http://research.example.org/paper", "https://user:secret@research.example.org/paper"):
            raw = ('<meta property="og:url" content="https://example.org/list"><div id="qo-mashup"><a class="qo-title" href="'+url+'">Paper</a></div>').encode()
            with self.subTest(url=url), self.assertRaises(ValueError):intake.extract_leads(parent, raw)
        with self.assertRaisesRegex(ValueError, "identity"):
            intake.extract_leads(parent, b'<meta property="og:url" content="https://example.org/other"><div id="qo-mashup"></div>')

    def test_matched_audit_is_read_only(self):
        values = audit_fixture();before = copy.deepcopy(values)
        intake.validate_audit(*values)
        self.assertEqual(values, before)

    def test_capture_registry_dates_coverage_and_approval_faults_are_refused(self):
        faults = [lambda a,r:a["snapshot"].update(body_sha256="changed"), lambda a,r:r["entries"][0].update(url="https://example.org/changed"),
                  lambda a,r:a["items"][0].update(started_utc="2026-01-01T00:00:00+00:00"),
                  lambda a,r:a["items"][0].update(response_status=403), lambda a,r:a["items"].append(copy.deepcopy(a["items"][0])),
                  lambda a,r:a.update(mail_approvals_added=1), lambda a,r:a.update(delivery_eligible=True)]
        for fault in faults:
            values = audit_fixture();fault(values[0], values[3])
            with self.subTest(), self.assertRaises(ValueError):intake.validate_audit(*values)

    def test_supplement_must_be_new_pending_and_held_from_frozen_mail(self):
        for change in ({"material_id": "existing"}, {"review_status": "approved"}, {"frozen_mail_admission": "allowed"}):
            values = audit_fixture();audit, manifest, _, _ = values
            audit["items"][0]["status"] = "body_supplement_pending"
            supplement = {"parent_material_id": audit["targets"][0]["parent_material_id"], "material_id": "new-material",
                          "review_status": "pending", "delivery_eligible": False, "frozen_mail_admission": "held_later_capture"}
            if change.get("material_id") == "existing":change = {"material_id": cards.prepare_manifest(manifest)[0]["material_id"]}
            supplement.update(change);audit["supplements"] = [supplement]
            with self.subTest(change=change), self.assertRaises(ValueError):intake.validate_audit(*values)


if __name__ == "__main__":unittest.main()
