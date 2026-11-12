"""Resume only publication of the exact ZIP earned by cloud run 43149241199."""
import hashlib
import importlib.util
import json
import argparse
import os
from pathlib import Path
import subprocess
import urllib.request
import zipfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--transport',type=Path,required=True)
parser.add_argument('--native',type=Path,required=True)
args = parser.parse_args()
BASE = args.transport.resolve()
REPO = 'liuruojiang/codex-daily-automation-probe'
RELEASE = 442481189
TAG = 'microcap-scoped-whole-20261110-a7c1b9ac'
NAME = 'microcap-whole-delivery-state-archive-20261110-m7-observation.zip'
SHA = 'dadbbb46c831db8bd24f5b901242d03be4455f5bf91fdf99b934dc0bc17751ec'
SIZE = 672177030
RUNTIME = 'a7c1b9ac33454be4367880a0989dfc1466640503'
bundle = BASE/'earned-archive'/NAME


def gh(*args):
    return subprocess.check_output(['gh', *args])


def receipt(name):
    paths = list((BASE/'first-run-evidence').rglob(name))
    assert len(paths) == 1, name
    return json.loads(paths[0].read_bytes())


def remote():
    value = json.loads(gh('api', f'repos/{REPO}/releases/{RELEASE}'))
    assert value['id'] == RELEASE and value['tag_name'] == TAG
    assert value['prerelease'] is False
    assert value['upload_url'].split('{')[0] == f'https://uploads.github.com/repos/{REPO}/releases/{RELEASE}/assets'
    return value


def asset_present(value):
    assets = value['assets']
    assert len(assets) <= 1 and all(item['name'] == NAME for item in assets)
    if assets:
        item = assets[0]
        assert item['state'] == 'uploaded' and item['size'] == SIZE and item['digest'] == 'sha256:'+SHA
    return bool(assets)


identity = receipt('archive-identity.json')
assert identity['runtime_sha'] == RUNTIME and identity['sha256'] == SHA and identity['bytes'] == SIZE
assert identity['current_signal_ready'] is False
packed, certified = receipt('native-pack.json'), receipt('recertified-manifest.json')
assert packed['ok'] and packed['release_sha'] == RUNTIME and packed['current_signal_ready'] is False
assert certified['ok'] and certified['release_sha'] == RUNTIME and certified['current_signal_ready'] is False
stage, staged = receipt('p2-native-stage.json'), receipt('p2-original-stage-receipt.json')
source_sha = '3b327f9443105b59615338ca9e8572c07730cd64f77959a07b2af58c5cd4b3c0'
assert stage['status'] == 'native_scoped_sources_staged' and stage['issuer_count'] == 300
assert stage['source_target'] == '2026-11-11' and stage['bundle_sha256'] == source_sha
assert stage['production_pointers_changed'] is False and stage['native_late_adoption_required'] is True
assert stage['final_strategy_delivery'] is False
assert staged['status'] == 'native_sources_staged' and staged['target'] == '2026-11-11'
assert staged['source_bundle_sha256'] == source_sha and len(staged['files']) == 28743
before_stage = receipt('pack-before-source-stage.json')
assert before_stage == {'sha256': SHA, 'bytes': SIZE}
assert bundle.stat().st_size == SIZE
with bundle.open('rb') as stream:
    assert hashlib.file_digest(stream,'sha256').hexdigest() == SHA
assert subprocess.check_output(['git','-C',str(args.native),'rev-parse','HEAD'],text=True).strip() == RUNTIME
native = args.native/'scripts/scoped_archive_source_revalidation.py'
spec = importlib.util.spec_from_file_location('earned_archive_source_contract',native)
contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)
with zipfile.ZipFile(bundle) as archive:
    raw = archive.read('outputs/top100_delivery_manifest.json')
    manifest = json.loads(raw)
    assert {k:v for k,v in manifest.items() if k!='verified_at'} == {k:v for k,v in certified.items() if k!='verified_at'}
    actual = {name:hashlib.sha256(archive.read(name)).hexdigest() for name in contract.BASELINE['economic_csvs']}
assert actual == identity['economic_csvs'] == contract.BASELINE['economic_csvs']
assert manifest[contract.ACTIVE]['data_inventory_sha256'] == contract.BASELINE['state_inventory_sha256']
assert manifest[contract.ACTIVE]['revision'] == contract.REVISION
run = json.loads(gh('api', f'repos/{REPO}/actions/runs/43149241199'))
assert run['head_sha'] == '1d152fbd5fac2607d6f4cc9e50b0a864a0e91a5c' and run['head_branch'] == 'main'
jobs = json.loads(gh('api', f'repos/{REPO}/actions/runs/43149241199/jobs'))['jobs']
for stage in ('Earn the native complete archive certificate without refreshing history',
              'Pack the native-validated archive without claiming a current signal',
              'Read back the earned P2 source package through the exact new native consumer'):
    assert any(step['name']==stage and step['conclusion']=='success' for job in jobs for step in job['steps'])
before = remote()
(BASE/'release-recovery-before.json').write_text(json.dumps(before,indent=2)+'\n',encoding='utf-8')
if not asset_present(before):
    assert before['draft'] is True
    token = gh('auth','token').decode().strip()
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):
            raise ValueError('Refuse authenticated upload redirect')
    opener = urllib.request.build_opener(NoRedirect())
    try:
        with bundle.open('rb') as stream:
            request = urllib.request.Request(before['upload_url'].split('{')[0]+'?name='+NAME,
                data=stream,method='POST',headers={'Authorization':'Bearer '+token,
                'Content-Type':'application/zip','Content-Length':str(SIZE),'Accept':'application/vnd.github+json'})
            with opener.open(request,timeout=240) as response:
                uploaded = json.load(response)
        (BASE/'upload-recovery-response.json').write_text(json.dumps(uploaded,indent=2)+'\n',encoding='utf-8')
    except Exception as error:
        # No repeat upload after an uncertain response. Reconcile the exact asset.
        current = remote()
        if not asset_present(current):
            raise RuntimeError('Upload acceptance unknown; retain earned archive and reconcile') from None
uploaded_release = remote()
assert asset_present(uploaded_release)
if uploaded_release['draft']:
    gh('api','--method','PATCH',f'repos/{REPO}/releases/{RELEASE}','-F','draft=false')
published = remote()
assert published['draft'] is False and asset_present(published)
(BASE/'release-published.json').write_text(json.dumps(published,indent=2)+'\n',encoding='utf-8')
proof = dict(identity,publication_run=43149241199,publication_recovered_without_recertifying=True,
    source_artifact=13914725127,release_id=RELEASE,asset_id=published['assets'][0]['id'],
    url=published['assets'][0]['browser_download_url'],original_archive_bytes_unchanged=True,
    manifest_sha256=hashlib.sha256(raw).hexdigest(),email_sent=False,orders_placed=False,
    producer_run=43149241199,recovery_run=int(os.environ['GITHUB_RUN_ID']))
(BASE/'publication-recovery.json').write_text(json.dumps(proof,indent=2)+'\n',encoding='utf-8')
print(json.dumps(proof))
