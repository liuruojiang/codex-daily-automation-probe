"""Runtime/package identity is separate from a precisely approved test-source role."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/'scripts/verify_microcap_daily_test_source.py'
spec=importlib.util.spec_from_file_location('daily_test_source',SCRIPT)
source=importlib.util.module_from_spec(spec);spec.loader.exec_module(source)


def git(root,*args):
    return subprocess.check_output(['git','-C',str(root),*args],stderr=subprocess.PIPE).decode().strip()


@pytest.fixture
def roles(tmp_path):
    repo=tmp_path/'runtime';repo.mkdir()
    git(repo,'init');git(repo,'config','user.name','fixture');git(repo,'config','user.email','fixture@example.invalid')
    git(repo,'config','core.autocrlf','false')
    git(repo,'remote','add','origin','https://github.com/liuruojiang/microcap.git')
    for name,body in {'strategy.py':b'original runtime\n','tests/helper.py':b'old helper\n','scripts/dep.py':b'dep\n'}.items():
        path=repo/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(body)
    git(repo,'add','.');git(repo,'commit','-m','runtime');runtime=git(repo,'rev-parse','HEAD')
    (repo/'tests/helper.py').write_bytes(b'new helper\n')
    (repo/'tests/new_test.py').write_bytes(b'new tests\n')
    git(repo,'add','.');git(repo,'commit','-m','approved tests');test=git(repo,'rev-parse','HEAD')
    changes=[]
    for path in ['tests/helper.py','tests/new_test.py']:
        try:old=hashlib.sha256(subprocess.check_output(['git','-C',str(repo),'show',runtime+':'+path],stderr=subprocess.PIPE)).hexdigest()
        except subprocess.CalledProcessError:old=None
        new=hashlib.sha256((repo/path).read_bytes()).hexdigest()
        changes.append({'path':path,'old_git_blob_sha256':old,'new_git_blob_sha256':new})
    config={'strategy_sha':runtime,'daily_test_source':{'runtime_sha':runtime,'source_sha':test,'allowed_changes':changes}}
    config_path=tmp_path/'config.json';config_path.write_text(json.dumps(config),encoding='utf8')
    return repo,config_path,config,runtime,test


@pytest.mark.parametrize('head_role',['runtime','test'])
def test_both_explicit_heads_prove_git_runtime_identity_without_state_changes(roles,head_role):
    repo,path,_,runtime,test=roles
    git(repo,'checkout','--detach',runtime if head_role=='runtime' else test)
    before=git(repo,'status','--porcelain')
    proof=source.verify(repo,path)
    assert proof['verified'] is True and proof['runtime_equivalent'] is True
    assert proof['runtime_sha']==runtime and proof['test_source_sha']==test
    assert proof['actual_repo_head']==(runtime if head_role=='runtime' else test)
    assert proof['runtime_or_package_author_relabelled'] is False
    assert git(repo,'status','--porcelain')==before


@pytest.mark.parametrize('fault',['runtime','test','pin','missing_path','extra_path','old_hash','new_hash','duplicate','path','missing_hash'])
def test_unapproved_role_or_change_is_rejected(roles,fault):
    repo,path,config,runtime,test=roles
    role=config['daily_test_source']
    if fault=='runtime':role['runtime_sha']='main'
    elif fault=='test':role['source_sha']=True
    elif fault=='pin':config['strategy_sha']=test
    elif fault=='missing_path':role['allowed_changes'].pop()
    elif fault=='extra_path':role['allowed_changes'].append({'path':'tests/extra.py','old_git_blob_sha256':None,'new_git_blob_sha256':'f'*64})
    elif fault=='old_hash':role['allowed_changes'][0]['old_git_blob_sha256']='0'*64
    elif fault=='new_hash':role['allowed_changes'][0]['new_git_blob_sha256']='0'*64
    elif fault=='duplicate':role['allowed_changes'].append(copy.deepcopy(role['allowed_changes'][0]))
    elif fault=='path':role['allowed_changes'][0]['path']='tests/../strategy.py'
    elif fault=='missing_hash':del role['allowed_changes'][0]['new_git_blob_sha256']
    path.write_text(json.dumps(config),encoding='utf8')
    with pytest.raises((ValueError,subprocess.CalledProcessError)):source.verify(repo,path)


def test_different_repository_and_unknown_head_rejected(roles):
    repo,path,_,_,_=roles
    git(repo,'remote','set-url','origin','https://github.com/unknown/microcap.git')
    with pytest.raises(ValueError,match='origin'):source.verify(repo,path)
    git(repo,'remote','set-url','origin','https://github.com/liuruojiang/microcap.git')
    (repo/'unapproved.txt').write_text('extra');git(repo,'add','.');git(repo,'commit','-m','unknown')
    with pytest.raises(ValueError,match='HEAD'):source.verify(repo,path)


def test_runtime_edit_cannot_be_authorized_as_test_change(roles):
    repo,path,config,_,_=roles
    (repo/'strategy.py').write_text('changed runtime')
    git(repo,'add','.');git(repo,'commit','-m','bad runtime')
    config['daily_test_source']['source_sha']=git(repo,'rev-parse','HEAD')
    config['daily_test_source']['allowed_changes'].append({'path':'strategy.py','old_git_blob_sha256':'a'*64,'new_git_blob_sha256':'b'*64})
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError,match='only reviewed test/doc'):source.verify(repo,path)


def test_materialization_installs_complete_immutable_test_code_and_rejects_mutation(roles,tmp_path):
    repo,path,_,runtime,test=roles;target=tmp_path/'test-source'
    proof=source.materialize(repo,path,target)
    assert git(repo,'rev-parse','HEAD')==test
    assert git(target,'rev-parse','HEAD')==test
    assert (target/'strategy.py').read_bytes()==b'original runtime\n'
    assert (target/'tests/helper.py').read_bytes()==b'new helper\n'
    receipt=tmp_path/'materialized.json';receipt.write_text(json.dumps(proof))
    assert source.check_materialized(receipt)['source_before_equals_after'] is True
    (target/'tests/helper.py').write_bytes(b'tampered')
    with pytest.raises(ValueError,match='bytes changed'):source.check_materialized(receipt)


def test_additional_python_and_existing_destination_are_rejected(roles,tmp_path):
    repo,path,_,_,_=roles;target=tmp_path/'test-source'
    proof=source.materialize(repo,path,target);receipt=tmp_path/'materialized.json';receipt.write_text(json.dumps(proof))
    (target/'tests/injected.py').write_text('malicious')
    with pytest.raises(ValueError,match='additional executable'):source.check_materialized(receipt)
    with pytest.raises(ValueError,match='target already exists'):source.materialize(repo,path,target)


def test_test_archive_is_independent_of_windows_repository_eol_configuration(roles,tmp_path):
    repo,path,_,_,_=roles
    git(repo,'config','core.autocrlf','true');git(repo,'config','core.eol','crlf')
    proof=source.materialize(repo,path,tmp_path/'exact-test-source')
    assert proof['materialized_complete_test_source'] is True
    assert (tmp_path/'exact-test-source/tests/helper.py').read_bytes()==b'new helper\n'


def test_optimized_cli_rejects_without_success_receipt(roles,tmp_path):
    repo,path,_,_,_=roles;output=tmp_path/'rejected.json'
    result=subprocess.run([sys.executable,'-O','-B',str(SCRIPT),'verify','--runtime-root',str(repo),'--config',str(path),'--out',str(output)],capture_output=True)
    assert result.returncode!=0 and not output.exists()


def test_recurring_workflow_uses_same_verifier_full_test_role_and_preserves_failed_group_xml():
    workflow=yaml.load((ROOT/'.github/workflows/delivery-regression.yml').read_text(),Loader=yaml.BaseLoader)
    steps=workflow['jobs']['strategy-tests']['steps']
    materialize=next(s for s in steps if s.get('name')=='Verify and materialize the separate complete Microcap test source')
    assert 'verify_microcap_daily_test_source.py materialize' in materialize['run']
    assert '--runtime-root strategy' in materialize['run'] and '--test-root strategy-tests' in materialize['run']
    test=next(s for s in steps if s.get('name')=='Test Microcap state and cloud transport invariants')
    assert test['working-directory']=='strategy-tests'
    for name in ['test_share_change_source.py','test_share_source_transport.py','test_share_unchanged_tail_20261103.py']:
        assert name in test['run']
    check=next(s for s in steps if s.get('name')=='Verify Microcap test source bytes after execution')
    assert 'always()' in check['if'] and 'check-materialized' in check['run']
    upload=next(s for s in steps if s.get('uses','').startswith('actions/upload-artifact'))
    for required in ['strategy-tests/regression-core.xml','strategy-tests/regression-release.xml','strategy/tested-sha.txt','strategy/tested-test-source-sha.txt','strategy/microcap-test-source-after.json']:
        assert required in upload['with']['path']
    formal=yaml.load((ROOT/'.github/workflows/microcap-realtime-digest.yml').read_text(),Loader=yaml.BaseLoader)
    assert formal['jobs']['regression']['uses']=='./.github/workflows/delivery-regression.yml'
    cfg=json.loads((ROOT/'config/microcap_approved_state.json').read_bytes())
    assert cfg['strategy_sha']==cfg['daily_test_source']['runtime_sha']
    assert cfg['daily_test_source']['source_sha']!=cfg['strategy_sha']
