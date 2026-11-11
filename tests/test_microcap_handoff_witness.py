"""Small software orchestration cases; not live strategy acceptance."""
import copy,json,sys,zipfile
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import microcap_handoff_witness as w

@pytest.fixture
def package(tmp_path):
    root=tmp_path/'consumer';(root/'outputs').mkdir(parents=True)
    panel=b'date,1.000852\n2026-11-10,6579.356\n';(root/w.PANEL).write_bytes(panel)
    manifest=json.dumps(dict(ok=True,status='complete',scope='whole_workspace_delivery',expected_date='2026-11-10')).encode()
    (root/'outputs/top100_delivery_manifest.json').write_bytes(manifest)
    paths=[]
    for name,value in [('current','6579.36'),('candidate','6579.356')]:
        folder=tmp_path/name;folder.mkdir();raw=f'date,microcap,hedge\n2026-11-10,287145.9583492838,{value}\n'.encode()
        period=json.dumps(dict(source_files={w.PANEL:w.sha(panel)},source_manifest_sha256=w.sha(manifest))).encode()
        (folder/'history.csv').write_bytes(raw);(folder/'period.json').write_bytes(period)
        cp=dict(schema='member_daily_checkpoint_v1',as_of_date='2026-11-10',bootstrap_source='existing_formal_exports',history=dict(path='history.csv',sha256=w.sha(raw)),period=dict(path='period.json',sha256=w.sha(period)))
        p=folder/'checkpoint.json';p.write_text(json.dumps(cp));paths.append(p)
    calls=[]
    def fetch(day,out,expected):
        calls.append((day,expected['hedge']));return dict(provider='eastmoney',instrument='1.000852',hedge_json=w.blob(b'original receipt'),bar_csv=w.blob(b'original bar'))
    return root,*paths,tmp_path/'witnesses',fetch,calls

def test_real_archived_source_log_is_bound_to_both_original_checkpoints(package):
    root,current,candidate,out,fetch,calls=package
    path=w.build(root,current,candidate,out,fetch=fetch);x=json.loads(path.read_bytes())
    assert calls==[('2026-11-10','6579.36')]
    assert x['current_checkpoint_sha256']==w.sha(current.read_bytes())
    assert x['candidate_checkpoint_sha256']==w.sha(candidate.read_bytes())
    assert x['records'][0]['formal']['close']=='6579.356'
    assert '[index-history]' in __import__('base64').b64decode(x['records'][0]['formal']['source_log']['base64']).decode()

@pytest.mark.parametrize('fault',['microcap','history_hash','panel','incomplete_formal','unknown_date'])
def test_unrelated_or_changed_state_cannot_acquire_a_precision_witness(package,fault):
    root,current,candidate,out,fetch,calls=package
    if fault=='panel':(root/w.PANEL).write_bytes(b'changed')
    elif fault=='incomplete_formal':(root/'outputs/top100_delivery_manifest.json').write_text('{}')
    else:
        raw=(candidate.parent/'history.csv').read_bytes()
        if fault=='microcap':raw=raw.replace(b'287145.9583492838',b'999')
        elif fault=='unknown_date':raw=raw.replace(b'2026-11-10',b'2026-11-09')
        else:raw+=b'changed'
        (candidate.parent/'history.csv').write_bytes(raw)
        if fault!='history_hash':
            cp=json.loads(candidate.read_bytes());cp['history']['sha256']=w.sha(raw);candidate.write_text(json.dumps(cp))
    with pytest.raises((ValueError,KeyError)):w.build(root,current,candidate,out,fetch=fetch)
    assert not calls

def test_identical_history_needs_no_source_requests(package):
    root,current,candidate,out,fetch,calls=package
    raw=(candidate.parent/'history.csv').read_bytes();(current.parent/'history.csv').write_bytes(raw)
    cp=json.loads(current.read_bytes());cp['history']['sha256']=w.sha(raw);current.write_text(json.dumps(cp))
    assert w.build(root,current,candidate,out,fetch=fetch) is None and not calls

def test_archive_digest_failure_never_extracts_unverified_source_bytes(tmp_path,monkeypatch):
    archive=tmp_path/'report-1.zip';archive.write_bytes(b'bad cache')
    with pytest.raises(ValueError,match='Cached original witness artifact changed'):
        w.original_report(dict(id=1,digest='sha256:'+'a'*64,size_in_bytes=9),tmp_path)
