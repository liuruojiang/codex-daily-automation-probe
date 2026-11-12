"""The one-off archive publisher must reconcile rather than replace assets."""
import importlib.util
import ast
import copy
import json
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


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    import socket
    monkeypatch.setattr(socket.socket, 'connect', lambda *a, **kw: pytest.fail('real network forbidden'))
    monkeypatch.setattr(publisher.time, 'sleep', lambda seconds: None)


def transport_tail():
    """Exercise actual publish statements after unchanged earned-source gates."""
    tree = ast.parse(PATH.read_text(encoding='utf8'))
    original = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'publish')
    start = next(i for i,n in enumerate(original.body) if isinstance(n,ast.Assign)
                 and isinstance(n.value,ast.Call) and isinstance(n.value.func,ast.Name)
                 and n.value.func.id == 'ensure_release')
    node = ast.parse('def run(bundle,evidence,identity,digest,size):\n pass').body[0]
    node.body = original.body[start:]
    scope = dict(publisher.__dict__)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),str(PATH),'exec'),scope)
    return scope['run']


class GitHubModel:
    """Deterministic transport model; no native recertification/data substitute."""
    def __init__(self):
        self.value = None
        self.discoverable = False
        self.creates = self.uploads = self.patches = 0
        self.reads = []
        self.initial_id_404 = 0
        self.lost_create = self.lost_upload = self.lost_patch = False
        self.hold_upload = self.hold_publish = False
        self.read_fault = None
        self.evidence = None

    def response(self, value, status=0):
        return SimpleNamespace(returncode=status, stdout=json.dumps(value).encode())

    def read(self, args, **kwargs):
        endpoint = args[-1]; self.reads.append(endpoint)
        if '/releases/tags/' in endpoint:
            return self.response(self.value) if self.discoverable else self.response({'status':'404'},1)
        if '/releases?' in endpoint:
            return self.response([self.value] if self.discoverable else [])
        assert endpoint == f'repos/{publisher.REPOSITORY}/releases/442481189'
        if self.initial_id_404:
            self.initial_id_404 -= 1
            return self.response({'status':'404'},1)
        value = copy.deepcopy(self.value)
        if self.hold_upload: value['assets'] = []
        if self.hold_publish: value['draft'] = True
        if self.read_fault == 'id': value['id'] += 1
        if self.read_fault == 'tag': value['tag_name'] += '-wrong'
        if self.read_fault == 'prerelease': value['prerelease'] = True
        return self.response(value)

    def write(self, *args, input=None):
        assert args[:3] == ('api','--method',args[2])
        endpoint = args[3]
        body = json.loads(input)
        if args[2] == 'POST':
            assert endpoint == f'repos/{publisher.REPOSITORY}/releases'
            assert (self.evidence/'release-create-intent.json').exists()
            self.creates += 1
            assert self.value is None, 'duplicate create'
            self.value = dict(id=442481189,tag_name=publisher.TAG,prerelease=False,draft=True,assets=[],
                              target_commitish=body['target_commitish'])
            if self.lost_create: raise RuntimeError('accepted create response lost')
        else:
            assert args[2] == 'PATCH' and endpoint.endswith('/442481189')
            assert body == {'draft':False}, 'never retarget a recovered release'
            assert (self.evidence/'release-publish-intent.json').exists()
            self.patches += 1; self.value['draft'] = False
            if self.lost_patch: raise RuntimeError('accepted PATCH response lost')
        return json.dumps(self.value).encode()

    def upload(self, release_id, bundle):
        assert release_id == 442481189
        assert (self.evidence/'release-upload-intent.json').exists()
        self.uploads += 1
        assert not self.value['assets'], 'duplicate upload'
        self.value['assets'] = [dict(id=17,name=publisher.NAME,state='uploaded',size=bundle.stat().st_size,
                                    digest='sha256:'+'a'*64,browser_download_url='https://example.invalid/earned.zip')]
        if self.lost_upload: raise RuntimeError('accepted upload response lost')
        return json.dumps(self.value['assets'][0]).encode()


@pytest.fixture
def model(tmp_path, monkeypatch):
    value = GitHubModel(); value.evidence = tmp_path/'evidence'; value.evidence.mkdir()
    monkeypatch.setattr(publisher.subprocess,'run',value.read)
    monkeypatch.setattr(publisher,'gh',value.write)
    monkeypatch.setattr(publisher,'upload_asset',value.upload)
    monkeypatch.setenv('GITHUB_SHA','b'*40)
    return value


def publish_transport(model):
    bundle = model.evidence/publisher.NAME
    bundle.write_bytes(b'earned archive transport fixture')
    identity = dict(sha256='a'*64,bytes=bundle.stat().st_size)
    return transport_tail()(bundle,model.evidence,identity,'a'*64,bundle.stat().st_size)


def test_created_draft_uses_returned_id_when_tag_and_complete_list_remain_invisible(model):
    model.initial_id_404 = 2
    result = publish_transport(model)
    assert result['url'].endswith('/earned.zip')
    assert (model.creates,model.uploads,model.patches) == (1,1,1)
    assert model.reads.count(f'repos/{publisher.REPOSITORY}/releases/tags/{publisher.TAG}') == 1
    assert sum('/releases?' in path for path in model.reads) == 1
    receipt = json.loads((model.evidence/'release-create-receipt.json').read_bytes())
    assert receipt['release_id'] == 442481189
    assert (model.evidence/'release-create-response.raw').exists()


def test_create_response_lost_keeps_intent_and_cannot_issue_another_create(model):
    model.lost_create = True
    for _ in range(2):
        with pytest.raises(RuntimeError,match='unknown'):
            publish_transport(model)
    assert (model.creates,model.uploads,model.patches) == (1,0,0)
    model.discoverable = True
    publish_transport(model)
    assert (model.creates,model.uploads,model.patches) == (1,1,1)


@pytest.mark.parametrize('operation', ('upload','patch'))
def test_unknown_upload_or_patch_is_only_reconciled_with_reads_never_reissued(model,operation):
    setattr(model,'lost_'+operation,True)
    setattr(model,'hold_'+('publish' if operation=='patch' else 'upload'),True)
    for _ in range(2):
        with pytest.raises(RuntimeError,match='unknown'):
            publish_transport(model)
    assert model.creates == model.uploads == 1
    assert model.patches == (1 if operation=='patch' else 0)
    setattr(model,'hold_'+('publish' if operation=='patch' else 'upload'),False)
    publish_transport(model)
    assert (model.creates,model.uploads,model.patches) == (1,1,1)


@pytest.mark.parametrize('fault', ('id','tag','prerelease'))
def test_exact_id_response_must_keep_original_identity_before_any_asset_write(model,fault):
    model.read_fault = fault
    with pytest.raises(ValueError,match='identity'):
        publish_transport(model)
    assert (model.creates,model.uploads,model.patches) == (1,0,0)


def test_saved_creation_receipt_retains_original_target_when_workflow_sha_changes(model,monkeypatch):
    model.read_fault = 'tag'
    with pytest.raises(ValueError): publish_transport(model)
    model.read_fault = None
    monkeypatch.setenv('GITHUB_SHA','c'*40)
    publish_transport(model)
    intent = json.loads((model.evidence/'release-create-intent.json').read_bytes())
    assert intent['request']['target_commitish'] == model.value['target_commitish'] == 'b'*40
    assert (model.creates,model.uploads,model.patches) == (1,1,1)


@pytest.mark.parametrize('fault', ('intent','receipt_id','receipt_hash'))
def test_durable_creation_evidence_cannot_be_rebound(model,fault):
    model.read_fault = 'tag'
    with pytest.raises(ValueError): publish_transport(model)
    model.read_fault = None
    path = model.evidence/('release-create-intent.json' if fault=='intent' else 'release-create-receipt.json')
    record = json.loads(path.read_bytes())
    if fault == 'intent': record['binding']['archive_sha256'] = 'f'*64
    elif fault == 'receipt_id': record['release_id'] += 1
    else: record['response_sha256'] = '0'*64
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError): publish_transport(model)
    assert (model.creates,model.uploads,model.patches) == (1,0,0)


def test_exact_id_transient_read_retry_is_bounded_and_cannot_recreate(model):
    model.initial_id_404 = 100
    for _ in range(2):
        with pytest.raises(RuntimeError,match='Exact release ID'):
            publish_transport(model)
    assert model.creates == 1 and model.uploads == model.patches == 0
    assert sum(path.endswith('/442481189') for path in model.reads) == 8


def test_existing_published_asset_needs_no_create_upload_or_patch(model):
    model.value = dict(id=442481189,tag_name=publisher.TAG,prerelease=False,draft=False,assets=[],
                       target_commitish='d'*40)
    bundle = model.evidence/publisher.NAME; bundle.write_bytes(b'earned archive transport fixture')
    model.value['assets'] = [dict(name=publisher.NAME,state='uploaded',size=bundle.stat().st_size,
        digest='sha256:'+'a'*64,browser_download_url='https://example.invalid/earned.zip')]
    model.discoverable = True
    publish_transport(model)
    assert (model.creates,model.uploads,model.patches) == (0,0,0)
    assert model.value['target_commitish'] == 'd'*40


@pytest.mark.parametrize('status', (301,302,307,308))
def test_streaming_upload_never_follows_an_authenticated_redirect(tmp_path,monkeypatch,status):
    connections = []; headers = []; chunks = []
    class Connection:
        def __init__(self,host,**kwargs): connections.append(host)
        def putrequest(self,method,path):
            assert method=='POST' and '/releases/442481189/assets?' in path
        def putheader(self,key,value): headers.append((key,value))
        def endheaders(self): pass
        def send(self,body): chunks.append(body)
        def getresponse(self):
            return SimpleNamespace(status=status,read=lambda:b'redirect is not followed')
        def close(self): pass
    monkeypatch.setattr(publisher.http.client,'HTTPSConnection',Connection)
    monkeypatch.setattr(publisher,'gh',lambda *args,**kwargs:b'unit-test-token')
    bundle = tmp_path/publisher.NAME; bundle.write_bytes(b'one exact upload')
    with pytest.raises(RuntimeError,match='reconcile'):
        publisher.upload_asset(442481189,bundle)
    assert connections == ['uploads.github.com'] and b''.join(chunks) == bundle.read_bytes()
    assert ('Authorization','Bearer unit-test-token') in headers
    assert all(b'unit-test-token' not in path.read_bytes() for path in tmp_path.iterdir())
