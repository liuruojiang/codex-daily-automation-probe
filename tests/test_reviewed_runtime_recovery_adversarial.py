"""Independent real-Git/whole-ZIP boundaries with an explicit software restore.

No native market validation, network download, formal refresh or sending occurs.
The original activator and wrapper run unmodified against the tiny fixture.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import zipfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import microcap_reviewed_runtime_recovery as m


class Interrupted(BaseException):
    pass


@pytest.fixture
def boundary(tmp_path, monkeypatch):
    root=tmp_path/'git-consumer'; root.mkdir()
    def git(*args):
        return subprocess.check_output(['git','-C',str(root),*args],stderr=subprocess.PIPE).decode().strip()
    git('init','-q');git('config','user.name','Independent software boundary')
    git('config','user.email','boundary@example.invalid');git('config','core.autocrlf','false')
    source=root/'scripts/reader.py';source.parent.mkdir();source.write_bytes(b'original reader\n')
    history=root/'outputs/history.csv';history.parent.mkdir()
    original=b'date,value\r\n2026-11-10,1.000000000000003\r\n';history.write_bytes(original)
    (root/'.gitignore').write_text('.microcap_index_cache/\n')
    git('add','.');git('commit','-qm','original software baseline');old=git('rev-parse','HEAD')
    source.write_bytes(b'reviewed parser successor\n')
    newfile=root/'tests/parser-fixture.txt';newfile.parent.mkdir();newfile.write_bytes(b'reviewed source evidence\n')
    git('add','.');git('commit','-qm','source-only successor');new=git('rev-parse','HEAD')
    git('remote','add','origin',str(root))
    changes={}
    for name in git('diff','--name-only',old,new).splitlines():
        try: before=subprocess.check_output(['git','-C',str(root),'show',old+':'+name],stderr=subprocess.PIPE)
        except subprocess.CalledProcessError:before=None
        after=subprocess.check_output(['git','-C',str(root),'show',new+':'+name])
        changes[name]=[hashlib.sha256(before).hexdigest() if before is not None else None,hashlib.sha256(after).hexdigest()]
    review=tmp_path/'review.json'
    review.write_text(json.dumps(dict(schema_version=1,baseline_sha=old,runtime_sha=new,changes=changes)))
    ignored=root/'.microcap_index_cache/raw/source.json';ignored.parent.mkdir(parents=True)
    ignored.write_bytes(b'original ignored source\x00\xff')
    manifest=dict(ok=True,status='complete',scope='whole_workspace_delivery',release_sha=old,
        expected_date='2026-11-10',verified_at='2026-11-12T16:00:00+00:00',
        inputs={'scripts/reader.py':changes['scripts/reader.py'][0]},
        artifacts={'outputs/history.csv':hashlib.sha256(original).hexdigest()},streams={'software_fixture':{'rows':1}})
    manifest_raw=json.dumps(manifest).encode(); seed=tmp_path/'original-software-seed.zip'
    with zipfile.ZipFile(seed,'w') as z:
        z.writestr(m.MANIFEST,manifest_raw);z.writestr('outputs/history.csv',original)
    args=dict(root=root,runtime_sha=new,not_after='2026-11-12',bundle=tmp_path/'downloaded.zip',
        receipt=tmp_path/'original-restore.json',token='software-no-network',review=review,
        activation_receipt=tmp_path/'prepare/source-activation.json',allow_activation=True,
        fallback_url='https://github.com/liuruojiang/codex-daily-automation-probe/releases/download/software/original.zip',
        fallback_sha256=hashlib.sha256(seed.read_bytes()).hexdigest())
    calls=[]
    def complete(kw):
        assert git('rev-parse','HEAD')==old==kw['runtime_sha']
        assert source.read_bytes()==b'original reader\n' and not newfile.exists()
        kw['bundle'].write_bytes(seed.read_bytes())
        (root/m.MANIFEST).write_bytes(manifest_raw)
        result=dict(restored=True,status='scoped_whole_restore_verified',runtime_sha=old,
            repository=m.REPOSITORY,source='explicit_release_seed',fallback_used=True,
            source_manifest_release_sha=old,restored_manifest_release_sha=old,
            historical_release_files_equivalent=True,seed_date='2026-11-10',
            source_manifest_sha256=hashlib.sha256(manifest_raw).hexdigest(),
            bundle_sha256=hashlib.sha256(seed.read_bytes()).hexdigest(),bundle_bytes=seed.stat().st_size,
            native_restore=dict(ok=True,expected_date='2026-11-10'),seed_url=args['fallback_url'],
            seed_sha256=args['fallback_sha256'])
        kw['receipt'].parent.mkdir(parents=True,exist_ok=True)
        kw['receipt'].write_bytes(json.dumps(result).encode())
        return result
    def restore(**kw):calls.append(kw);return complete(kw)
    monkeypatch.setattr(m,'fetch_latest',lambda *a:None)
    monkeypatch.setattr(m,'recover',restore)
    return SimpleNamespace(args=args,git=git,old=old,new=new,source=source,history=history,
        ignored=ignored,original=original,seed=seed,manifest_raw=manifest_raw,calls=calls,
        complete=complete,restore=restore,real_activate=m.activate)


def test_original_whole_identity_and_ignored_sources_survive_real_activation(boundary):
    b=boundary; raw=b.seed.read_bytes();ignored=b.ignored.read_bytes()
    result=m.recover_reviewed(**b.args)
    assert result['baseline_restore']['runtime_sha']==b.old!=result['source_activation']['runtime_sha']==b.new
    assert b.args['receipt'].read_bytes()==b.calls[0]['receipt'].read_bytes()
    assert b.args['bundle'].read_bytes()==raw and b.history.read_bytes()==b.original and b.ignored.read_bytes()==ignored
    assert b.git('rev-parse','HEAD')==b.new and len(b.calls)==1
    assert not result['current_data_verified'] and not result['formal_release_verified'] and not result['email_sent']


def test_failed_baseline_restore_restarts_from_own_old_head_without_overwriting_failure(boundary,monkeypatch):
    b=boundary; failures=[]
    def fail_once(**kw):
        if not failures:
            kw['receipt'].parent.mkdir(parents=True,exist_ok=True)
            kw['receipt'].write_bytes(b'{"status":"failed","error_code":"download_transport_unavailable"}')
            failures.append((kw['receipt'],kw['receipt'].read_bytes()))
            raise m.RecoveryError('download_transport_unavailable')
        return b.restore(**kw)
    monkeypatch.setattr(m,'recover',fail_once)
    with pytest.raises(m.RecoveryError,match='transport'):m.recover_reviewed(**b.args)
    assert b.git('rev-parse','HEAD')==b.old
    result=m.recover_reviewed(**b.args)
    assert result['source_activation']['activated'] and b.git('rev-parse','HEAD')==b.new
    assert failures[0][0].read_bytes()==failures[0][1] and len(b.calls)==1


@pytest.mark.parametrize('point',['before_activation','after_receipt','after_checkout_without_receipt'])
def test_activation_restart_reuses_verified_restore_and_never_replays_completed_checkout(boundary,monkeypatch,point):
    b=boundary; activations=[]
    def interrupt(*args,**kwargs):
        activations.append('called')
        if point=='before_activation':raise Interrupted()
        b.real_activate(*args,**kwargs)
        if point=='after_checkout_without_receipt':
            target=b.args['activation_receipt'];assert target.is_relative_to(b.args['root'].parent)
            target.unlink()  # Only this managed fixture's just-created receipt.
        raise Interrupted()
    monkeypatch.setattr(m,'activate',interrupt)
    with pytest.raises(Interrupted):m.recover_reviewed(**b.args)
    before=b.args['receipt'].read_bytes(); bundle=b.args['bundle'].read_bytes()
    def finish(*args,**kwargs):activations.append('finished');return b.real_activate(*args,**kwargs)
    monkeypatch.setattr(m,'activate',finish)
    result=m.recover_reviewed(**b.args)
    assert b.git('rev-parse','HEAD')==b.new and len(b.calls)==1
    assert b.args['receipt'].read_bytes()==before and b.args['bundle'].read_bytes()==bundle
    assert len(activations)==(2 if point=='before_activation' else 1)
    if point=='after_checkout_without_receipt':
        assert result['source_activation']['schema']=='reviewed_scoped_runtime_readback_v1'
    original_receipt=b.args['activation_receipt'].read_bytes()
    m.recover_reviewed(**b.args)
    assert b.args['activation_receipt'].read_bytes()==original_receipt and len(b.calls)==1
    assert len(activations)==(2 if point=='before_activation' else 1)


@pytest.mark.parametrize('fault',['ignored_change','ignored_add','tracked_change','review_change','receipt_change','unknown_head'])
def test_lost_response_never_promotes_changed_data_or_unknown_identity(boundary,monkeypatch,fault):
    b=boundary
    def interrupt(*a,**kw):b.real_activate(*a,**kw);raise Interrupted()
    monkeypatch.setattr(m,'activate',interrupt)
    with pytest.raises(Interrupted):m.recover_reviewed(**b.args)
    if fault=='ignored_change':b.ignored.write_bytes(b'changed ignored raw')
    elif fault=='ignored_add':b.ignored.with_name('injected.json').write_bytes(b'unknown source')
    elif fault=='tracked_change':b.history.write_bytes(b'changed historical numeric value')
    elif fault=='review_change':b.args['review'].write_bytes(b.args['review'].read_bytes()+b' ')
    elif fault=='receipt_change':b.args['receipt'].write_bytes(b.args['receipt'].read_bytes()+b' ')
    else:
        b.git('commit','--allow-empty','-qm','unknown role')
    preserved={p:p.read_bytes() for p in [b.history,b.ignored,b.args['receipt'],b.args['activation_receipt']]}
    monkeypatch.setattr(m,'activate',lambda *a,**kw:pytest.fail('Unknown state must not activate again'))
    with pytest.raises((m.RecoveryError,ValueError)):m.recover_reviewed(**b.args)
    assert len(b.calls)==1 and all(p.read_bytes()==raw for p,raw in preserved.items())


def test_full_pinned_seed_after_failed_restore_uses_native_recheck_without_second_download(boundary,monkeypatch):
    b=boundary; failed=[];checks=[]
    def failed_restore(**kw):
        b.complete(kw)
        kw['receipt'].write_bytes(b'{"status":"failed","error_code":"native_whole-restore_failed"}')
        failed.append((kw['receipt'],kw['receipt'].read_bytes()))
        raise m.RecoveryError('native_whole-restore_failed')
    monkeypatch.setattr(m,'recover',failed_restore)
    with pytest.raises(m.RecoveryError):m.recover_reviewed(**b.args)
    def software_native(root,bundle,runtime,target,work):
        checks.append((root,bundle,runtime,target))
        result=b.complete(dict(bundle=bundle,receipt=work/'software-verified-receipt.json',runtime_sha=runtime))
        return result
    monkeypatch.setattr(m,'restore_bundle',software_native)
    monkeypatch.setattr(m,'recover',lambda **kw:pytest.fail('A pinned complete seed must not download again'))
    result=m.recover_reviewed(**b.args)
    assert result['baseline_restore']['source']=='reused_exact_original_seed' and len(checks)==1
    assert failed[0][0].read_bytes()==failed[0][1] and b.history.read_bytes()==b.original


@pytest.mark.parametrize('fault',['old_head_without_intent','unowned_bundle','unowned_receipt','api_unknown'])
def test_unowned_state_or_unknown_artifact_discovery_never_enters_fallback(boundary,monkeypatch,fault):
    b=boundary
    if fault=='old_head_without_intent':b.git('checkout','--detach',b.old)
    elif fault=='unowned_bundle':b.args['bundle'].write_bytes(b.seed.read_bytes())
    elif fault=='unowned_receipt':b.args['activation_receipt'].parent.mkdir();b.args['activation_receipt'].write_text('{}')
    else:
        def unknown(*a):raise m.RecoveryError('github_api_http_403')
        monkeypatch.setattr(m,'fetch_latest',unknown)
    initial=b.git('rev-parse','HEAD');original=b.history.read_bytes()
    with pytest.raises(m.RecoveryError):m.recover_reviewed(**b.args)
    assert b.git('rev-parse','HEAD')==initial and b.history.read_bytes()==original and not b.calls


@pytest.mark.parametrize('fault',['owner_missing','owner_changed','activation_fields'])
def test_resume_requires_same_checkout_and_independently_recomputed_activation_fields(boundary,monkeypatch,fault):
    b=boundary
    def interrupt(*a,**kw):b.real_activate(*a,**kw);raise Interrupted()
    monkeypatch.setattr(m,'activate',interrupt)
    with pytest.raises(Interrupted):m.recover_reviewed(**b.args)
    intent=b.args['activation_receipt'].with_name(b.args['activation_receipt'].name+'.intent.json')
    owner=Path(b.git('rev-parse','--git-path','codex-reviewed-restore.owner'))
    if not owner.is_absolute():owner=b.args['root']/owner
    if fault=='owner_missing':
        assert owner.is_relative_to(b.args['root']);owner.unlink()
    elif fault=='owner_changed':owner.write_bytes(b'f'*32)
    else:
        journal=json.loads(intent.read_bytes());journal['activation']['historical_rewrite_authorized']=True
        intent.write_text(json.dumps(journal))
        fake=json.loads(b.args['activation_receipt'].read_bytes());fake['historical_rewrite_authorized']=True
        b.args['activation_receipt'].write_text(json.dumps(fake))
    original=b.history.read_bytes();ignored=b.ignored.read_bytes();raw_receipt=b.args['receipt'].read_bytes()
    monkeypatch.setattr(m,'activate',lambda *a,**kw:pytest.fail('Unbound resume must not activate'))
    with pytest.raises(m.RecoveryError):m.recover_reviewed(**b.args)
    assert b.history.read_bytes()==original and b.ignored.read_bytes()==ignored
    assert b.args['receipt'].read_bytes()==raw_receipt and len(b.calls)==1


def test_unreadable_data_inventory_is_not_an_empty_unchanged_inventory(tmp_path,monkeypatch):
    (tmp_path/'outputs').mkdir()
    def unavailable(*args,**kwargs):
        kwargs['onerror'](PermissionError('software unreadable data subtree'))
        return iter(())
    monkeypatch.setattr(m.os,'walk',unavailable)
    with pytest.raises(m.RecoveryError,match='unreadable'):m.data_identity(tmp_path)


def test_closed_completed_receipt_is_idempotent_and_later_loss_is_not_recreated(boundary):
    b=boundary;m.recover_reviewed(**b.args)
    intent=b.args['activation_receipt'].with_name(b.args['activation_receipt'].name+'.intent.json')
    original_intent=intent.read_bytes();original_receipt=b.args['activation_receipt'].read_bytes()
    m.recover_reviewed(**b.args)
    assert intent.read_bytes()==original_intent and b.args['activation_receipt'].read_bytes()==original_receipt
    assert len(b.calls)==1
    target=b.args['activation_receipt'];assert target.is_relative_to(b.args['root'].parent)
    target.unlink()
    with pytest.raises(m.RecoveryError,match='previously_closed_activation_receipt_missing'):
        m.recover_reviewed(**b.args)
    assert not target.exists() and intent.read_bytes()==original_intent and len(b.calls)==1
