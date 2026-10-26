import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import etf_fresh_shadow as fresh
import etf_content_cards as cards
import etf_automatic_mail_preview as auto
from test_etf_automatic_mail_preview import two_articles, registry_for


def fixture():
    inputs = two_articles();inputs[2]['run_id'] = '123';registry = registry_for(inputs)
    old = copy.deepcopy(registry);old['snapshot']['cutoff_utc'] = '2026-01-01T00:00:00+00:00'
    old['snapshot']['run_id'] = 'old';old['entries'][0]['material_id'] = '0' * 64
    index = {'synthetic': True}
    pin = {'repository': 'example/project', 'repository_id': 7, 'branch': 'main', 'run_id': int(inputs[2]['run_id']),
           'run_attempt': 1, 'head_sha': inputs[2]['head_sha'], 'workflow_path': '.github/workflows/daily.yml',
           'event': 'schedule', 'artifact_id': 8, 'artifact_name': 'snapshot', 'artifact_sha256': 'a' * 64}
    report, _ = auto.build(*inputs[1:], registry)
    record = {'version': fresh.VERSION, 'source': pin, 'registry': registry, 'old_registry_sha256': cards.digest(old),
              'old_index_sha256': cards.digest(index), 'protocol': {'selection': [e['material_id'] for e in registry['entries']]},
              'initial_model_usage': {'requests': 4}, 'delivery_eligible': False,
              'expected': {k: report[k] for k in ('selected_count', 'replaced_count', 'retained_count', 'original_content_audit')}}
    return inputs, record, old, index


def provenance(pin):
    repo = {'id': pin['repository_id'], 'full_name': pin['repository']}
    run = {'id': pin['run_id'], 'run_attempt': 1, 'path': pin['workflow_path'], 'head_sha': pin['head_sha'],
           'head_branch': 'main', 'event': 'schedule', 'status': 'completed', 'conclusion': 'success',
           'repository': repo, 'head_repository': copy.deepcopy(repo)}
    artifact = {'id': pin['artifact_id'], 'name': pin['artifact_name'], 'expired': False,
                'digest': 'sha256:' + pin['artifact_sha256'], 'workflow_run': {'id': pin['run_id'],
                'head_sha': pin['head_sha'], 'head_branch': 'main', 'repository_id': 7, 'head_repository_id': 7}}
    return run, artifact


class FreshShadowTests(unittest.TestCase):
    def test_exact_new_snapshot_replays_offline_and_old_same_url_is_held(self):
        inputs, record, old, index = fixture();before = copy.deepcopy((inputs, record, old, index))
        with patch.object(cards, 'call_json', side_effect=AssertionError('no model')), patch.object(fresh.urllib.request, 'urlopen', side_effect=AssertionError('no network')):
            report, page = fresh.build(*inputs[1:], record, old, index)
        self.assertEqual((inputs, record, old, index), before)
        self.assertEqual(report['coverage']['replaced_count'], 2)
        self.assertEqual(report['actual_old_registry']['replaced_count'], 0)
        self.assertEqual(report['synthetic_same_url_old_snapshot']['reason_counts']['registry_snapshot_mismatch'], 1)
        self.assertEqual(report['synthetic_rebound_old_material']['reason_counts']['stale_material'], 1)
        self.assertEqual(report['replay_model_calls'], 0);self.assertFalse(report['sent'])
        self.assertIn('</html>', page)

    def test_mismatched_snapshot_historical_digest_or_sample_is_fatal(self):
        for kind in ('registry', 'index', 'source', 'selection', 'duplicate', 'older'):
            inputs, record, old, index = fixture()
            if kind == 'registry':old['entries'].pop()
            elif kind == 'index':index['changed'] = True
            elif kind == 'source':record['source']['head_sha'] = 'b' * 40
            elif kind == 'selection':record['protocol']['selection'].reverse()
            elif kind == 'duplicate':record['registry']['entries'].append(copy.deepcopy(record['registry']['entries'][0]))
            else:
                old['snapshot']['cutoff_utc'] = inputs[2]['cutoff_utc'];record['old_registry_sha256'] = cards.digest(old)
            with self.subTest(kind=kind), self.assertRaises(ValueError):fresh.build(*inputs[1:], record, old, index)

    def test_source_bytes_verified_before_json_replay(self):
        inputs, record, _, _ = fixture()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder);record['input_sha256'] = {}
            for name, value in zip(fresh.CORE, inputs[1:]):
                raw = json.dumps(value).encode();(root / name).write_bytes(raw)
                record['input_sha256'][name] = hashlib.sha256(raw).hexdigest()
            self.assertEqual(fresh.read_source(root, record), list(inputs[1:]))
            (root / fresh.CORE[0]).write_text('tampered', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'source bytes changed'):fresh.read_source(root, record)

    def test_incomplete_editor_or_rejected_model_prevents_acceptance_proof(self):
        for kind in ('missing_editor', 'wrong_editor', 'model_rejected', 'date_conflict'):
            inputs, record, old, index = fixture();entry = record['registry']['entries'][0]
            if kind == 'missing_editor':entry['editor'] = None
            elif kind == 'wrong_editor':entry['editor']['card_hash'] = 'stale'
            elif kind == 'date_conflict':entry['annotation']['date_status'] = 'conflicting'
            else:
                entry['review']['overall'] = 'revise';entry['review']['checks'][-1]['verdict'] = 'needs_revision'
                entry['editor']['review_hash'] = cards.digest(entry['review'])
            with self.subTest(kind=kind), self.assertRaises(ValueError):fresh.build(*inputs[1:], record, old, index)

    def test_trusted_completed_main_run_and_exact_artifact(self):
        _, record, _, _ = fixture();pin = record['source'];run, artifact = provenance(pin)
        self.assertTrue(fresh.verify_source(run, artifact, pin, 'example/project', 'main')['verified'])

    def test_wrong_producer_artifact_or_fork_is_rejected(self):
        for target, field, value in [('run','id',9),('run','path','other'),('run','run_attempt',2),
            ('run','event','pull_request'),('run','head_branch','feature'),('run','head_sha','other'),
            ('run','conclusion','failure'),('run','status','in_progress'),('artifact','id',99),
            ('artifact','name','other'),('artifact','expired',True),('artifact','digest','sha256:wrong'),
            ('link','head_repository_id',99),('link','repository_id',99),('link','head_sha','other'),
            ('link','head_branch','feature'),('link','id',99),('fork','id',99),('fork','full_name','fork/project')]:
            _, record, _, _ = fixture();pin = record['source'];run, artifact = provenance(pin)
            obj = {'run': run, 'artifact': artifact, 'link': artifact['workflow_run'], 'fork': run['head_repository']}[target]
            obj[field] = value
            with self.subTest(target=target, field=field), self.assertRaises(ValueError):
                fresh.verify_source(run, artifact, pin, 'example/project', 'main')

    def test_no_metadata_redirect_with_authorization(self):
        with self.assertRaisesRegex(ValueError, 'redirect refused'):
            fresh.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.example')
