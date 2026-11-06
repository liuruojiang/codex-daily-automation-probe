import importlib.util
import json
from datetime import datetime
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).parents[1] / 'scripts/run_local_intraday.py'
spec = importlib.util.spec_from_file_location('intraday_orchestrator_test', SCRIPT)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


@pytest.mark.parametrize('stamp', ['2026-11-05T14:14:59+08:00', '2026-11-05T15:00:00+08:00',
                                 '2026-11-06T14:20:00+08:00'])
def test_cutoff_and_cross_day_reject(stamp):
    with pytest.raises(ValueError):
        m.require_window('2026-11-05', datetime.fromisoformat(stamp))


def test_window_uses_beijing_and_accepts_exact_start():
    m.require_window('2026-11-05', datetime.fromisoformat('2026-11-05T06:15:00+00:00'))


def test_unknown_claim_cannot_be_reentered(tmp_path):
    m.claim_day(tmp_path, '2026-11-05', tmp_path/'one')
    with pytest.raises(FileExistsError):
        m.claim_day(tmp_path, '2026-11-05', tmp_path/'two')


@pytest.mark.parametrize('child', ['', 'outputs/new'])
def test_output_never_overlaps_production(tmp_path, child):
    prod = tmp_path/'production'
    prod.mkdir()
    with pytest.raises(ValueError):
        m.isolated_output(prod/child, [prod])


def test_output_never_overwrites_existing_receipts(tmp_path):
    existing = tmp_path/'old'
    existing.mkdir()
    (existing/'run.json').write_text('original')
    with pytest.raises(FileExistsError):
        m.isolated_output(existing, [tmp_path/'production'])
    assert (existing/'run.json').read_text() == 'original'


def bare_run(tmp_path):
    r = object.__new__(m.Run)
    r.out = tmp_path
    r.day = '2026-11-05'
    r.claim = tmp_path/'claim.json'
    return r


def test_one_failed_strategy_never_masks_other_success(tmp_path):
    r = bare_run(tmp_path)
    calls = []
    def fail():
        calls.append('microcap')
        raise ValueError('source unavailable')
    def success():
        calls.append('icim')
        return {'actual_publication': False, 'summary': 'validated'}
    r.run_icim, r.run_microcap = success, fail
    assert r.execute() == 1
    receipt = m.read(tmp_path/'run.json')
    assert receipt['results']['icim']['status'] == 'ready_not_published'
    assert receipt['results']['microcap']['status'] == 'failed_or_unknown'
    assert receipt['actual_publication'] is False
    assert sorted(calls) == ['icim', 'microcap']


def test_components_run_independently_not_serially(tmp_path):
    r = bare_run(tmp_path)
    barrier = threading.Barrier(2)
    def work():
        barrier.wait(timeout=5)
        return {'actual_publication': False}
    r.run_icim = r.run_microcap = work
    assert r.execute() == 0


def test_icim_selection_never_invokes_old_microcap_pipeline(tmp_path):
    r = bare_run(tmp_path)
    r.strategy = 'icim'
    r.run_icim = lambda: {'actual_publication': False}
    def forbidden():
        raise AssertionError('Old microcap path must not run after member-daily cutover')
    r.run_microcap = forbidden
    assert r.execute() == 0
    assert set(m.read(tmp_path/'run.json')['results']) == {'icim'}


def test_child_nonzero_recorded_no_retry_and_no_shell(tmp_path, monkeypatch):
    r = bare_run(tmp_path)
    monkeypatch.setattr(m, 'require_window', lambda *a: None)
    command = [sys.executable, '-B', '-c', 'import sys;print("child");sys.exit(7)']
    with pytest.raises(RuntimeError, match='exit 7'):
        r.command('native', command, tmp_path)
    saved = m.read(tmp_path/'native.command.json')
    assert saved['exit_code'] == 7 and saved['status'] == 'completed'
    assert (tmp_path/'native.stdout.log').read_text().strip() == 'child'
    with pytest.raises(ValueError, match='already attempted'):
        r.command('native', command, tmp_path)


def test_no_spawn_after_cutoff(tmp_path, monkeypatch):
    r = bare_run(tmp_path)
    monkeypatch.setattr(m, 'clock', lambda: datetime.fromisoformat('2026-11-05T15:00:00+08:00'))
    with pytest.raises(ValueError):
        r.command('future', [sys.executable, '-c', 'raise AssertionError()'], tmp_path)
    assert not (tmp_path/'future.command.json').exists()


def test_ledger_hashes_catch_same_size_content_change(tmp_path):
    (tmp_path/'journal').mkdir()
    for file in ['latest.json', 'migration_record.json', 'journal/one.json']:
        (tmp_path/file).write_text('{"x":1}')
    first = m.ledger_hashes(tmp_path)
    (tmp_path/'journal/one.json').write_text('{"x":2}')
    assert m.ledger_hashes(tmp_path) != first


def test_microcap_serial_order_with_native_gate_and_no_send(tmp_path, monkeypatch):
    r = bare_run(tmp_path)
    r.home = tmp_path/'home';r.micro=tmp_path/'micro';r.resident=tmp_path/'resident'
    r.gate=tmp_path/'gate.json';r.gate_sha='gate';r.anchor='2026-11-04';r.python=sys.executable
    (r.micro/'outputs').mkdir(parents=True)
    readiness=tmp_path/'readiness.json';readiness.write_text('native receipt')
    r.authorize=lambda: None
    monkeypatch.setattr(m,'require_window',lambda *a: None)
    calls=[]
    def command(label,args,cwd,env=None,**kwargs):
        calls.append(label)
        if label=='resident_intraday':
            m.write(tmp_path/'resident_intraday.stdout.log', {'pass':True,
                'readiness_path':str(readiness),'readiness_sha256':m.sha(readiness),
                'readiness':{'pass':True,'source_and_shared_inputs_unchanged':True,'expected_date':r.anchor,
                             'mode':'intraday','authorization':{'gate_sha256':'gate'}}})
        if label.startswith('microcap_v'):
            assert env['TOP100_REALTIME_REQUIRE_STATE']=='1'
            stem=Path(args[4]).stem
            (r.micro/'outputs'/(stem+'_realtime_signal.csv')).write_text('validated,fixture')
        assert not any('--apply'==str(a) or 'send_gmail' in str(a) for a in args)
    r.command=command
    def render(strategy,inputs):
        assert '--microcap-readiness' in inputs
        assert sum(v=='--microcap-csv' for v in inputs)==3
        calls.append('render');return {'actual_publication':False}
    r.render=render
    r.run_microcap()
    assert calls==['resident_intraday','resident_owner_before_signals','microcap_v2_0','microcap_v2_3','microcap_v2_5','render','resident_shutdown']
    assert not (r.micro/'outputs/top100_delivery.lock').exists()


def test_failed_or_unknown_actor_is_not_shutdown_or_retried(tmp_path):
    r=bare_run(tmp_path);r.home=tmp_path;r.micro=tmp_path;r.resident=tmp_path;r.gate=tmp_path/'gate'
    r.python=sys.executable;r.authorize=lambda:None;calls=[]
    def command(label,*a,**kw):
        calls.append(label);raise RuntimeError('client timed out; actor state unknown')
    r.command=command
    with pytest.raises(RuntimeError):r.run_microcap()
    assert calls==['resident_intraday']


def test_naive_clock_rejected():
    with pytest.raises(ValueError, match='timezone'):
        m.require_window('2026-11-05', datetime.fromisoformat('2026-11-05T14:20:00'))


def test_installation_manifest_binds_sources(tmp_path, monkeypatch):
    bundle=tmp_path/'bundle'; (bundle/'scripts').mkdir(parents=True)
    files={}
    for name in ['run_local_intraday.py','build_local_intraday_report.py']:
        p=bundle/'scripts'/name;p.write_text('immutable source');files['scripts/'+name]=m.sha(p)
    monkeypatch.setattr(m,'__file__',str(bundle/'scripts/run_local_intraday.py'))
    m.write(bundle/'manifest.json', {'schema':1,'files':files})
    m.write(tmp_path/'local_intraday_install.json', {'bundle':str(bundle),'manifest_sha256':m.sha(bundle/'manifest.json')})
    m.verify_installation(tmp_path)
    (bundle/'scripts/build_local_intraday_report.py').write_text('modified')
    with pytest.raises(ValueError,match='source changed'):m.verify_installation(tmp_path)


def test_empty_manifest_cannot_certify_installation(tmp_path, monkeypatch):
    bundle=tmp_path/'bundle';(bundle/'scripts').mkdir(parents=True)
    monkeypatch.setattr(m,'__file__',str(bundle/'scripts/run_local_intraday.py'))
    m.write(bundle/'manifest.json', {'schema':1,'files':{}})
    m.write(tmp_path/'local_intraday_install.json', {'bundle':str(bundle),'manifest_sha256':m.sha(bundle/'manifest.json')})
    with pytest.raises(ValueError,match='incomplete'):m.verify_installation(tmp_path)


def test_refresh_lock_prevents_signal_execution(tmp_path):
    (tmp_path/'outputs').mkdir()
    lock=tmp_path/'outputs/top100_delivery.lock';lock.write_text('another writer')
    with pytest.raises(FileExistsError):
        with m.microcap_writer_lock(tmp_path):raise AssertionError('must not enter')
    assert lock.read_text()=='another writer'


def test_own_lock_blocks_second_writer_and_releases_after_failure(tmp_path):
    (tmp_path/'outputs').mkdir()
    with pytest.raises(RuntimeError):
        with m.microcap_writer_lock(tmp_path):
            with pytest.raises(FileExistsError):
                with m.microcap_writer_lock(tmp_path):pass
            raise RuntimeError('native failed')
    assert not (tmp_path/'outputs/top100_delivery.lock').exists()


def test_shutdown_failure_preserves_valid_report(tmp_path):
    r=bare_run(tmp_path);r.home=tmp_path;r.micro=tmp_path;r.resident=tmp_path;r.gate=tmp_path/'gate'
    r.python=sys.executable;r.anchor='2026-11-04';r.gate_sha='gate';r.authorize=lambda:None
    (tmp_path/'outputs').mkdir()
    readiness=tmp_path/'readiness.json';readiness.write_text('original')
    def command(label,*a,**kw):
        if label=='resident_intraday':
            m.write(tmp_path/'resident_intraday.stdout.log', {'pass':True,'readiness_path':str(readiness),
              'readiness_sha256':m.sha(readiness),'readiness':{'pass':True,'mode':'intraday',
              'source_and_shared_inputs_unchanged':True,'expected_date':r.anchor,'authorization':{'gate_sha256':'gate'}}})
        if label=='resident_shutdown':raise RuntimeError('unknown cleanup')
    r.command=command;r.microcap_signals=lambda response:{'actual_publication':False,'summary':'valid'}
    result=r.run_microcap()
    assert result['summary']=='valid' and result['resident_cleanup']=='failed_or_unknown'


def test_missing_icim_source_does_not_prevent_other_strategy(tmp_path):
    r=bare_run(tmp_path);r.authorize=lambda:None;r.ledger_source=tmp_path/'missing.json'
    r.run_microcap=lambda:{'summary':'valid','actual_publication':False}
    assert r.execute()==1
    result=m.read(tmp_path/'run.json')['results']
    assert result['microcap']['status']=='ready_not_published'
    assert result['icim']['status']=='failed_or_unknown'


def test_constructor_uses_native_active_location_and_allows_missing_other_source(tmp_path, monkeypatch):
    home=tmp_path/'home';(home/'scripts').mkdir(parents=True)
    native_active=home/'scripts/realtime_daily_active.json'
    m.write(native_active, {'prepared':str(tmp_path/'prepared')})
    m.write(home/'scripts/realtime_daily_gate.json', {'microcap_source':str(tmp_path/'micro'), 'icim_source':str(tmp_path/'icim')})
    gate=tmp_path/'gate.json';m.write(gate,{'calendar':{'today':'2026-11-05','latest_completed_session':'2026-11-04','next_session':'2026-11-06'}})
    monkeypatch.setattr(m,'verify_installation',lambda h:{'bound':True})
    monkeypatch.setattr(m,'load_readiness',lambda h:SimpleNamespace(_launcher=lambda:SimpleNamespace(ACTIVE=native_active),authorize_intraday=lambda *a:{'native':True}))
    monkeypatch.setattr(m,'clock',lambda:datetime.fromisoformat('2026-11-05T14:20:00+08:00'))
    run=m.Run(home,gate,tmp_path/'resident',tmp_path/'missing.json',tmp_path/'result')
    assert run.authorization=={'native':True}
    run.authorize()
