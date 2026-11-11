import io
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import restore_microcap_scoped_inputs as restore


def config(tmp_path, pointer=None, owner='cloud'):
    path=tmp_path/'host.json'
    path.write_text(json.dumps(dict(schema='microcap_local_maintenance_host_v1',owner=owner,scoped_inputs=pointer)))
    return path


def pointer():
    return dict(url='https://github.com/liuruojiang/codex-daily-automation-probe/releases/download/microcap-local-sources-20261106/input.zip',
                sha256='a'*64,runtime_sha=restore.RUNTIME,target='2026-11-06',producer_commit='b'*40)


def test_disabled_source_pointer_leaves_original_rebalance_unchanged(tmp_path,monkeypatch):
    monkeypatch.setattr(restore,'install',lambda *a,**k:pytest.fail('unexpected production import'))
    result=restore.restore(tmp_path/'root',config(tmp_path),'2026-11-06',tmp_path/'out',
                           opener=lambda *a,**k:pytest.fail('unexpected download'))
    assert result['status']=='no_local_source_bundle_configured'
    assert not (tmp_path/'out').exists()


def test_older_reviewed_producer_keeps_its_original_pointer_identity(tmp_path):
    p=pointer();p['runtime_sha']='be89e87a0da728b27be0308d7c4c9e57a212b815'
    assert restore.load_config(config(tmp_path,p))['scoped_inputs']==p


@pytest.mark.parametrize('url',[
    'http://github.com/liuruojiang/codex-daily-automation-probe/releases/download/microcap-local-sources-x/input.zip',
    'https://github.com/another/repo/releases/download/microcap-local-sources-x/input.zip',
    'https://github.com/liuruojiang/codex-daily-automation-probe/releases/download/microcap-local-sources-x/%2E%2E/input.zip',
    'https://github.com/liuruojiang/codex-daily-automation-probe/releases/download/microcap-local-sources-x/input.zip?token=example',
])
def test_release_route_cannot_be_replaced(tmp_path,url):
    p=pointer();p['url']=url
    with pytest.raises(ValueError):restore.load_config(config(tmp_path,p))


@pytest.mark.parametrize('target',['2026-11-05','2026-11-14'])
def test_future_or_stale_bundle_is_not_downloaded(tmp_path,target):
    with pytest.raises(ValueError,match='future or stale'):
        restore.restore(tmp_path/'root',config(tmp_path,pointer()),target,tmp_path/'out',
                        opener=lambda *a,**k:pytest.fail('unexpected download'))


def test_corrupt_download_does_not_install_or_create_output(tmp_path,monkeypatch):
    monkeypatch.setattr(restore,'install',lambda *a,**k:pytest.fail('unexpected import'))
    with pytest.raises(ValueError,match='SHA differs'):
        restore.restore(tmp_path/'root',config(tmp_path,pointer()),'2026-11-06',tmp_path/'out',
                        opener=lambda *a,**k:io.BytesIO(b'corrupt'))
    assert not (tmp_path/'out').exists()
