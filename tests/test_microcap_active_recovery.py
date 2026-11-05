import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('active_recovery', ROOT / 'scripts/verify_microcap_active_recovery.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def install(root, **changes):
    receipt = dict(schema='microcap_recovery_readiness_v1', **{'pass': True},
        strategy_sha='a' * 40, seed_date='2026-11-04', bundle_sha256='b' * 64,
        repository='liuruojiang/codex-daily-automation-probe', fallback_used_by_consumer=False,
        recovery_only=True, current_market_authorized=False, email_delivery_proven=False,
        producer_run=1, consumer_run=2, source_artifact=3, bundle_bytes=100)
    receipt.update(changes)
    raw = json.dumps(receipt).encode()
    (root / 'config').mkdir()
    (root / '.github/workflows').mkdir(parents=True)
    (root / 'config/microcap_recovery_readiness.json').write_bytes(raw)
    config = dict(strategy_sha='a' * 40, state_date='2026-11-04', state_sha256='b' * 64,
                  recovery_readiness={'path': 'config/microcap_recovery_readiness.json', 'sha256': hashlib.sha256(raw).hexdigest()})
    (root / 'config/microcap_approved_state.json').write_text(json.dumps(config))
    (root / '.github/workflows/microcap-realtime-digest.yml').write_text('repository: liuruojiang/microcap\n  ref: ' + 'a' * 40)


@pytest.mark.parametrize('change', [{'pass': False}, {'pass': 1}, {'strategy_sha': 'c' * 40},
    {'seed_date': '2026-11-05'}, {'bundle_sha256': 'c' * 64}, {'producer_run': 2},
    {'source_artifact': None}, {'fallback_used_by_consumer': True}, {'recovery_only': False},
    {'email_delivery_proven': True}, {'repository': 'other/repo'}])
def test_changed_or_unverified_release_cannot_reuse_receipt(tmp_path, change):
    install(tmp_path, **change)
    with pytest.raises(ValueError):
        module.verify(tmp_path)


def test_bound_evidence_does_not_claim_current_freshness(tmp_path):
    install(tmp_path)
    assert module.verify(tmp_path)['current_session_verified'] is False
    (tmp_path / 'config/microcap_recovery_readiness.json').write_bytes(b'{}')
    with pytest.raises(ValueError, match='bytes changed'):
        module.verify(tmp_path)


def test_active_release_has_actual_bound_recovery_acceptance():
    assert module.verify(ROOT)['pass'] is True
    jobs = yaml.safe_load((ROOT / '.github/workflows/microcap-realtime-digest.yml').read_text(encoding='utf-8'))['jobs']
    names = [item.get('name') for item in jobs['send']['steps']]
    assert names.index('Verify accepted production recovery identity') < names.index('Check delivery marker')
    assert 'verify_microcap_active_recovery.py' not in json.dumps(jobs['recovery-check'])


def test_release_download_url_is_not_an_additional_checkout(tmp_path):
    install(tmp_path)
    path = tmp_path / '.github/workflows/microcap-realtime-digest.yml'
    path.write_text(path.read_text() + '\nCACHE_URL: https://github.com/liuruojiang/microcap/releases/download/cache/cache.zip\n')
    assert module.verify(tmp_path)['pass'] is True


@pytest.mark.parametrize('repo_quote', ['', "'", '\"'])
@pytest.mark.parametrize('ref', ["'" + 'c' * 40 + "'", '\"' + 'c' * 40 + '\"', '${{ inputs.unreviewed }}', 'main'])
def test_every_checkout_ref_is_checked_including_quoted_yaml(tmp_path, repo_quote, ref):
    install(tmp_path)
    path = tmp_path / '.github/workflows/microcap-realtime-digest.yml'
    path.write_text(path.read_text() + '\nrepository: ' + repo_quote + 'liuruojiang/microcap' + repo_quote + '\n  ref: ' + ref)
    with pytest.raises(ValueError, match='workflow differs'):
        module.verify(tmp_path)


def test_missing_ref_cannot_hide_beside_an_accepted_checkout(tmp_path):
    install(tmp_path)
    path = tmp_path / '.github/workflows/microcap-realtime-digest.yml'
    path.write_text(path.read_text() + '\nrepository: liuruojiang/microcap\n  path: missing-ref')
    with pytest.raises(ValueError, match='workflow differs'):
        module.verify(tmp_path)


@pytest.mark.parametrize('extra', ["\nrepository: liuruojiang/microcap # another checkout\n  ref: main", "\nwith: {repository: liuruojiang/microcap, ref: main}", "\nrepository: other-owner/microcap\n  ref: " + 'c' * 40, "\nwith: {repository: other-owner/microcap, ref: main}"])
def test_other_yaml_forms_cannot_hide_unreviewed_checkout(tmp_path, extra):
    install(tmp_path)
    path = tmp_path / '.github/workflows/microcap-realtime-digest.yml'
    path.write_text(path.read_text() + extra)
    with pytest.raises(ValueError, match='workflow differs'):
        module.verify(tmp_path)


def test_exact_source_successor_keeps_original_pair_identity(tmp_path):
    install(tmp_path,strategy_sha='6fa0b9d73b12fff548200d987c953d1b1a7d74d7')
    from scripts.restore_approved_microcap_state import EXACT_301288_PATHS, EXACT_301288_PACKAGE
    path=tmp_path/'config/microcap_approved_state.json';c=json.loads(path.read_bytes())
    c['approved_source_successor']={'bootstrap_sha':'6fa0b9d73b12fff548200d987c953d1b1a7d74d7',
        'approval_package_sha256':EXACT_301288_PACKAGE,'changes':{p:['a'*64,'b'*64] for p in EXACT_301288_PATHS}}
    path.write_text(json.dumps(c))
    result=module.verify(tmp_path)
    assert result['pass'] and not result['current_session_verified']
    assert result['accepted_restore_runtime']=='6fa0b9d73b12fff548200d987c953d1b1a7d74d7'
    assert result['strategy_sha']=='a'*40
    c['approved_source_successor']['approval_package_sha256']='0'*64;path.write_text(json.dumps(c))
    with pytest.raises(ValueError):module.verify(tmp_path)
