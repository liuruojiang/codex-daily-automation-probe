"""Publish one exact native-earned source-only archive; no refresh or email."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

REPOSITORY = 'liuruojiang/codex-daily-automation-probe'
RUNTIME = '14e9a9fa6f0487f1d5547598435089c43f2f9fd1'
TAG = 'microcap-scoped-whole-20261110-14e9a9fa'
NAME = 'microcap-whole-delivery-state-archive-20261110-14e9a9fa.zip'


def gh(*args):
    return subprocess.check_output(['gh', *args])


def release():
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
        return None
    value = json.loads(result.stdout)
    if value.get('tag_name') != TAG or value.get('prerelease') is not False:
        raise ValueError('Existing release identity differs')
    return value


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
    current = release()
    if current is None:
        target = os.environ['GITHUB_SHA']
        gh('release', 'create', TAG, '--repo', REPOSITORY, '--draft', '--target', target,
           '--title', 'Microcap verified 10-Nov archive with exact issuer coverage',
           '--notes', 'Source-only native recertification. Economic history preserved. No current signal or email.')
        current = release()
    (evidence/'release-before.json').write_text(json.dumps(current, indent=2)+'\n', encoding='utf-8')
    if not check_assets(current, digest, size):
        if current.get('draft') is not True:
            raise ValueError('Refuse adding an unreviewed asset to a published release')
        gh('release', 'upload', TAG, str(bundle), '--repo', REPOSITORY)
    uploaded = release()
    if not check_assets(uploaded, digest, size):
        raise ValueError('Published archive asset is absent')
    (evidence/'release-uploaded.json').write_text(json.dumps(uploaded, indent=2)+'\n', encoding='utf-8')
    if uploaded['draft']:
        gh('release', 'edit', TAG, '--repo', REPOSITORY, '--draft=false')
    published = release()
    if published.get('draft') is not False or not check_assets(published, digest, size):
        raise ValueError('Archive publication was not confirmed')
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
