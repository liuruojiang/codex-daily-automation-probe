"""Publish one exact native-earned source-only archive; no refresh or email."""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import sys
import time
from urllib.parse import quote
import zipfile

REPOSITORY = 'liuruojiang/codex-daily-automation-probe'
RUNTIME = '14e9a9fa6f0487f1d5547598435089c43f2f9fd1'
TAG = 'microcap-scoped-whole-20261110-14e9a9fa'
NAME = 'microcap-whole-delivery-state-archive-20261110-14e9a9fa.zip'


def gh(*args, input=None):
    return subprocess.check_output(['gh', *args], input=input, stderr=subprocess.PIPE)


def release_identity(value, release_id=None):
    if (not isinstance(value, dict) or type(value.get('id')) is not int or value['id'] <= 0
            or (release_id is not None and value['id'] != release_id)
            or value.get('tag_name') != TAG or value.get('prerelease') is not False
            or type(value.get('draft')) is not bool or not isinstance(value.get('assets'), list)):
        raise ValueError('Existing release identity differs')
    return value


def release(release_id=None):
    if release_id is not None:
        if type(release_id) is not int or release_id <= 0:
            raise ValueError('Invalid exact release ID')
        for attempt in range(4):
            result = subprocess.run(['gh', 'api', f'repos/{REPOSITORY}/releases/{release_id}'],
                                    capture_output=True)
            if not result.returncode:
                return release_identity(json.loads(result.stdout), release_id)
            try:
                status = str(json.loads(result.stdout).get('status'))
            except (ValueError, AttributeError):
                status = None
            if status not in (None, '404', '429', '500', '502', '503', '504') or attempt == 3:
                raise RuntimeError('Exact release ID lookup failed; reconcile before any repeat write')
            time.sleep(0.2)
    result = subprocess.run(['gh', 'api', f'repos/{REPOSITORY}/releases/tags/{TAG}'],
                            capture_output=True)
    if result.returncode:
        # Distinguish an actual missing tag from authentication/network failure.
        try:
            missing = str(json.loads(result.stdout).get('status')) == '404'
        except (ValueError, AttributeError):
            missing = False
        if not missing:
            raise RuntimeError('Release lookup failed; reconcile before any write')
        # The tag endpoint omits an unpublished draft. Discover existing drafts
        # completely before a create, so retrying publication cannot create a
        # second release after the first create succeeded.
        matches = []
        for page in range(1, 101):
            listed = subprocess.run(['gh', 'api', f'repos/{REPOSITORY}/releases?per_page=100&page={page}'],
                                    capture_output=True)
            if listed.returncode:
                raise RuntimeError('Draft release lookup failed; reconcile before any write')
            items = json.loads(listed.stdout)
            if not isinstance(items, list):
                raise RuntimeError('Draft release lookup is incomplete')
            matches.extend(item for item in items if isinstance(item, dict) and item.get('tag_name') == TAG)
            if len(items) < 100:
                break
        else:
            raise RuntimeError('Draft release lookup exceeds its complete discovery budget')
        if len(matches) > 1:
            raise ValueError('Ambiguous existing release; refuse duplicate creation')
        if not matches:
            return None
        value = matches[0]
    else:
        value = json.loads(result.stdout)
    return release_identity(value)


def binding(identity):
    return dict(repository=REPOSITORY, runtime_sha=RUNTIME, tag=TAG, asset_name=NAME,
                archive_sha256=identity['sha256'], archive_bytes=identity['bytes'])


def save_json(path, value):
    path.write_text(json.dumps(value, indent=2)+'\n', encoding='utf-8')


def write_once(evidence, role, intent, action):
    """An unacknowledged write is not permission for another write."""
    path = evidence/(role+'-intent.json')
    if path.exists():
        if json.loads(path.read_bytes()) != intent:
            raise ValueError('Saved publication intent identity differs')
        return None
    if (evidence/(role+'-response.raw')).exists() or (evidence/(role+'-unknown.json')).exists():
        raise RuntimeError('Publication response lacks its original intent; reconcile before any write')
    with path.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(intent, indent=2)+'\n')
    try:
        raw = action()
    except Exception as error:
        save_json(evidence/(role+'-unknown.json'), dict(outcome='unknown', error_type=type(error).__name__))
        return None  # Only resource readback may resolve this; never retry it.
    (evidence/(role+'-response.raw')).write_bytes(raw)
    return raw


def ensure_release(evidence, identity):
    intent_path = evidence/'release-create-intent.json'
    response_path = evidence/'release-create-response.raw'
    receipt_path = evidence/'release-create-receipt.json'
    if intent_path.exists():
        intent = json.loads(intent_path.read_bytes())
        request = intent.get('request', {})
        # Resume the original target; a newer recovery workflow SHA must never
        # retarget or recreate an already accepted draft.
        if (intent.get('binding') != binding(identity) or intent.get('schema') != 'github_release_create_intent_v1'
                or not isinstance(request, dict)
                or request != create_request(request.get('target_commitish'))):
            raise ValueError('Saved release creation intent identity differs')
        if receipt_path.exists():
            receipt = json.loads(receipt_path.read_bytes())
            if (type(receipt.get('release_id')) is not int or receipt['release_id'] <= 0
                    or receipt.get('intent_sha256') != hashlib.sha256(intent_path.read_bytes()).hexdigest()
                    or not response_path.exists()
                    or receipt.get('response_sha256') != hashlib.sha256(response_path.read_bytes()).hexdigest()):
                raise ValueError('Saved creation receipt binding differs')
            value = release_identity(json.loads(response_path.read_bytes()), receipt.get('release_id'))
        elif response_path.exists():
            value = release_identity(json.loads(response_path.read_bytes()))
        else:
            value = release()
            if value is None:
                raise RuntimeError('Release creation outcome unknown; reconcile saved intent before any repeat write')
            response_path.write_bytes(json.dumps(value).encode())
        save_json(receipt_path, dict(schema='github_release_create_receipt_v1', release_id=value['id'],
            intent_sha256=hashlib.sha256(intent_path.read_bytes()).hexdigest(),
            response_sha256=hashlib.sha256(response_path.read_bytes()).hexdigest()))
        return release(value['id'])
    if receipt_path.exists() or response_path.exists() or (evidence/'release-create-unknown.json').exists():
        raise RuntimeError('Creation evidence lacks its original intent; reconcile before any write')
    current = release()
    if current is not None:
        return current
    request = create_request(os.environ['GITHUB_SHA'])
    intent = dict(schema='github_release_create_intent_v1', binding=binding(identity), request=request)
    raw = write_once(evidence, 'release-create', intent,
        lambda: gh('api', '--method', 'POST', f'repos/{REPOSITORY}/releases', '--input', '-',
                   input=json.dumps(request).encode()))
    if raw is None:
        return ensure_release(evidence, identity)  # The saved intent forbids another POST.
    value = release_identity(json.loads(raw))
    if value['draft'] is not True:
        raise ValueError('New release was not created as a draft')
    save_json(receipt_path, dict(schema='github_release_create_receipt_v1', release_id=value['id'],
        intent_sha256=hashlib.sha256(intent_path.read_bytes()).hexdigest(),
        response_sha256=hashlib.sha256(raw).hexdigest()))
    return release(value['id'])


def create_request(target):
    if not isinstance(target, str) or re.fullmatch('[0-9a-f]{40}', target) is None:
        raise ValueError('Release target commit differs')
    return dict(tag_name=TAG, target_commitish=target, draft=True, prerelease=False,
        name='Microcap verified 10-Nov archive with exact issuer coverage',
        body='Source-only native recertification. Economic history preserved. No current signal or email.')


def upload_asset(release_id, bundle):
    """Stream to the fixed uploads host; HTTPSConnection never follows redirects."""
    token = gh('auth', 'token').decode().strip()
    if not token:
        raise RuntimeError('GitHub authentication is unavailable')
    connection = http.client.HTTPSConnection('uploads.github.com', timeout=60,
                                              context=ssl.create_default_context())
    try:
        connection.putrequest('POST', f'/repos/{REPOSITORY}/releases/{release_id}/assets?name={quote(NAME, safe="")}')
        for key, value in (('Authorization', 'Bearer '+token), ('Accept', 'application/vnd.github+json'),
                           ('X-GitHub-Api-Version', '2022-11-28'), ('Content-Type', 'application/zip'),
                           ('Content-Length', str(bundle.stat().st_size))):
            connection.putheader(key, value)
        connection.endheaders()
        with bundle.open('rb') as stream:
            while chunk := stream.read(1024*1024):
                connection.send(chunk)
        response = connection.getresponse()
        raw = response.read()
        if response.status != 201:
            raise RuntimeError('Archive upload not acknowledged; reconcile before any repeat write')
        return raw
    finally:
        connection.close()


def confirmed_release(release_id, digest, size, published=False):
    for attempt in range(4):
        value = release(release_id)
        if check_assets(value, digest, size) and (not published or value['draft'] is False):
            return value
        if attempt != 3:
            time.sleep(0.2)
    raise RuntimeError('Archive publication state unknown; reconcile before any repeat write')


def check_assets(value, digest, size):
    items = value['assets']
    if any(item.get('name') != NAME for item in items) or len(items) > 1:
        raise ValueError('Unexpected release assets; preserve for review')
    if items:
        item = items[0]
        if (item.get('state') != 'uploaded' or item.get('size') != size
                or item.get('digest') != 'sha256:' + digest):
            raise ValueError('Existing asset differs; refuse overwrite')
    return bool(items)


def publish(root, bundle, evidence):
    root, bundle, evidence = map(lambda p: Path(p).resolve(), (root, bundle, evidence))
    evidence.mkdir(parents=True, exist_ok=True)
    if bundle.name != NAME:
        raise ValueError('Unexpected archive name')
    if subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip() != RUNTIME:
        raise ValueError('Exact native source pin differs')
    sys.path.insert(0, str(root))
    from scripts.scoped_archive_source_revalidation import ACTIVE, BASELINE, REVISION
    manifest = json.loads((root/'outputs/top100_delivery_manifest.json').read_bytes())
    if (manifest.get('release_sha') != RUNTIME or manifest.get('ok') is not True
            or manifest.get('status') != 'complete' or manifest.get('archive_session') != '2026-11-10'
            or manifest.get('current_signal_ready') is not False
            or manifest[ACTIVE]['revision'] != REVISION
            or manifest[ACTIVE]['data_inventory_sha256'] != BASELINE['state_inventory_sha256']):
        raise ValueError('No exact complete native source-only archive certificate')
    recert = json.loads((evidence/'native-recertification.json').read_bytes())
    packed = json.loads((evidence/'native-pack.json').read_bytes())
    if (recert.get('ok') is not True or packed.get('ok') is not True
            or packed.get('release_sha') != RUNTIME or packed.get('current_signal_ready') is not False):
        raise ValueError('Native recertification/pack did not complete')
    with bundle.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    size = bundle.stat().st_size
    with zipfile.ZipFile(bundle) as archive:
        saved = json.loads(archive.read('outputs/top100_delivery_manifest.json'))
        # Native shared packing supplies its own verified_at while preserving
        # the earned source certificate, data inventory and economic streams.
        if ({k:v for k,v in saved.items() if k != 'verified_at'}
                != {k:v for k,v in manifest.items() if k != 'verified_at'}):
            raise ValueError('Packed native manifest differs')
        csv_hashes = {name: hashlib.sha256(archive.read(name)).hexdigest()
                      for name in BASELINE['economic_csvs']}
    if csv_hashes != BASELINE['economic_csvs']:
        raise ValueError('Economic CSV bytes changed')
    identity = dict(schema='microcap_boundary_archive_publication_v1', runtime_sha=RUNTIME,
                    date='2026-11-10', sha256=digest, bytes=size, economic_csvs=csv_hashes,
                    data_inventory_sha256=BASELINE['state_inventory_sha256'],
                    current_signal_ready=False, email_sent=False, orders_placed=False)
    (evidence/'archive-identity.json').write_text(json.dumps(identity, indent=2)+'\n', encoding='utf-8')
    current = ensure_release(evidence, identity)
    release_id = current['id']
    (evidence/'release-before.json').write_text(json.dumps(current, indent=2)+'\n', encoding='utf-8')
    if not check_assets(current, digest, size):
        if current.get('draft') is not True:
            raise ValueError('Refuse adding an unreviewed asset to a published release')
        intent = dict(schema='github_release_upload_intent_v1', binding=binding(identity), release_id=release_id)
        write_once(evidence, 'release-upload', intent, lambda: upload_asset(release_id, bundle))
    uploaded = confirmed_release(release_id, digest, size)
    (evidence/'release-uploaded.json').write_text(json.dumps(uploaded, indent=2)+'\n', encoding='utf-8')
    if uploaded['draft']:
        intent = dict(schema='github_release_publish_intent_v1', binding=binding(identity), release_id=release_id)
        write_once(evidence, 'release-publish', intent,
            lambda: gh('api', '--method', 'PATCH', f'repos/{REPOSITORY}/releases/{release_id}',
                       '--input', '-', input=b'{"draft":false}'))
    published = confirmed_release(release_id, digest, size, published=True)
    (evidence/'release-published.json').write_text(json.dumps(published, indent=2)+'\n', encoding='utf-8')
    identity['url'] = published['assets'][0]['browser_download_url']
    (evidence/'publication.json').write_text(json.dumps(identity, indent=2)+'\n', encoding='utf-8')
    return identity


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('root', 'bundle', 'evidence'):
        parser.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(publish(args.root, args.bundle, args.evidence)))
