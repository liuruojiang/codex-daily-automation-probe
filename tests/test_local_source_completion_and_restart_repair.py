"""Real orchestration with software-only source I/O; no market collection."""
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import maintain_microcap_local as local
import microcap_local_due as due
import microcap_local_workers as workers
import microcap_scoped_inputs as transport

TARGET = '2026-11-11'
SYMBOLS = [f'{i:06d}' for i in range(1, 301)]


def write(path, value):
    local.atomic(path, local.encode(value))


def fake_native(monkeypatch, root):
    package = ModuleType('scripts')
    package.__path__ = []
    monkeypatch.setitem(sys.modules, 'scripts', package)
    def module(name, **fields):
        value = ModuleType(name)
        value.__dict__.update(fields)
        monkeypatch.setitem(sys.modules, name, value)
    freq = SimpleNamespace(ROOT=root, load_security_master=lambda: object(),
        list_backtest_universe_symbols=lambda: SYMBOLS,
        _read_security_meta_payload=lambda p, s: json.loads(p.read_bytes()),
        _notice_horizon_covers=lambda *_: True)
    module('microcap_top100_mom16_biweekly_live_v2_0', base_mod=SimpleNamespace(freq_mod=freq,
        fetch_mod=object(), fetch_tencent_realtime_quotes=lambda *_: None))
    module('scripts.stock_daily_inputs', refresh_daily_inputs=lambda **_: None)
    def share_cache(**kwargs):
        name=kwargs['symbol']+'.json'
        raw=local.bound(kwargs['root'],local.CACHE+'share_sources/'+name).read_bytes()
        return dict(source_files={name:local.sha(raw)})
    module('scripts.share_change_source', refresh_share_change=lambda **_: None, verify_share_cache=share_cache)
    module('scripts.stock_return_pipeline', load_return_prices=lambda **_: None)
    module('scripts.stock_input_contract', source_fingerprint=lambda _: {})
    module('scripts.exchange_calendar', latest_completed_session=lambda: date.fromisoformat(TARGET), is_trading_day=lambda _: True)
    fresh = local.CACHE + 'st_notice_sources/freshness.json'
    module('scripts.st_notice_refresh', FRESHNESS=fresh, freshness_source_files=lambda _: [fresh], refresh_notice_archive=lambda **_: None)
    module('scripts.st_history_scope', source_closure=lambda _: {}, resolve_archive_reference=lambda _: ('fixture', 'authority'))
    local.atomic(root / 'software-source-fixture', b'previously verified software-only source')
    for key in ('price_proof', 'share_proof', 'ratio_proof', 'current_names_proof'):
        monkeypatch.setattr(local, key, lambda *a, **k: local.hashes(root, ['software-source-fixture']))
    monkeypatch.setattr(local, 'price_seed_anchor', lambda *_: TARGET)


def run_inline(root, state, symbols, context):
    directory = state / 'runs' / context['target']
    assignment = directory / 'worker-context.json'
    write(assignment, dict(context=context, symbols=symbols, dispatch_id=uuid.uuid4().hex))
    assert [workers.worker(root, state, slot, assignment) for slot in (0, 1)] == [0, 0]
    reports = [json.loads((directory / f'worker-{slot}.json').read_bytes()) for slot in (0, 1)]
    return dict(status='complete', target=TARGET, scope_sha256=context['scope_sha256'], total=300,
        processed=sum(r['processed'] for r in reports), complete=sum(r['complete'] for r in reports),
        failures={s:f for r in reports for s,f in r['failures'].items()}, reused_stages=sum(r['reused_stages'] for r in reports))


def test_partial_bootstrap_does_not_abandon_sources_or_certify_299_metadata(tmp_path, monkeypatch):
    state = tmp_path / 'state'
    root = state / 'runtime'
    root.mkdir(parents=True)
    fake_native(monkeypatch, root)
    monkeypatch.setattr(local, 'validate_runtime', lambda *_: local.RUNTIME)
    floor = datetime.now(timezone.utc) - timedelta(hours=1)
    monkeypatch.setattr(local, 'observation_floor', lambda: floor)
    write(local.bound(root, local.CACHE + 'st_notice_sources/freshness.json'), dict(target=TARGET, required_asof=floor.isoformat()))
    local.atomic(local.bound(root, local.CACHE + 'security_master.csv'), b'software master')
    local.atomic(local.bound(root, local.CACHE + 'current_st.csv'), b'software ST snapshot')
    write(local.bound(root,local.CACHE+'st_notice_archive_pointer.json'), {'fixture':True})
    for symbol in SYMBOLS[:-1]:
        write(local.bound(root, local.CACHE + 'security_meta/' + symbol + '.json'),
            dict(notice_source_complete=True,st_notice_archive_sha256='authority'))
    protected=tmp_path/'protected'
    protected.mkdir()
    reference=b'date,microcap\n2026-11-10,1\n'
    local.atomic(state/'reference-proxy.csv',reference)
    write(state / 'bootstrap.json', dict(status='partial', symbols=SYMBOLS,
        initialized_symbols=SYMBOLS[:-1], failures={SYMBOLS[-1]: {'error': 'incomplete original source'}},
        runtime_sha=local.RUNTIME,source_root=str(protected),files={},reference_consumed_through='2026-11-10',
        reference_proxy_sha256=local.sha(reference)))
    candidates = {'candidates': [dict(symbol=s, current_member=i < 100) for i, s in enumerate(SYMBOLS)]}
    run = tmp_path / 'screen'
    write(run / 'candidates.json', candidates)
    pool = tmp_path / 'pool.json'
    write(pool, dict(schema='microcap_local_screen_preparation_v1', status='PREPARATION_VERIFIED', date=TARGET,
        run_directory=str(run), candidates_sha256=local.sha((run / 'candidates.json').read_bytes())))
    monkeypatch.setattr(workers, 'run_workers', run_inline)
    code = local.main(['run', '--root', str(root), '--state-root', str(state), '--pool', str(pool),
        '--protected-root', str(tmp_path / 'protected'), '--as-of', TARGET])
    result = json.loads((state / 'runs' / TARGET / 'result.json').read_bytes())
    assert code == 1 and result['status'] == 'incomplete'
    assert result['processed'] == result['complete'] == 300  # independent source work survived
    assert result['bootstrap_status'] == 'incomplete' and result['missing_initialized_symbols'] == [SYMBOLS[-1]]
    assert result['metadata_status'] == 'failed'
    assert not due.completed(state / 'runs' / TARGET, root, TARGET)
    with pytest.raises(ValueError, match='complete hash-verified'):
        transport.export(root, state, TARGET, tmp_path / 'not-published.zip')
    assert not (tmp_path / 'not-published.zip').exists()
    # The ACTIVE 09:00 task already invokes this idempotent bootstrap route.
    # A still-missing original fails locally; successful issuer stages survive.
    before=(root/'software-source-fixture').read_bytes()
    missing=local.bootstrap(root,protected,SYMBOLS,state)
    assert missing['status']=='partial' and list(missing['failures'])==[SYMBOLS[-1]]
    symbol=SYMBOLS[-1]
    raw=b'date,close_raw\n2026-11-10,10\n'
    adjusted=b'date,close_adj\n2026-11-10,10\n'
    objects={
        local.CACHE+'prices_raw/'+symbol+'.csv':raw,
        local.CACHE+'share_change/'+symbol+'.csv':b'change_date,total_shares_10k\n2026-11-10,1\n',
        local.CACHE+'security_meta/'+symbol+'.json':local.encode(dict(notice_source_complete=True,st_notice_archive_sha256='authority')),
        local.CACHE+'trade_status/'+symbol+'.json':b'{}',
        local.CACHE+'share_sources/'+symbol+'.json':local.encode(dict(query_end='2026-11-10')),
        local.CACHE+'return_basis/prices_adj/'+symbol+'.csv':adjusted,
    }
    manifest=dict(candidate_sha256=local.sha(adjusted),raw_sha256=local.sha(raw))
    objects[local.CACHE+'return_basis/manifests/'+symbol+'.json']=local.encode(manifest)
    identity=local.sha(json.dumps(manifest,sort_keys=True).encode())
    objects[local.CACHE+'return_basis/bundles/'+symbol+'/'+identity+'/raw.csv']=raw
    objects.update({local.CACHE+'return_basis/sources/'+symbol+'/'+name:b'software factor source'
                    for name in ('qfq.js','hfq.js','qfq.json','hfq.json')})
    for name,body in objects.items():local.atomic(local.bound(protected,name),body)
    restored=local.bootstrap(root,protected,SYMBOLS,state)
    assert restored['status']=='complete' and len(restored['initialized_symbols'])==300
    assert (root/'software-source-fixture').read_bytes()==before
    assert list((state/'bootstrap-revisions').glob('*.json'))
    code=local.main(['run','--root',str(root),'--state-root',str(state),'--pool',str(pool),
        '--protected-root',str(protected),'--as-of',TARGET])
    final=json.loads((state/'runs'/TARGET/'result.json').read_bytes())
    assert code==0 and final['status']=='complete' and final['reused_stages']==900
    assert due.completed(state/'runs'/TARGET,root,TARGET)


def completed_fixture(state):
    root = state / 'runtime'
    root.mkdir(parents=True)
    directory = state / 'runs' / TARGET
    directory.mkdir(parents=True)
    context = dict(runtime_sha=local.RUNTIME, native_sources={}, target=TARGET, scope_sha256='a'*64)
    local.atomic(root / 'verified-source', b'software source')
    stage = dict(status='complete', context=context, files=local.hashes(root, ['verified-source']))
    for symbol in SYMBOLS:
        for kind in ('price', 'share', 'ratio'):
            write(directory / (symbol + '-' + kind + '.json'), stage)
    floor = datetime.fromisoformat('2026-11-12T09:00:00+08:00')
    for name, value in [(local.CACHE+'st_notice_sources/freshness.json', {'required_asof':floor.isoformat()}),
        (local.CACHE+'local_current_names/'+TARGET+'.json', {'observed_at':floor.isoformat()})]:
        write(local.bound(root, name), value)
    for name in ('notice', 'current-names'):
        write(directory / (name+'.json'), stage)
    metadata = []
    for symbol in SYMBOLS:
        name = local.CACHE+'security_meta/'+symbol+'.json'
        local.atomic(local.bound(root,name), b'software metadata')
        metadata.append(name)
    write(directory/'metadata.json', dict(stage, files=local.hashes(root,metadata)))
    write(directory/'result.json', dict(status='complete', target=TARGET, total=300, complete=300,
        failures={}, notice_status='complete', current_names_status='complete', metadata_status='complete',
        context=context, symbols=SYMBOLS, scope_sha256=context['scope_sha256']))
    write(state/'bootstrap.json', dict(status='complete', symbols=SYMBOLS, initialized_symbols=SYMBOLS, failures={}))
    return root, directory, floor


@pytest.mark.parametrize('fault', ['missing', 'same_count_wrong_symbol'])
def test_903_count_cannot_hide_missing_or_different_metadata_symbol(tmp_path, fault):
    root, directory, _ = completed_fixture(tmp_path / 'state')
    assert due.completed(directory, root, TARGET)
    path = directory/'metadata.json'
    metadata = json.loads(path.read_bytes())
    metadata['files'].pop(local.CACHE+'security_meta/000300.json')
    if fault == 'same_count_wrong_symbol':
        name = local.CACHE+'security_meta/000301.json'
        local.atomic(local.bound(root,name), b'other issuer')
        metadata['files'][name] = local.sha(b'other issuer')
    write(path, metadata)
    assert not due.completed(directory, root, TARGET)
    with pytest.raises(ValueError, match='complete hash-verified'):
        transport.export(root, tmp_path/'state', TARGET, tmp_path/'not-published.zip')


def test_due_and_export_bind_the_latest_weekly_roster(tmp_path, monkeypatch):
    state = tmp_path/'state'
    root, directory, floor = completed_fixture(state)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return (floor+timedelta(hours=3)).astimezone(tz or timezone.utc)
    monkeypatch.setattr(due, 'datetime', Clock)
    sessions = [date(2026,11,11), date(2026,11,12)]
    assert due.plan(floor+timedelta(hours=2),sessions,state,root)['status'] == 'already_complete'
    screen = state/'screens'/TARGET
    symbols = SYMBOLS[:-1]+['000301']
    write(screen/'candidates.json', {'candidates':[dict(symbol=s,current_member=i<100) for i,s in enumerate(symbols)]})
    write(screen/'latest.json', dict(schema='microcap_local_screen_preparation_v1',status='PREPARATION_VERIFIED',
        date=TARGET,run_directory=str(screen),candidates_sha256=local.sha((screen/'candidates.json').read_bytes())))
    assert due.plan(floor+timedelta(hours=2),sessions,state,root)['status'] == 'due'
    with pytest.raises(ValueError,match='complete hash-verified'):
        transport.export(root,state,TARGET,tmp_path/'wrong-scope.zip')
    assert not (tmp_path/'wrong-scope.zip').exists()


def test_old_parent_stop_request_is_preserved_but_does_not_poison_new_dispatch(tmp_path, monkeypatch):
    state = tmp_path/'state'
    root = state/'runtime'
    root.mkdir(parents=True)
    fake_native(monkeypatch,root)
    write(state/'bootstrap.json', {'initialized_symbols':[]})
    context = dict(target=TARGET,scope_sha256='a'*64,orchestrator_sources={},native_sources={})
    directory = state/'runs'/TARGET
    directory.mkdir(parents=True)
    old_legacy = directory/'stop-after-stage.json'
    write(old_legacy, {'context_sha256':local.sha(local.encode(context))})
    class Alive:
        returncode = None
        def poll(self):return self.returncode
    child = Alive()
    count = []
    def failed_launch(*args,**kwargs):
        count.append(True)
        if len(count)==1:return child
        raise OSError('software-only second-launch failure')
    with monkeypatch.context() as patch:
        patch.setattr(workers.subprocess,'Popen',failed_launch)
        with pytest.raises(OSError,match='second-launch'):
            workers.run_workers(root,state,SYMBOLS,context)
    assignment = directory/'worker-context.json'
    old_id = json.loads(assignment.read_bytes())['dispatch_id']
    request = directory/('stop-after-stage-'+old_id+'.json')
    old_bytes, legacy_bytes = request.read_bytes(),old_legacy.read_bytes()
    assert [workers.worker(root,state,slot,assignment) for slot in (0,1)] == [2,2]
    child.returncode = 0
    class Done:
        returncode = 0
        def poll(self):return self.returncode
    def completed_launch(args,**kwargs):
        assert workers.worker(root,state,int(args[-2]),Path(args[-1])) == 0
        return Done()
    monkeypatch.setattr(workers.subprocess,'Popen',completed_launch)
    progress = workers.run_workers(root,state,SYMBOLS,context)
    assert progress['processed'] == progress['complete'] == 300
    assert progress['dispatch_id'] != old_id
    assert request.read_bytes() == old_bytes and old_legacy.read_bytes() == legacy_bytes
