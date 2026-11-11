"""The one-off archive publisher must reconcile rather than replace assets."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

PATH = Path(__file__).resolve().parents[1]/'scripts/publish_microcap_boundary_archive_20261112.py'
spec = importlib.util.spec_from_file_location('boundary_archive_publish', PATH)
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


@pytest.mark.parametrize('digest,size,state,name', [
    ('b'*64, 100, 'uploaded', publisher.NAME),
    ('a'*64, 99, 'uploaded', publisher.NAME),
    ('a'*64, 100, 'starter', publisher.NAME),
    ('a'*64, 100, 'uploaded', 'other.zip'),
])
def test_different_or_incomplete_existing_assets_are_rejected(digest, size, state, name):
    value = {'assets':[{'name':name, 'state':state, 'size':size, 'digest':'sha256:'+digest}]}
    with pytest.raises(ValueError):
        publisher.check_assets(value, 'a'*64, 100)


def test_existing_exact_asset_is_reused_and_empty_release_is_not_treated_as_uploaded():
    assert not publisher.check_assets({'assets':[]}, 'a'*64, 100)
    item = {'name':publisher.NAME, 'state':'uploaded', 'size':100, 'digest':'sha256:'+'a'*64}
    assert publisher.check_assets({'assets':[item]}, 'a'*64, 100)
    with pytest.raises(ValueError):
        publisher.check_assets({'assets':[item,item]}, 'a'*64, 100)


@pytest.mark.parametrize('payload', [b'{"status":"503"}', b'{"status":"403"}', b'', b'not-json'])
def test_failed_release_read_cannot_become_permission_to_create(monkeypatch, payload):
    monkeypatch.setattr(publisher.subprocess, 'run', lambda *a,**k: SimpleNamespace(returncode=1,stdout=payload))
    with pytest.raises(RuntimeError, match='reconcile'):
        publisher.release()


def test_actual_not_found_is_distinguished_from_unknown_state(monkeypatch):
    replies = iter([SimpleNamespace(returncode=1,stdout=b'{"status":"404"}'),
                    SimpleNamespace(returncode=0,stdout=b'[]')])
    monkeypatch.setattr(publisher.subprocess, 'run', lambda *a,**k: next(replies))
    assert publisher.release() is None


def test_successful_draft_creation_is_reconciled_when_tag_route_returns_404(monkeypatch):
    import json
    value = dict(id=442297118,tag_name=publisher.TAG,prerelease=False,draft=True,assets=[])
    replies = iter([SimpleNamespace(returncode=1,stdout=b'{"status":"404"}'),
                    SimpleNamespace(returncode=0,stdout=json.dumps([value]).encode())])
    monkeypatch.setattr(publisher.subprocess, 'run', lambda *a,**k: next(replies))
    assert publisher.release() == value


def test_duplicate_drafts_cannot_become_another_create_permission(monkeypatch):
    import json
    item = dict(tag_name=publisher.TAG,prerelease=False,draft=True,assets=[])
    replies = iter([SimpleNamespace(returncode=1,stdout=b'{"status":"404"}'),
                    SimpleNamespace(returncode=0,stdout=json.dumps([item,item]).encode())])
    monkeypatch.setattr(publisher.subprocess, 'run', lambda *a,**k: next(replies))
    with pytest.raises(ValueError,match='Ambiguous'):
        publisher.release()


def test_unreadable_drafts_preserve_unknown_state(monkeypatch):
    replies = iter([SimpleNamespace(returncode=1,stdout=b'{"status":"404"}'),
                    SimpleNamespace(returncode=1,stdout=b'{"status":"403"}')])
    monkeypatch.setattr(publisher.subprocess, 'run', lambda *a,**k: next(replies))
    with pytest.raises(RuntimeError,match='reconcile'):
        publisher.release()
