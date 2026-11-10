import copy,json
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import microcap_scoped_settings as m


def config(tmp_path,change=None):
    value=json.loads((Path(__file__).resolve().parents[1]/'config/microcap_scoped_rebalance.json').read_bytes())
    value.update(enabled=False,whole_seed=None,producer_sha=m.RUNTIME)
    if change:change(value)
    path=tmp_path/'config.json';path.write_text(json.dumps(value));return path


def test_inactive_preparation_is_readable_but_cannot_run_acceptance_without_seed(tmp_path):
    path=config(tmp_path)
    assert m.load(path)['enabled'] is False
    with pytest.raises(ValueError,match='seed has not been accepted'):m.load(path,require_seed=True)


@pytest.mark.parametrize('fault',['enabled_without_seed','producer','sha','other_repo','http','query','parent','future_format'])
def test_invalid_or_unreviewed_pointers_cannot_activate_a_source_runtime(tmp_path,fault):
    def change(v):
        item=v['weekly_roster']
        if fault=='enabled_without_seed':v['enabled']=True
        elif fault=='producer':v['producer_sha']='0'*40
        elif fault=='sha':item['sha256']='bad'
        elif fault=='other_repo':item['url']=item['url'].replace('/liuruojiang/','/unknown/')
        elif fault=='http':item['url']=item['url'].replace('https:','http:')
        elif fault=='query':item['url']+='?unchecked=1'
        elif fault=='parent':item['url']=item['url'].replace('/download/','/download/../')
        else:item['date']='not-a-date'
    with pytest.raises(ValueError):m.load(config(tmp_path,change))


@pytest.fixture
def source_download(tmp_path,monkeypatch):
    import restore_microcap_scoped_inputs as host
    import microcap_scoped_inputs as transport
    from prepare_microcap_local import sha
    body=b'local source download transport fixture'
    item=dict(url='https://github.com/liuruojiang/codex-daily-automation-probe/releases/download/microcap-local-sources-test/source.zip',
              target='2026-11-11',sha256=sha(body),runtime_sha=host.RUNTIME,producer_commit='c'*40)
    path=tmp_path/'host.json';path.write_text(json.dumps(dict(schema='microcap_local_maintenance_host_v1',owner='local',scoped_inputs=item)))
    calls=[]
    class Response:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def read(self,limit):return body
    def get(url,**kwargs):calls.append(url);return Response()
    monkeypatch.setattr(m.urllib.request,'urlopen',get)
    manifest=dict(target=item['target'],runtime_sha=item['runtime_sha'],producer_commit=item['producer_commit'])
    monkeypatch.setattr(transport,'read_bundle',lambda *a:(manifest,{},item['sha256']))
    return path,tmp_path/'sources.zip',body,calls,manifest


def test_successful_source_download_is_revalidated_and_reused_without_second_http(source_download):
    config,bundle,body,calls,_=source_download
    first=m.download_sources(config,bundle,'2026-11-12');second=m.download_sources(config,bundle,'2026-11-12')
    assert first==second and len(calls)==1 and bundle.read_bytes()==body


def test_corrupt_cached_source_is_preserved_and_never_redownloaded(source_download):
    config,bundle,_,calls,_=source_download;bundle.write_bytes(b'corrupt cached evidence')
    with pytest.raises(ValueError,match='SHA differs'):m.download_sources(config,bundle,'2026-11-12')
    assert calls==[] and bundle.read_bytes()==b'corrupt cached evidence'


def test_source_download_rejects_a_different_original_producer_runtime(source_download):
    config,bundle,_,_,manifest=source_download;manifest['runtime_sha']='0'*40
    with pytest.raises(ValueError,match='producer receipt differ'):m.download_sources(config,bundle,'2026-11-12')
