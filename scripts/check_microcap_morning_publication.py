"""Reconcile the morning source publication without collecting or publishing."""
from __future__ import annotations

import argparse
import base64
from datetime import datetime
import json
from pathlib import Path
import re
import sys
from urllib.parse import quote, urlsplit
from zipfile import BadZipFile

from dispatch_microcap_recovery import REPO, ReadFailure, gh_api
from maintain_microcap_local import CACHE, RUNTIME, bound
from microcap_local_due import BJ, morning_target, plan, pool_binding
from microcap_scoped_inputs import MAX_BYTES, read_bundle, transport_files
from microcap_scoped_roster import roster_archive
from microcap_scoped_settings import read_roster, validate_settings
from prepare_microcap_local import atomic, encode, sha, writer_lock
from restore_microcap_scoped_inputs import validate_config


CONTROLLERS = ('maintain_microcap_local.py', 'microcap_local_workers.py',
               'prepare_microcap_local.py', 'microcap_local_http.py')
# The frozen 11/26 run predates the cold-authority repair in the fixed controller.
# Its exact result and the successor repair were independently accepted together.
REVIEWED_COLD_METADATA = (
    'db6efefc002beb77124b580627b40a81065dd020b3595cc25b05ef1bf69eadb7',
    '84bdb250f2afa3aba0de9b690f45bfd92f33c0a88f255fe861d221145b25afd0',
    '3d8f9e9aa17d24186ce51226ca66029334d014bec9b181f676cbf055cd378c13',
)


def verify_orchestration(context, completion_raw):
    recorded = context['orchestrator_sources']
    if set(recorded) != set(CONTROLLERS) or context['orchestrator_sha256'] != recorded[CONTROLLERS[0]]:
        raise ValueError('Completed source run has no exact maintenance controller binding')
    for name in CONTROLLERS:
        current = Path(__file__).with_name(name).read_bytes().replace(b'\r\n', b'\n')
        if recorded[name] in (sha(current), sha(current.replace(b'\n', b'\r\n'))):
            continue
        if (name == CONTROLLERS[0]
                and (sha(completion_raw), recorded[name], sha(current)) == REVIEWED_COLD_METADATA):
            continue
        raise ValueError('Maintenance controller changed and its affected source stages need revalidation: ' + name)


def remote_contract(api):
    head = api('GET', f'repos/{REPO}/commits/main')['sha']
    if re.fullmatch('[0-9a-f]{40}', head) is None:
        raise ValueError('Invalid automation main revision')

    def read(name):
        value = api('GET', f'repos/{REPO}/contents/config/{name}?ref={head}')
        return json.loads(base64.b64decode(value['content'], validate=False))

    return (head, validate_config(read('microcap_local_maintenance.json')),
            validate_settings(read('microcap_scoped_rebalance.json')))


def published_asset(api, pointer):
    _, tag, filename = urlsplit(pointer['url']).path.rsplit('/', 2)
    release = api('GET', f'repos/{REPO}/releases/tags/{quote(tag, safe="")}')
    assets = [asset for asset in release['assets'] if asset['name'] == filename]
    if release['tag_name'] != tag or release['draft'] is not False or len(assets) != 1:
        raise ValueError('Configured source release is not uniquely published')
    asset = assets[0]
    if (asset['state'] != 'uploaded' or asset['browser_download_url'] != pointer['url']
            or asset['digest'] != 'sha256:' + pointer['sha256']
            or type(asset['id']) is not int or asset['id'] <= 0
            or type(asset['size']) is not int or not 0 < asset['size'] <= MAX_BYTES):
        raise ValueError('Configured source asset identity differs')
    return asset


def inspect(root, state, now, sessions, *, pool=None, bundle=None, api=gh_api, roster_reader=read_roster):
    root, state = Path(root), Path(state)
    try:
        with writer_lock(state / 'writer.lock'):
            return inspect_locked(root, state, now, sessions, pool, bundle, api, roster_reader)
    except OSError:
        return dict(status='source_writer_active_or_unavailable', target=morning_target(now, sessions),
                    action='follow_existing_writer', publication_action='wait_for_source_completion')


def inspect_locked(root, state, now, sessions, pool, bundle, api, roster_reader):
    target = morning_target(now, sessions)
    if target is None:
        return dict(status='not_due', target=None, action='no_collection',
                    publication_action='wait_for_source_completion')
    base = dict(target=target, automatic_on_wakeup=False)
    try:
        head, host, scoped = remote_contract(api)
    except (KeyError, OSError, ReadFailure, TypeError, ValueError) as exc:
        return dict(base, status='remote_contract_unconfirmed', action='wait_for_verified_remote_contract',
                    publication_action='hold_publication', publication_error=str(exc))
    base.update(configuration_sha=head, owner=host['owner'], scoped_enabled=scoped['enabled'])
    if host['owner'] != 'local' or not scoped['enabled']:
        return dict(base, status='local_maintenance_not_enabled', action='no_collection',
                    publication_action='hold_publication')
    item = scoped['weekly_roster']
    try:
        binding = pool_binding(state, target, pool)
        checked_on = now.astimezone(BJ).date().isoformat()
        summary, _ = roster_archive(roster_reader(item), item['sha256'], checked_on)
        if summary['date'] != item['date']:
            raise ValueError('Cloud weekly roster date differs from its pointer')
        base.update(weekly_roster_sha256=item['sha256'], remote_scope_sha256=summary['candidates_sha256'],
                    local_scope_sha256=binding[1] if binding else None)
        if binding is None or binding[1] != summary['candidates_sha256']:
            raise ValueError('Weekly roster used by the cloud handoff differs from the local preparation')
    except (KeyError, OSError, ReadFailure, TypeError, ValueError, BadZipFile) as exc:
        return dict(base, status='weekly_roster_needs_reconciliation', action='reconcile_weekly_roster',
                    publication_action='hold_publication', publication_error=str(exc))
    files = {}
    result = {**base, **plan(now, sessions, state, root, pool, files_out=files)}
    if result['status'] != 'already_complete':
        return {**result, 'publication_action': 'wait_for_source_completion'}

    directory = state / 'runs' / target
    try:
        completion_path = directory / 'result.json'
        completion_raw = completion_path.read_bytes()
        completion = json.loads(completion_raw)
        if (completion['scope_sha256'] != binding[1]
                or set(completion['symbols']) != set(binding[0])
                or completion['context']['runtime_sha'] != RUNTIME):
            raise ValueError('Completed sources differ from the current exact roster or runtime')
        verify_orchestration(completion['context'], completion_raw)
        files = transport_files(files)
        for name, digest in completion['context']['native_sources'].items():
            if sha(bound(root, name).read_bytes().replace(b'\r\n', b'\n')) != digest:
                raise ValueError('Completed source code binding differs')
        pointer = host['scoped_inputs']
        if pointer is None or pointer['target'] < target:
            return {**result, 'publication_action': 'reconcile_existing_export_or_release'}
        if pointer['target'] != target or pointer['runtime_sha'] != RUNTIME:
            raise ValueError('Formal source target or native runtime differs')
        asset = published_asset(api, pointer)
        identity = dict(target=target, runtime_sha=RUNTIME, producer_commit=pointer['producer_commit'],
                        producer_context=completion['context'], symbols=completion['symbols'],
                        scope_sha256=binding[1], files=files,
                        master_sha256=sha(bound(root, CACHE + 'security_master.csv').read_bytes()))
        proof = dict(schema='microcap_morning_source_publication_v1', pointer=pointer,
                     asset_id=asset['id'], size=asset['size'], source_identity_sha256=sha(encode(identity)))
        receipt = directory / ('morning-publication-' + pointer['sha256'] + '.json')
        if receipt.exists():
            if json.loads(receipt.read_bytes()) != proof:
                raise ValueError('Published source receipt differs from current closed sources')
        elif bundle is None:
            return {**result, 'publication_action': 'verify_existing_asset_only',
                    'source': pointer, 'asset_id': asset['id']}
        else:
            if Path(bundle).stat().st_size != asset['size']:
                raise ValueError('Saved source archive size differs from the published asset')
            manifest, _, _ = read_bundle(bundle, target, pointer['sha256'])
            if any(manifest.get(key) != expected for key, expected in identity.items()):
                raise ValueError('Published manifest differs from the completed morning sources')
            if completion_path.read_bytes() != completion_raw:
                raise ValueError('Source completion changed during publication readback')
            atomic(receipt, encode(proof))
        return {**result, 'publication_action': 'no_publication', 'source': pointer,
                'asset_id': asset['id'], 'publication_receipt': str(receipt)}
    except (KeyError, OSError, ReadFailure, TypeError, ValueError, BadZipFile) as exc:
        return {**result, 'publication_action': 'resolve_unconfirmed_publication',
                'publication_error': str(exc)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--state-root', type=Path, required=True)
    parser.add_argument('--pool', type=Path)
    parser.add_argument('--bundle', type=Path, help='Existing local copy of the published source ZIP')
    args = parser.parse_args()
    sys.path.insert(0, str(args.root.resolve()))
    from scripts.exchange_calendar import sessions_for_day

    now = datetime.now(BJ)
    report = inspect(args.root, args.state_root, now, sessions_for_day(now.date()),
                     pool=args.pool, bundle=args.bundle)
    print(json.dumps(report))
    return int(report['status'] in {'remote_contract_unconfirmed', 'weekly_roster_needs_reconciliation'}
               or report['publication_action'] == 'resolve_unconfirmed_publication')


if __name__ == '__main__':
    raise SystemExit(main())
