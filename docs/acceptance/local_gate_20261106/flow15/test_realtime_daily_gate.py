"""Counterexamples for the launcher contracts, not fabricated market evidence."""
import hashlib
import json
from pathlib import Path
import pytest
import realtime_daily_gate as g


@pytest.mark.parametrize('ref', ['main', 'v1.0', 'a' * 39, 'a' * 41])
def test_workflow_rejects_unpinned_or_malformed_ref(ref):
    with pytest.raises(ValueError):
        g.pin_from_workflow('repository: liuruojiang/microcap\n ref: ' + ref, 'liuruojiang/microcap')


def test_workflow_exact_repository_and_pin():
    pin = 'a' * 40
    assert g.pin_from_workflow('repository: liuruojiang/microcap\n ref: ' + pin, 'liuruojiang/microcap') == pin
    with pytest.raises(ValueError):
        g.pin_from_workflow('repository: wrong/microcap\n ref: ' + pin, 'liuruojiang/microcap')


@pytest.mark.parametrize('second', ['b' * 40, 'main'])
def test_workflow_cannot_hide_a_conflicting_or_unpinned_sibling_checkout(second):
    workflow = 'repository: liuruojiang/microcap\n ref: ' + 'a' * 40
    workflow += '\nrepository: liuruojiang/microcap\n ref: ' + second
    with pytest.raises(ValueError):
        g.pin_from_workflow(workflow, 'liuruojiang/microcap')


def test_reviewed_recovery_alias_must_equal_the_exact_normal_pin():
    pin = 'a' * 40
    expression = '${{ steps.recovery_pin.outputs.sha }}'
    workflow = 'repository: liuruojiang/microcap\n ref: ' + pin
    workflow += '\nrepository: liuruojiang/microcap\n ref: ' + expression
    with pytest.raises(ValueError):
        g.pin_from_workflow(workflow, 'liuruojiang/microcap')
    assert g.pin_from_workflow(workflow, 'liuruojiang/microcap', {expression: pin}) == pin
    with pytest.raises(ValueError):
        g.pin_from_workflow(workflow, 'liuruojiang/microcap', {expression: 'b' * 40})
    with pytest.raises(ValueError):
        g.pin_from_workflow(workflow.replace('recovery_pin', 'unreviewed'), 'liuruojiang/microcap', {expression: pin})


@pytest.mark.parametrize('relative', ['../escape', '..\\escape'])
def test_inventory_escape_rejected(tmp_path, relative):
    with pytest.raises(ValueError):
        g.safe_path(tmp_path, relative)


@pytest.mark.parametrize('operation', ['change', 'add', 'remove'])
def test_snapshot_inventory_mutation_rejected(tmp_path, operation):
    p = tmp_path / 'authority.json'; p.write_text('original')
    expected = g.inventory(tmp_path)
    if operation == 'change': p.write_text('changed')
    if operation == 'add': (tmp_path / 'new').write_text('new')
    if operation == 'remove': p.unlink()
    with pytest.raises(ValueError): g.verify_inventory(tmp_path, expected)


def test_production_and_fixture_outputs_must_be_disjoint(tmp_path):
    roots = {k: str(tmp_path / k) for k in ('microcap_source', 'icim_source', 'automation_source')}
    with pytest.raises(ValueError): g.check_isolation(Path(roots['microcap_source']) / 'test', None, roots)
    with pytest.raises(ValueError): g.check_isolation(tmp_path / 'snapshot', tmp_path / 'snapshot/results', roots)
    g.check_isolation(tmp_path / 'snapshot', tmp_path / 'results', roots)


def setup_manifest(tmp_path):
    outputs = tmp_path / 'outputs'; outputs.mkdir()
    meta = outputs / 'microcap_top100_mom16_biweekly_live_v2_0_base_proxy_meta.json'
    policy = 'delist_zero_writeoff_equal_survivors_v1'
    meta.write_text(json.dumps({'delisting_valuation_policy': policy, 'core_params': {'delisting_valuation_policy': policy}}))
    manifest = {'ok': True, 'status': 'complete', 'scope': 'whole_workspace_delivery', 'expected_date': '2026-11-02',
                'inputs': {meta.relative_to(tmp_path).as_posix(): g.sha(meta)}, 'artifacts': {}}
    (outputs / 'top100_delivery_manifest.json').write_text(json.dumps(manifest))
    return meta, manifest


def test_manifest_byte_mutation_rejected(tmp_path):
    meta, _ = setup_manifest(tmp_path)
    meta.write_text('{}')
    with pytest.raises(ValueError, match='binding mismatch'): g.compatibility(tmp_path)


def test_authentic_but_old_policy_fixture_rejected(tmp_path):
    meta, manifest = setup_manifest(tmp_path)
    meta.write_text('{}')
    manifest['inputs'][meta.relative_to(tmp_path).as_posix()] = g.sha(meta)
    (tmp_path / 'outputs/top100_delivery_manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='incompatible'): g.compatibility(tmp_path)


def test_missing_evidence_rejected_before_tests(tmp_path):
    _, manifest = setup_manifest(tmp_path)
    manifest['inputs']['data/st_notice_evidence/missing.pdf'] = '0' * 64
    (tmp_path / 'outputs/top100_delivery_manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(FileNotFoundError): g.compatibility(tmp_path)


def test_xml_subset_and_skip_not_success(tmp_path):
    p = tmp_path / 'regression.xml'
    p.write_text('<testsuites><testsuite tests="1" failures="0" errors="0" skipped="1"><testcase classname="tests.test_a"><skipped/></testcase></testsuite></testsuites>')
    assert g.junit_counts(p, ['test_a.py'])['skipped'] == 1
    with pytest.raises(ValueError, match='incomplete'): g.junit_counts(p, ['test_a.py', 'test_b.py'])


def test_required_gate_lists_preserved():
    assert len(g.CORE) == 30 and len(g.RELEASE) == 6
    assert 'test_v23_no_overheat_promotion.py' in g.CORE
    assert 'test_whole_delivery_member_cache_gate_20261024.py' in g.RELEASE


def test_compiled_bytecode_rejected_even_when_B_is_used(tmp_path):
    cache = tmp_path / '__pycache__'; cache.mkdir()
    (cache / 'helper.cpython-314.pyc').write_bytes(b'untrusted')
    with pytest.raises(ValueError, match='bytecode'): g.inventory(tmp_path)


def test_junit_actual_failure_cannot_hide_behind_zero_aggregate(tmp_path):
    p = tmp_path / 'regression.xml'
    p.write_text('<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0"><testcase classname="tests.test_a"><failure>bad</failure></testcase></testsuite></testsuites>')
    with pytest.raises(ValueError, match='aggregates'): g.junit_counts(p, ['test_a.py'])


def test_environment_cannot_inject_test_filter_or_production_archive(monkeypatch):
    for key in ('PYTEST_PLUGINS', 'PYTEST_ADDOPTS', 'PYTHONSTARTUP', 'MICROCAP_ST_NOTICE_ARCHIVE', 'MICROCAP_ST_NOTICE_ARCHIVE_SHA256', 'ICIM_STATE_DIR'):
        monkeypatch.setenv(key, 'poison')
    clean = g.clean_test_env()
    assert not any(key in clean for key in ('PYTEST_PLUGINS', 'PYTEST_ADDOPTS', 'PYTHONSTARTUP', 'MICROCAP_ST_NOTICE_ARCHIVE', 'MICROCAP_ST_NOTICE_ARCHIVE_SHA256', 'ICIM_STATE_DIR'))


def test_collection_deselection_rejected(tmp_path):
    p = tmp_path / 'collection.json'
    g.write(p, {'collected': ['test_a.py::pass', 'test_a.py::fail'], 'selected': ['test_a.py::pass'],
                'executed': ['test_a.py::pass'], 'deselected': ['test_a.py::fail']})
    with pytest.raises(ValueError, match='filtered'): g.validate_collection(p, {'tests': 1})


def test_guard_uses_event_kinds_not_arbitrary_error_words(tmp_path):
    p = tmp_path / 'guard.jsonl'
    p.write_text(json.dumps({'kind': 'GUARD_STARTED'}) + '\n' + json.dumps({'kind': 'SUBPROCESS', 'detail': 'assert FULL_REBUILD_BLOCKED'}) + '\n')
    assert g.guard_evidence(p) == (True, [])
    p.write_text(p.read_text() + json.dumps({'kind': 'EXTERNAL_SUBPROCESS_BLOCKED'}) + '\n')
    assert g.guard_evidence(p) == (True, ['EXTERNAL_SUBPROCESS_BLOCKED'])


def test_consumer_json_contract_is_bound(tmp_path):
    for rel in ('scripts/build_microcap_realtime_digest.py', 'scripts/build_ic_im_v1_4_digest.py',
                '.github/workflows/microcap-realtime-digest.yml', '.github/workflows/ic-im-v1-4-daily-digest.yml',
                'config/microcap_approved_state.json', 'docs/ic_im_daily_release_20261023_repairs_v3_r3_manifest.json'):
        p = tmp_path / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_text('' if rel.endswith('.py') else '{}')
    before = g.consumer_bindings(tmp_path)
    p = tmp_path / 'docs/ic_im_daily_release_20261023_repairs_v3_r3_manifest.json'; p.write_text('{"changed":true}')
    assert before != g.consumer_bindings(tmp_path)


def test_workflow_helper_and_its_local_import_cannot_drift_unnoticed(tmp_path):
    for rel in ('scripts/build_microcap_realtime_digest.py', 'scripts/build_ic_im_v1_4_digest.py',
                '.github/workflows/microcap-realtime-digest.yml', '.github/workflows/ic-im-v1-4-daily-digest.yml',
                'config/microcap_approved_state.json', 'docs/ic_im_daily_release_20261023_repairs_v3_r3_manifest.json'):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('' if rel.endswith('.py') else '{}')
    workflow = tmp_path / '.github/workflows/microcap-realtime-digest.yml'
    workflow.write_text('run: python -B automation/scripts/recovery.py restore')
    helper = tmp_path / 'scripts/recovery.py'
    helper.write_text('from scripts.restore_utils import validate\n')
    dependency = tmp_path / 'scripts/restore_utils.py'
    dependency.write_text('def validate(): return True\n')
    before = g.consumer_bindings(tmp_path)
    assert 'scripts/recovery.py' in before and 'scripts/restore_utils.py' in before
    dependency.write_text('def validate(): return False\n')
    assert before != g.consumer_bindings(tmp_path)
    dependency.unlink()
    helper.unlink()
    with pytest.raises(FileNotFoundError):
        g.consumer_bindings(tmp_path)


def test_failed_termination_is_recorded_and_blocks_lock_release(tmp_path, monkeypatch):
    class Child:
        pid = 12345678
        def wait(self, timeout): raise g.subprocess.TimeoutExpired('owned test', timeout)
    monkeypatch.setattr(g.subprocess, 'Popen', lambda *a, **k: Child())
    def fail(*a, **k): raise OSError('termination denied')
    monkeypatch.setattr(g.subprocess, 'run', fail)
    try:
        result = g.subprocess_gate(['owned-test'], tmp_path, {}, tmp_path / 'out', 'test', timeout=.01)
        assert result['exit_code'] == 124 and not result['termination_confirmed']
        assert (tmp_path / 'out/process.json').is_file()
        assert 12345678 in g.UNCONFIRMED_CHILDREN
    finally:
        g.UNCONFIRMED_CHILDREN.clear()


def test_unexpected_wait_error_preserves_unknown_child_state(tmp_path, monkeypatch):
    class Child:
        pid = 12345679
        def wait(self, timeout): raise OSError('wait handle failed')
        def poll(self): return None
    monkeypatch.setattr(g.subprocess, 'Popen', lambda *a, **k: Child())
    def fail(*a, **k): raise OSError('tree termination denied')
    monkeypatch.setattr(g.subprocess, 'run', fail)
    try:
        result = g.subprocess_gate(['owned-test'], tmp_path, {}, tmp_path / 'out', 'test', timeout=.01)
        assert result['exit_code'] == 1 and not result['termination_confirmed']
        assert 'wait handle failed' in result['wait_error']
        assert 12345679 in g.UNCONFIRMED_CHILDREN
    finally:
        g.UNCONFIRMED_CHILDREN.clear()


def test_worker_cannot_inherit_ancestor_pytest_filter_or_conftest(tmp_path):
    root = tmp_path / 'suite'; root.mkdir()
    (tmp_path / 'pytest.ini').write_text('[pytest]\npython_functions=test_pass\n')
    (tmp_path / 'conftest.py').write_text('raise RuntimeError("ancestor conftest must not load")\n')
    (root / 'test_sample.py').write_text('def test_pass(): assert True\ndef test_fail(): assert False\n')
    out = tmp_path / 'results'; out.mkdir()
    env = {**g.clean_test_env(), 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1',
           'HYPOTHESIS_STORAGE_DIRECTORY': str(tmp_path / 'hypothesis'), 'PYTHONDONTWRITEBYTECODE': '1'}
    result = g.subprocess.run([g.sys.executable, '-B', '-X', 'utf8', str(g.HOME / 'scripts/daily_microcap_test_worker.py'),
                               str(out), 'test_sample.py'], cwd=root, env=env, capture_output=True, timeout=25)
    assert result.returncode == 1, result.stdout.decode('utf8', errors='replace')
    counts = g.junit_counts(out / 'regression.xml', ['test_sample.py'])
    assert counts == {'tests': 2, 'failures': 1, 'errors': 0, 'skipped': 0}
    g.validate_collection(out / 'collection.json', counts)


def readiness_launcher_fixture(tmp_path, monkeypatch):
    """Only launcher control flow; these receipts are not real suite evidence."""
    from contextlib import nullcontext
    home = tmp_path / 'home'; (home / 'scripts').mkdir(parents=True)
    for name in ('daily_microcap_test_worker.py', 'daily_microcap_group_worker.py',
                 'daily_pytest.ini', 'daily_test_guard.py', 'microcap_readiness.py', 'microcap_resident_readiness.py'):
        (home / 'scripts' / name).write_text('isolated launcher fixture')
    prepared = tmp_path / 'prepared'; prepared.mkdir()
    (prepared / 'prepared.json').write_text('{}')
    (prepared / 'guard').mkdir()
    (prepared / 'guard/sitecustomize.py').write_bytes((home/'scripts/daily_test_guard.py').read_bytes())
    config = home / 'config.json'
    config.write_text(json.dumps({k:str(tmp_path/k) for k in ('microcap_source','icim_source','automation_source')}))
    active = home / 'active.json'
    monkeypatch.setattr(g, 'HOME', home)
    monkeypatch.setattr(g, 'CONFIG', config)
    monkeypatch.setattr(g, 'ACTIVE', active)
    monkeypatch.setattr(g, 'validate_prepared', lambda target: {'automation_pin':'a'*40, 'strategy_pins':{'microcap':'m'*40,'icim':'i'*40}})
    monkeypatch.setattr(g, 'verify_production_sources', lambda receipt: None)
    monkeypatch.setattr(g, 'check_isolation', lambda *args: None)
    monkeypatch.setattr(g, 'coordinator_lock', lambda: nullcontext())
    def failed_suite(cmd, cwd, env, out, label, **kwargs):
        out.mkdir(parents=True, exist_ok=True)
        return {'exit_code':1, 'pass':False, 'label':label}
    monkeypatch.setattr(g, 'subprocess_gate', failed_suite)
    bindings = {'launcher_sha256':g.sha(Path(g.__file__)),
                'worker_sha256':g.sha(home/'scripts/daily_microcap_test_worker.py'),
                'group_worker_sha256':g.sha(home/'scripts/daily_microcap_group_worker.py'),
                'pytest_config_sha256':g.sha(home/'scripts/daily_pytest.ini'),
                'microcap_readiness_sha256':g.sha(home/'scripts/microcap_readiness.py'),
                'resident_worker_sha256':g.sha(home/'scripts/microcap_resident_readiness.py'),
                'guard_source_sha256':g.sha(home/'scripts/daily_test_guard.py'),
                'config_sha256':g.sha(config),
                'prepared_receipt_sha256':g.sha(prepared/'prepared.json')}
    g.write(active, dict(bindings, prepared=str(prepared)))
    return home, prepared, bindings


def test_gate_requires_readiness_file_before_starting_suites(tmp_path, monkeypatch):
    home, prepared, _ = readiness_launcher_fixture(tmp_path, monkeypatch)
    (home/'scripts/microcap_readiness.py').unlink()
    with pytest.raises(FileNotFoundError):
        g.gate(prepared, tmp_path/'audit', live_identity=False)
    assert not (tmp_path/'audit').exists()


def test_gate_readiness_mutation_cannot_keep_a_certificate(tmp_path, monkeypatch):
    home, prepared, _ = readiness_launcher_fixture(tmp_path, monkeypatch)
    calls = []
    def validate(target):
        calls.append(target)
        if len(calls) == 2:
            (home/'scripts/microcap_readiness.py').write_text('changed during test processes')
        return {'automation_pin':'a'*40, 'strategy_pins':{'microcap':'m'*40,'icim':'i'*40}}
    monkeypatch.setattr(g, 'validate_prepared', validate)
    with pytest.raises(ValueError, match='readiness/config/guard changed'):
        g.gate(prepared, tmp_path/'audit', live_identity=False)
    assert not (tmp_path/'audit/gate.json').exists()


def test_plain_gate_has_no_daily_calendar_or_intraday_release(tmp_path, monkeypatch):
    home, prepared, bindings = readiness_launcher_fixture(tmp_path, monkeypatch)
    result = g.gate(prepared, tmp_path/'audit', live_identity=False)
    assert result['microcap_readiness_sha256'] == bindings['microcap_readiness_sha256']
    assert result['guard_source_sha256'] == bindings['guard_source_sha256']
    assert result['config_sha256'] == bindings['config_sha256']
    assert result['calendar'] is None and result['release_for_intraday'] is False


@pytest.mark.parametrize('missing', [True, False])
def test_activation_rejects_missing_or_changed_readiness_binding(tmp_path, monkeypatch, missing):
    home, prepared, bindings = readiness_launcher_fixture(tmp_path, monkeypatch)
    audit = tmp_path/'audit'; audit.mkdir()
    acceptance = dict(bindings, **{'pass':True})
    if missing:
        acceptance.pop('microcap_readiness_sha256')
    else:
        (home/'scripts/microcap_readiness.py').write_text('changed after acceptance')
    g.write(audit/'gate.json', acceptance)
    monkeypatch.setattr(g.sys, 'argv', ['gate', 'activate', '--prepared', str(prepared), '--out', str(audit)])
    old_active = g.ACTIVE.read_bytes()
    with pytest.raises(ValueError, match='activation requires'):
        g.main()
    assert g.ACTIVE.read_bytes() == old_active


def daily_calendar_fixture(monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    class Fixed(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 3, 14, 35, tzinfo=ZoneInfo('Asia/Shanghai'))
    monkeypatch.setattr(g, 'datetime', Fixed)
    calendar = {'checked_at':'2026-11-03T14:30:00+08:00', 'today':'2026-11-03',
                'is_trading_day':True, 'latest_completed_session':'2026-11-02', 'next_session':'2026-11-04'}
    monkeypatch.setattr(g, 'independent_calendar', lambda root: dict(calendar))
    return calendar


def test_daily_passes_actual_calendar_to_gate_and_releases_only_today(tmp_path, monkeypatch):
    _, prepared, _ = readiness_launcher_fixture(tmp_path, monkeypatch)
    calendar = daily_calendar_fixture(monkeypatch)
    observed = []
    def isolated_gate(target, out, **kwargs):
        observed.append(kwargs['calendar'])
        out.mkdir()
        return {'schema':1, 'pass':True, 'calendar':kwargs['calendar'], 'release_for_intraday':False}
    monkeypatch.setattr(g, 'gate', isolated_gate)
    audit = tmp_path/'daily'
    monkeypatch.setattr(g.sys, 'argv', ['gate', 'daily', '--out', str(audit)])
    assert g.main() == 0
    result = json.loads((audit/'gate.json').read_bytes())
    assert observed == [calendar] and result['calendar'] == calendar
    assert result['release_for_intraday'] is True


@pytest.mark.parametrize('missing', [True, False])
def test_daily_rejects_missing_or_changed_readiness_before_suite(tmp_path, monkeypatch, missing):
    home, prepared, _ = readiness_launcher_fixture(tmp_path, monkeypatch)
    daily_calendar_fixture(monkeypatch)
    if missing:
        active = json.loads(g.ACTIVE.read_bytes()); active.pop('microcap_readiness_sha256');g.write(g.ACTIVE, active)
    else:
        (home/'scripts/microcap_readiness.py').write_text('changed after activation')
    def forbidden(*args, **kwargs):
        pytest.fail('daily gate must not start with changed readiness helper')
    monkeypatch.setattr(g, 'gate', forbidden)
    monkeypatch.setattr(g.sys, 'argv', ['gate', 'daily', '--out', str(tmp_path/'daily')])
    with pytest.raises(ValueError, match='microcap_readiness_sha256'):
        g.main()


@pytest.mark.parametrize('kind', ['guard', 'config'])
def test_gate_rejects_guard_or_config_mutation_before_certifying(tmp_path, monkeypatch, kind):
    home, prepared, _ = readiness_launcher_fixture(tmp_path, monkeypatch)
    calls = []
    def validate(target):
        calls.append(target)
        if len(calls) == 2:
            path = home/'scripts/daily_test_guard.py' if kind == 'guard' else g.CONFIG
            path.write_bytes(path.read_bytes()+b' ')
        return {'automation_pin':'a'*40, 'strategy_pins':{'microcap':'m'*40,'icim':'i'*40}}
    monkeypatch.setattr(g, 'validate_prepared', validate)
    with pytest.raises(ValueError, match='config/guard changed'):
        g.gate(prepared, tmp_path/'audit', live_identity=False)
    assert not (tmp_path/'audit/gate.json').exists()


def test_prepared_guard_must_equal_the_guard_being_certified(tmp_path, monkeypatch):
    home, prepared, _ = readiness_launcher_fixture(tmp_path, monkeypatch)
    (home/'scripts/daily_test_guard.py').write_text('new local guard, old prepared guard')
    with pytest.raises(ValueError, match='prepared test guard differs'):
        g.gate(prepared, tmp_path/'audit', live_identity=False)
    assert not (tmp_path/'audit').exists()


def test_authenticated_notice_audit_fixture_closure_is_retained():
    for part in ('manifest.json', '2026-11-03/page_0001.json', '2026-11-03/page_0001.receipt.json'):
        assert g.selected_micro_fixture('.microcap_index_cache/realtime_notice_audits/owned/' + part)
    assert not g.selected_micro_fixture('.microcap_index_cache/st_notice_archive_pointer.json')
    assert not g.selected_micro_fixture('.microcap_index_cache/unreviewed_folder/random.json')


@pytest.mark.parametrize('kind', ['guard', 'config'])
@pytest.mark.parametrize('missing', [True, False])
def test_activation_rejects_guard_or_config_changes_since_gate(tmp_path, monkeypatch, kind, missing):
    home, prepared, bindings = readiness_launcher_fixture(tmp_path, monkeypatch)
    audit=tmp_path/'audit';audit.mkdir()
    acceptance=dict(bindings, **{'pass':True})
    key='guard_source_sha256' if kind=='guard' else 'config_sha256'
    if missing:
        acceptance.pop(key)
    else:
        path=home/'scripts/daily_test_guard.py' if kind=='guard' else g.CONFIG
        path.write_bytes(path.read_bytes()+b' ')
    g.write(audit/'gate.json',acceptance)
    monkeypatch.setattr(g.sys,'argv',['gate','activate','--prepared',str(prepared),'--out',str(audit)])
    before=g.ACTIVE.read_bytes()
    with pytest.raises(ValueError,match='activation requires'):g.main()
    assert g.ACTIVE.read_bytes()==before


@pytest.fixture
def production_binding_case(tmp_path,monkeypatch):
    """Isolated source-declaration fixture, not a strategy/data certificate."""
    target=tmp_path/'prepared';micro=target/'microcap';micro.mkdir(parents=True)
    production=tmp_path/'production';production.mkdir()
    names=['data/share_source_future_plan_reviews.json','data/st_notice_evidence/notice.json',
        'data/st_notice_boundary_reviews/review.json','data/share_source_future_plan_reviews_other.json',
        'data/st_notice_evidence/share_source_future_plan_reviews.json','config/other.json',
        'outputs/microcap_top100_mom16_biweekly_live_v2_0_base_frozen_tail_authority.json']
    original=b'{\n  "date": "2026-11-04",\n  "quantity": 284835200\n}\n'
    for name in names:
        for root in (micro,production):
            path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(original)
    icim=target/'icim/docs';icim.mkdir(parents=True)
    (icim/'ic_im_daily_release_20261023_repairs_v3_r3_manifest.json').write_text('{"files":{}}')
    monkeypatch.setattr(g,'run',lambda *a,**k:json.dumps(names))
    bindings=g.production_bindings(target)
    config=tmp_path/'config.json';config.write_text(json.dumps({'microcap_source':str(production),'icim_source':str(tmp_path/'unused')}))
    monkeypatch.setattr(g,'CONFIG',config)
    return production,bindings,original


def test_only_registered_share_review_json_accepts_crlf_source_transport(production_binding_case):
    production,bindings,original=production_binding_case
    name='data/share_source_future_plan_reviews.json'
    assert bindings['microcap'][name]=={'sha256':hashlib.sha256(original).hexdigest(),'lf':True}
    (production/name).write_bytes(original.replace(b'\n',b'\r\n'))
    g.verify_production_sources({'production_bindings':bindings})


@pytest.mark.parametrize('change',['value','date','space'])
def test_registered_share_review_json_real_change_still_rejected(production_binding_case,change):
    production,bindings,original=production_binding_case
    changed={'value':original.replace(b'284835200',b'284835201'),
        'date':original.replace(b'2026-11-04',b'2026-11-03'),
        'space':original.replace(b'"quantity":',b'"quantity" :')}[change]
    (production/'data/share_source_future_plan_reviews.json').write_bytes(changed)
    with pytest.raises(ValueError,match='production source/authority differs'):
        g.verify_production_sources({'production_bindings':bindings})


@pytest.mark.parametrize('name',['data/st_notice_evidence/notice.json',
    'data/st_notice_boundary_reviews/review.json','data/share_source_future_plan_reviews_other.json',
    'data/st_notice_evidence/share_source_future_plan_reviews.json','config/other.json',
    'outputs/microcap_top100_mom16_biweekly_live_v2_0_base_frozen_tail_authority.json'])
def test_other_raw_json_and_authority_crlf_changes_still_rejected(production_binding_case,name):
    production,bindings,original=production_binding_case
    assert bindings['microcap'][name]['lf'] is False
    (production/name).write_bytes(original.replace(b'\n',b'\r\n'))
    with pytest.raises(ValueError,match='production source/authority differs'):
        g.verify_production_sources({'production_bindings':bindings})


def scheduled_gate_fixture(tmp_path, monkeypatch, *, unknown=None, share_fault=None):
    """Simulated child reports test launcher control flow, not strategy success."""
    import xml.etree.ElementTree as ET
    home, prepared, _ = readiness_launcher_fixture(tmp_path, monkeypatch)
    calls = []
    def child(cmd, cwd, env, out, label, timeout=180):
        out.mkdir(parents=True, exist_ok=False)
        calls.append(dict(label=label, command=cmd, cwd=cwd, env=env, out=out, timeout=timeout))
        if label == 'icim47':
            g.write(out.parent/'icim_results/receipt.json',dict(source_before_equals_after=True,exit_code=0,
                counts=dict(tests=1,failures=0,errors=0,skipped=0)))
        else:
            # The worker is argv[4]; output argv[5], tests begin argv[6].
            files = [Path(name).name for name in cmd[6:]]
            nodes=[name+'::test_isolated_control_flow' for name in files]
            skip = label=='share_source2' and share_fault=='skip'
            suite=ET.Element('testsuite',tests=str(len(files)),failures='0',errors='0',skipped='1' if skip else '0')
            for i,name in enumerate(files):
                case=ET.SubElement(suite,'testcase',classname='tests.'+Path(name).stem,name='test_isolated_control_flow')
                if skip and i==0:ET.SubElement(case,'skipped')
            ET.ElementTree(suite).write(out/'regression.xml',encoding='utf8')
            selection=dict(collected=nodes,selected=nodes,executed=nodes,deselected=[])
            if label=='share_source2' and share_fault=='deselected':selection['deselected']=[nodes[-1]]
            g.write(out/'collection.json',selection)
            events=[dict(kind='GUARD_STARTED')]
            if label=='share_source2' and share_fault=='network':events.append(dict(kind='NETWORK_BLOCKED'))
            Path(env['MICROCAP_OFFLINE_AUDIT_LOG']).write_text(''.join(json.dumps(x)+'\n' for x in events))
        if unknown==label:g.UNCONFIRMED_CHILDREN.append(987654321)
        return dict(label=label,exit_code=124 if unknown==label else 0,pass_=False)
    monkeypatch.setattr(g,'subprocess_gate',child)
    return prepared,calls


def test_share_source_group_runs_last_with_same_guard_and_approved_300_budget(tmp_path,monkeypatch):
    prepared,calls=scheduled_gate_fixture(tmp_path,monkeypatch)
    result=g.gate(prepared,tmp_path/'audit',live_identity=False)
    assert result['pass'] is True
    assert [c['label'] for c in calls]==['icim47','core30','release6','share_source2']
    assert [r['label'] for r in result['microcap']]==['core30','release6','share_source2']
    assert g.SHARE_SOURCE==['test_share_change_source.py','test_share_unchanged_tail_20261103.py']
    for call,files in zip(calls[1:],[g.CORE,g.RELEASE,g.SHARE_SOURCE]):
        assert call['timeout']==300
        assert call['command'][6:]==['tests/'+name for name in files]
        assert Path(call['command'][4]).name==('daily_microcap_group_worker.py' if call['label']=='core30' else 'daily_microcap_test_worker.py')
        assert call['env']['PYTHONDONTWRITEBYTECODE']=='1' and call['env']['PYTEST_DISABLE_PLUGIN_AUTOLOAD']=='1'
        assert call['env']['TOP100_REALTIME_REQUIRE_STATE']=='1'
        assert call['env']['PYTHONPATH'].split(g.os.pathsep)==[str(prepared/'guard'),str(prepared/'microcap')]
        assert call['env']['TEMP']==call['env']['TMP']==str(tmp_path/'audit'/(call['label']+'_temp'))
        assert call['env']['MICROCAP_DIGEST_SOURCE']==str(prepared/'consumer/scripts/build_microcap_realtime_digest.py')
    assert len({call['env']['TEMP'] for call in calls[1:]})==3


@pytest.mark.parametrize('unknown',['icim47','core30','release6','share_source2'])
def test_no_later_group_starts_after_unconfirmed_child_in_any_group(tmp_path,monkeypatch,unknown):
    prepared,calls=scheduled_gate_fixture(tmp_path,monkeypatch,unknown=unknown)
    try:
        result=g.gate(prepared,tmp_path/'audit',live_identity=False)
        order=['icim47','core30','release6','share_source2']
        assert [c['label'] for c in calls]==order[:order.index(unknown)+1]
        assert result['pass'] is False
        if unknown in ('core30','release6'):
            pending=order[order.index(unknown)+1:]
            assert [r['label'] for r in result['microcap'] if r.get('status')=='not_started_due_to_unconfirmed_child']==pending
    finally:g.UNCONFIRMED_CHILDREN.clear()


@pytest.mark.parametrize('fault',['skip','deselected','network'])
def test_share_source_group_cannot_bypass_zero_skip_full_collection_or_offline_guard(tmp_path,monkeypatch,fault):
    prepared,calls=scheduled_gate_fixture(tmp_path,monkeypatch,share_fault=fault)
    result=g.gate(prepared,tmp_path/'audit',live_identity=False)
    assert [c['label'] for c in calls][-1]=='share_source2'
    assert result['pass'] is False
    assert result['microcap'][-1]['label']=='share_source2' and result['microcap'][-1]['pass'] is False


@pytest.mark.parametrize('missing',['test_share_change_source.py','test_share_unchanged_tail_20261103.py',None])
def test_prepare_checks_both_share_tests_before_later_preparation_or_any_pytest(tmp_path,monkeypatch,missing):
    import zipfile
    config=dict(automation_source=str(tmp_path/'automation'),microcap_source=str(tmp_path/'production_micro'),
                icim_source=str(tmp_path/'production_icim'),microcap_archive=str(tmp_path/'frozen.zip'))
    with zipfile.ZipFile(config['microcap_archive'],'w'):pass
    config['microcap_archive_sha256']=g.sha(Path(config['microcap_archive']))
    def git_read(cmd,**kw):
        if cmd[-1]=='HEAD':return 'a'*40
        if cmd[-1].endswith(':config/microcap_approved_state.json'):return json.dumps(dict(strategy_sha='b'*40))
        if cmd[-1].endswith('microcap-realtime-digest.yml'):return 'repository: liuruojiang/microcap\n ref: '+'b'*40
        if cmd[-1].endswith('ic-im-v1-4-daily-digest.yml'):return 'repository: liuruojiang/ic-im-rolling-arbitrage\n ref: '+'c'*40
        pytest.fail('unexpected launcher read')
    monkeypatch.setattr(g,'run',git_read)
    class LaterPreparation(Exception):pass
    def checkout(repo,pin,destination,archive):
        if destination.name=='icim':raise LaterPreparation('all required Microcap files were present')
        destination.mkdir(parents=True)
        setup_manifest(destination)
        for name in g.CORE+g.RELEASE+g.SHARE_SOURCE:
            if name!=missing:
                path=destination/'tests'/name;path.parent.mkdir(exist_ok=True);path.write_text('# isolated existence control only\n')
    monkeypatch.setattr(g,'archive_checkout',checkout)
    monkeypatch.setattr(g,'subprocess_gate',lambda *a,**k:pytest.fail('no pytest may start during dependency precheck'))
    if missing is None:
        with pytest.raises(LaterPreparation):g.prepare(config,tmp_path/'prepared')
    else:
        with pytest.raises(ValueError,match='missing daily tests') as exc:g.prepare(config,tmp_path/'prepared')
        assert missing in str(exc.value)


def test_gate_requires_resident_worker_file_before_starting_suites(tmp_path,monkeypatch):
    home,prepared,_=readiness_launcher_fixture(tmp_path,monkeypatch)
    (home/'scripts/microcap_resident_readiness.py').unlink()
    with pytest.raises(FileNotFoundError):g.gate(prepared,tmp_path/'audit',live_identity=False)
    assert not (tmp_path/'audit').exists()


def test_gate_resident_worker_mutation_cannot_keep_a_certificate(tmp_path,monkeypatch):
    home,prepared,_=readiness_launcher_fixture(tmp_path,monkeypatch);calls=[]
    def validate(target):
        calls.append(target)
        if len(calls)==2:(home/'scripts/microcap_resident_readiness.py').write_text('changed during test processes')
        return {'automation_pin':'a'*40,'strategy_pins':{'microcap':'m'*40,'icim':'i'*40}}
    monkeypatch.setattr(g,'validate_prepared',validate)
    with pytest.raises(ValueError,match='resident worker changed during tests'):g.gate(prepared,tmp_path/'audit',live_identity=False)
    assert not (tmp_path/'audit/gate.json').exists()


@pytest.mark.parametrize('missing',[True,False])
def test_activation_rejects_missing_or_changed_resident_worker_binding(tmp_path,monkeypatch,missing):
    home,prepared,bindings=readiness_launcher_fixture(tmp_path,monkeypatch)
    audit=tmp_path/'audit';audit.mkdir();acceptance=dict(bindings,**{'pass':True})
    if missing:acceptance.pop('resident_worker_sha256')
    else:(home/'scripts/microcap_resident_readiness.py').write_text('changed after gate acceptance')
    g.write(audit/'gate.json',acceptance);before=g.ACTIVE.read_bytes()
    monkeypatch.setattr(g.sys,'argv',['gate','activate','--prepared',str(prepared),'--out',str(audit)])
    with pytest.raises(ValueError,match='activation requires'):g.main()
    assert g.ACTIVE.read_bytes()==before


@pytest.mark.parametrize('missing',[True,False])
def test_daily_rejects_old_active_or_changed_resident_before_suites(tmp_path,monkeypatch,missing):
    home,prepared,_=readiness_launcher_fixture(tmp_path,monkeypatch);daily_calendar_fixture(monkeypatch)
    if missing:
        active=json.loads(g.ACTIVE.read_bytes());active.pop('resident_worker_sha256');g.write(g.ACTIVE,active)
    else:(home/'scripts/microcap_resident_readiness.py').write_text('changed resident after activation')
    monkeypatch.setattr(g,'gate',lambda *a,**k:pytest.fail('old active must not launch suites'))
    monkeypatch.setattr(g.sys,'argv',['gate','daily','--out',str(tmp_path/'daily')])
    with pytest.raises(ValueError,match='resident_worker_sha256'):g.main()


def test_unchanged_resident_hash_carried_gate_to_active(tmp_path,monkeypatch):
    home,prepared,bindings=readiness_launcher_fixture(tmp_path,monkeypatch)
    result=g.gate(prepared,tmp_path/'audit',live_identity=False)
    assert result['resident_worker_sha256']==bindings['resident_worker_sha256']
    # The isolated mocked child suites fail; successful certificate is used only to test activation control flow.
    acceptance=dict(bindings,**{'pass':True});g.write(tmp_path/'audit/gate.json',acceptance)
    monkeypatch.setattr(g.sys,'argv',['gate','activate','--prepared',str(prepared),'--out',str(tmp_path/'audit')])
    assert g.main()==0
    assert json.loads(g.ACTIVE.read_bytes())['resident_worker_sha256']==bindings['resident_worker_sha256']


@pytest.mark.parametrize('fault',[None,'data','source','approval','seed','date'])
@pytest.mark.parametrize('transition_kind',['301288','603028','603028_market'])
def test_exact_301288_frozen_fixture_transition(tmp_path,fault,transition_kind):
    root=tmp_path/'microcap';(root/'outputs').mkdir(parents=True)
    names={'scripts/share_unchanged_tail.py','data/share_source_future_plan_reviews.json',
           'scripts/realtime_state_bundle.py','scripts/sse_share_daily_unchanged_20261104.py'}
    if transition_kind.startswith('603028'): names |= {'scripts/st_notice_boundary_review.py','scripts/share_change_source.py'}
    if transition_kind=='603028_market': names.add('microcap_top100_mom16_biweekly_live_v2_0.py')
    old=hashlib.sha256(b'old').hexdigest();new=hashlib.sha256(b'new').hexdigest()
    for name in names:
        p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'new')
    data=root/'outputs/economic.csv';data.write_bytes(b'original')
    policy='delist_zero_writeoff_equal_survivors_v1'
    meta={'delisting_valuation_policy':policy,'core_params':{'delisting_valuation_policy':policy}}
    g.write(root/'outputs/microcap_top100_mom16_biweekly_live_v2_0_base_proxy_meta.json',meta)
    manifest={'ok':True,'status':'complete','scope':'whole_workspace_delivery','expected_date':'2026-11-04',
        'release_sha':'6fa0b9d73b12fff548200d987c953d1b1a7d74d7','inputs':{p:old for p in names},
        'artifacts':{'outputs/economic.csv':g.sha(data)}}
    g.write(root/'outputs/top100_delivery_manifest.json',manifest)
    approved={'state_date':'2026-11-04','state_sha256':'6155ca0cc8ca94481cf41ce92af3f6d337549c5543a95dd9eb6e903d38944f7b',
        'approved_source_successor':{'bootstrap_sha':manifest['release_sha'],
            'approval_package_sha256':'3af53c7fea86b3334321112981747560e9fcea9dba5ee7667c9f011d8a70f4ff',
            'changes':{p:[old,new] for p in names}}}
    if transition_kind.startswith('603028'): approved['approved_source_successor']['approval_package_sha256']='7fe387b7a1fbcd8548fff0533852c6421d7b3c59502cee361d0da86734b18dad'
    if fault=='data':data.write_bytes(b'changed')
    if fault=='source':(root/'scripts/share_unchanged_tail.py').write_bytes(b'unreviewed')
    if fault=='approval':approved['approved_source_successor']['approval_package_sha256']='0'*64
    if fault=='seed':approved['state_sha256']='0'*64
    if fault=='date':approved['state_date']='2026-11-05'
    g.write(tmp_path/'preparation_tools/microcap_approved_state.json',approved)
    before=(root/'outputs/top100_delivery_manifest.json').read_bytes()
    if fault:
        with pytest.raises(ValueError):g.compatibility(root)
    else:assert g.compatibility(root)==manifest
    assert (root/'outputs/top100_delivery_manifest.json').read_bytes()==before


@pytest.mark.parametrize('fault',[None,'inventory','runtime','helper','supplemental','archive'])
def test_exact_completed_icim_reuse_keeps_all_dependencies(fault,monkeypatch):
    import copy
    review=g.ICIM_UNCHANGED_REUSE_REVIEW
    source=Path(review['gate']);previous=g._read_json(source)
    receipt=copy.deepcopy(g._read_json(Path(review['prepared'])))
    bindings={key:previous[key] for key in ('worker_sha256','pytest_config_sha256','guard_source_sha256','config_sha256','runtime')}
    if fault=='inventory':receipt['inventories']['icim']['not-real.py']='0'*64
    if fault=='runtime':receipt['strategy_pins']['icim']='0'*40
    if fault=='helper':bindings['worker_sha256']='0'*64
    if fault=='supplemental':receipt['icim_supplemental_bindings']={}
    if fault=='archive':monkeypatch.setitem(review,'gate_sha256','0'*64)
    if fault:
        with pytest.raises(ValueError):g.validate_unchanged_icim_reuse(source,receipt,bindings)
    else:
        reused,evidence=g.validate_unchanged_icim_reuse(source,receipt,bindings)
        assert reused['icim']['pass'] and reused['icim_supplemental5']['pass']
        assert evidence['gate_sha256']==review['gate_sha256']
