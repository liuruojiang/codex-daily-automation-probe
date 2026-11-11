"""Preparation requires the real latest completed date, for cached or restored CP."""
import json
import contextlib
from datetime import date, datetime
import io
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import restore_microcap_member_checkpoint as restore
import run_microcap_member_local as runner
from run_microcap_member_local import reusable_checkpoint
from test_member_local_checkpoint_read_policy import AB, real_transport, invoke


def test_explicit_cache_is_reusable_only_for_exact_expected_completed_session(tmp_path):
    path = tmp_path/'checkpoint.json'
    raw = json.dumps(dict(as_of_date='2026-11-09', history={'sha256':'valid'})).encode()
    path.write_bytes(raw)
    assert reusable_checkpoint(path,{},'2026-11-11') is None
    assert reusable_checkpoint(path,{},'2026-11-09') == path.resolve()
    assert path.read_bytes() == raw


def test_real_D11_package_meets_exact_D11_requirement(tmp_path,monkeypatch,capsys):
    real_transport(monkeypatch)
    out = tmp_path/'current'
    invoke(monkeypatch,out,extra=['--expected-date','2026-11-11'])
    assert json.loads((out/'checkpoint.json').read_bytes())['as_of_date'] == '2026-11-11'


def test_recovery_cannot_call_an_older_published_package_current(tmp_path,monkeypatch):
    # An internally valid but stale selected package must fail before unpacking.
    raw = (ROOT/'config/microcap_member_seed_20261109_4774966d.zip').read_bytes()
    identity = restore.checkpoint_identity(raw)
    monkeypatch.setattr(restore,'fetch_latest',lambda *a,**k:dict(_payload=raw,_selection=identity))
    monkeypatch.setenv('GITHUB_TOKEN','offline-test')
    out = tmp_path/'not-installed'
    with pytest.raises(ValueError,match='expected completed session'):
        invoke(monkeypatch,out,extra=['--expected-date','2026-11-11'])
    assert not out.exists()


def test_fallback_seed_cannot_cover_a_later_expected_session(tmp_path,monkeypatch):
    def missing(*args,**kwargs):
        raise RuntimeError('No published member checkpoint: software-only empty namespace')
    monkeypatch.setattr(restore,'fetch_latest',missing)
    monkeypatch.setenv('GITHUB_TOKEN','offline-test')
    out = tmp_path/'not-installed'
    with pytest.raises(ValueError,match='seed does not cover the expected'):
        invoke(monkeypatch,out,extra=['--expected-date','2026-11-11'])
    assert not out.exists()


def test_prepare_replaces_stale_cache_via_exact_D_restore_then_native_verify(tmp_path,monkeypatch,capsys):
    # Only the calendar acquisition and Git identity boundary are software
    # fixtures; runner, restore/source selection and native verify are real.
    real_transport(monkeypatch)
    monkeypatch.setenv('GITHUB_REPOSITORY','liuruojiang/codex-daily-automation-probe')
    native=tmp_path/'reader'
    (native/'scripts').mkdir(parents=True)
    verifier=ROOT/'tests/fixtures/member_checkpoint_local_read_20261111/ab214_member_period_handoff.py'
    (native/'scripts/member_period_handoff.py').write_bytes(verifier.read_bytes())
    calendar=ModuleType('scripts.exchange_calendar')
    calendar.latest_completed_session=lambda:date(2026,11,11)
    monkeypatch.setitem(sys.modules,'scripts.exchange_calendar',calendar)
    stale=tmp_path/'old-checkpoint.json'
    raw=json.dumps(dict(as_of_date='2026-11-09',history={'sha256':'valid-old'})).encode()
    stale.write_bytes(raw)
    original=runner.subprocess.run
    def command(argv,**kwargs):
        if argv[:2]==['git','-C']:
            return SimpleNamespace(stdout=AB+'\n',returncode=0)
        if Path(argv[2]).name=='restore_microcap_member_checkpoint.py':
            assert argv[argv.index('--expected-date')+1]=='2026-11-11'
            with monkeypatch.context() as patch:
                patch.setattr(sys,'argv',[argv[2],*argv[3:]])
                stream=io.StringIO()
                with contextlib.redirect_stdout(stream):restore.main()
            return SimpleNamespace(returncode=0,stdout=stream.getvalue(),stderr='')
        return original(argv,**kwargs)
    monkeypatch.setattr(runner.subprocess,'run',command)
    monkeypatch.setattr(runner,'github_environment',lambda:{})
    out=tmp_path/'prepared'
    today=datetime.now(runner.BJ).date().isoformat()
    monkeypatch.setattr(sys,'argv',['prepare','--automation-root',str(ROOT),'--root',str(native),
        '--date',today,'--out',str(out),'--checkpoint',str(stale),'--prepare-only'])
    assert runner.main()==0
    result=json.loads(capsys.readouterr().out.strip())
    assert result['status']=='prepared' and stale.read_bytes()==raw
    assert json.loads(Path(result['checkpoint']).read_bytes())['as_of_date']=='2026-11-11'
    commands=json.loads((out/'commands.json').read_bytes())
    assert len(commands)==2 and all(c['exit_code']==0 for c in commands)
    assert json.loads((out/'stale_checkpoint.json').read_bytes())['expected_as_of']=='2026-11-11'
