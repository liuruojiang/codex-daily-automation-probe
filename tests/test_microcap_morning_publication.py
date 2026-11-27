import base64
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
import sys
from zipfile import ZipFile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import check_microcap_morning_publication as morning


@pytest.fixture
def publication(tmp_path, monkeypatch):
    state = tmp_path / 'state'
    root = tmp_path / 'runtime'
    target = '2026-11-26'
    symbols = [f'{n:06d}' for n in range(1, 301)]
    def roster(order):
        candidate = morning.encode(dict(candidates=[
            dict(symbol=s, current_member=i < 100) for i, s in enumerate(order)]))
        scope = morning.sha(candidate)
        summary = dict(schema='microcap_local_screen_preparation_v1', status='PREPARATION_VERIFIED',
                       date=target, candidates_sha256=scope, st_reference_sha256=morning.sha(b'code\n'))
        bodies = {'original-summary.json': morning.encode(summary), 'candidates.json': candidate,
                  'st-reference.csv': b'code\n'}
        roster_manifest = dict(schema='microcap_portable_weekly_roster_v1', date=target,
                               original_observation_clocks_preserved=True, final_strategy_delivery=False,
                               files={name: morning.sha(data) for name, data in bodies.items()})
        stream = BytesIO()
        with ZipFile(stream, 'w') as archive:
            for name, data in bodies.items():
                archive.writestr(name, data)
            archive.writestr('manifest.json', morning.encode(roster_manifest))
        return scope, stream.getvalue()

    scope, packed = roster(symbols)
    controller_shas = {name: morning.sha(Path(morning.__file__).with_name(name).read_bytes())
                       for name in morning.CONTROLLERS}
    context = dict(runtime_sha=morning.RUNTIME, native_sources={}, orchestrator_sources=controller_shas,
                   orchestrator_sha256=controller_shas[morning.CONTROLLERS[0]])
    source = morning.CACHE + 'prices_raw/000001.csv'
    files = {source: '2' * 64}
    master = b'original master'
    morning.atomic(root / morning.CACHE / 'security_master.csv', master)
    morning.atomic(state / 'runs' / target / 'result.json', morning.encode(
        dict(scope_sha256=scope, symbols=symbols, context=context)))
    pointer = dict(target=target, runtime_sha=morning.RUNTIME, producer_commit='3' * 40,
                   sha256='4' * 64, url='https://github.com/' + morning.REPO +
                   '/releases/download/microcap-local-sources-fixture/sources.zip')
    archive = tmp_path / 'downloaded.zip'
    archive.write_bytes(b'archive')
    asset = dict(name='sources.zip', id=25, size=7, state='uploaded',
                 browser_download_url=pointer['url'], digest='sha256:' + pointer['sha256'])
    release = dict(tag_name='microcap-local-sources-fixture', draft=False, assets=[asset])
    calls = []
    reads = []
    host = dict(schema='microcap_local_maintenance_host_v1', owner='local', scoped_inputs=pointer)
    weekly = dict(date=target, sha256=morning.sha(packed),
                  url='https://github.com/' + morning.REPO + '/releases/download/fixture/roster.zip')
    scoped = dict(schema='microcap_scoped_rebalance_runtime_v1', enabled=True, runtime_sha='7' * 40,
                  producer_sha=morning.RUNTIME, whole_seed=weekly.copy(), weekly_roster=weekly)
    manifest = dict(target=target, runtime_sha=morning.RUNTIME, producer_commit=pointer['producer_commit'],
                    scope_sha256=scope, producer_context=context, symbols=symbols, files=dict(files),
                    master_sha256=morning.sha(master))

    def api(method, path):
        assert method == 'GET'
        calls.append(path)
        if path.endswith('/commits/main'):
            return dict(sha='5' * 40)
        if '/contents/' in path:
            assert path.endswith('?ref=' + '5' * 40)
            value = scoped if '/microcap_scoped_rebalance.json' in path else host
            return dict(content=base64.b64encode(morning.encode(value)).decode())
        assert path.endswith('/releases/tags/microcap-local-sources-fixture')
        return release

    def plan(*args, files_out=None):
        files_out.update(files)
        return dict(status='already_complete', target=target, action='no_collection')

    def read_bundle(bundle, day, digest):
        reads.append(bundle)
        assert day == target and digest == pointer['sha256']
        return manifest, {}, digest

    monkeypatch.setattr(morning, 'plan', plan)
    monkeypatch.setattr(morning, 'pool_binding', lambda *args: (symbols, scope))
    monkeypatch.setattr(morning, 'read_bundle', read_bundle)
    control = dict(host=host, scoped=scoped, directory=state/'runs'/target, roster=packed,
                   alternate_roster=roster(list(reversed(symbols)))[1])

    def inspect(bundle=None):
        now = datetime.fromisoformat('2026-11-27T11:00:00+08:00')
        return morning.inspect(root, state, now, (now.date(), date(2026, 11, 26)), bundle=bundle, api=api,
                               roster_reader=lambda _: control['roster'])

    return inspect, archive, pointer, release, calls, reads, files, manifest, control


def test_published_source_is_read_once_then_no_collection_or_publication(publication):
    inspect, archive, _, _, calls, reads, _, _, _ = publication
    assert inspect()['publication_action'] == 'verify_existing_asset_only'
    qualified = inspect(archive)
    assert qualified['action'] == 'no_collection'
    assert qualified['publication_action'] == 'no_publication'
    assert Path(qualified['publication_receipt']).is_file()
    assert inspect()['publication_action'] == 'no_publication'
    assert len(calls) == 12 and reads == [archive]


@pytest.mark.parametrize('fault', ['draft', 'digest', 'different_target', 'changed_manifest'])
def test_unverified_or_conflicting_release_never_requests_another_upload(publication, fault):
    inspect, archive, pointer, release, _, _, _, manifest, _ = publication
    if fault == 'draft':
        release['draft'] = True
    elif fault == 'digest':
        release['assets'][0]['digest'] = 'sha256:' + 'f' * 64
    elif fault == 'different_target':
        pointer['target'] = '2026-11-27'
    else:
        manifest['files'] = {}
    assert inspect(archive)['publication_action'] == 'resolve_unconfirmed_publication'


def test_changed_closed_sources_invalidate_saved_publication_proof(publication):
    inspect, archive, _, _, _, _, files, _, _ = publication
    assert inspect(archive)['publication_action'] == 'no_publication'
    files[next(iter(files))] = '6' * 64
    assert inspect()['publication_action'] == 'resolve_unconfirmed_publication'


def test_old_pointer_keeps_collection_complete_and_reconciles_existing_export(publication):
    inspect, _, pointer, _, calls, reads, _, _, _ = publication
    pointer['target'] = '2026-11-25'
    result = inspect()
    assert result['action'] == 'no_collection'
    assert result['publication_action'] == 'reconcile_existing_export_or_release'
    assert len(calls) == 3 and not reads


def test_cloud_scope_divergence_blocks_existing_receipt_without_starting_stock_work(publication):
    inspect, archive, _, _, _, _, _, _, control = publication
    assert inspect(archive)['publication_action'] == 'no_publication'
    control['roster'] = control['alternate_roster']
    control['scoped']['weekly_roster']['sha256'] = morning.sha(control['roster'])
    decision = inspect()
    assert decision['action'] == 'reconcile_weekly_roster'
    assert decision['local_scope_sha256'] != decision['remote_scope_sha256']
    assert decision['publication_action'] == 'hold_publication'


@pytest.mark.parametrize('remote_change', ['cloud_owner', 'scoped_disabled'])
def test_fresh_target_checks_control_before_plan(publication, monkeypatch, remote_change):
    inspect, _, _, _, calls, _, _, _, control = publication
    if remote_change == 'cloud_owner':
        control['host']['owner'] = 'cloud'
    else:
        control['scoped']['enabled'] = False
    def must_not_plan(*args, **kwargs):
        pytest.fail('must not plan writes after a remote ownership change')
    monkeypatch.setattr(morning, 'plan', must_not_plan)
    result = inspect()
    assert result['action'] == 'no_collection'
    assert result['publication_action'] == 'hold_publication'
    assert any('microcap_scoped_rebalance.json?ref=' in call for call in calls)


def test_unsupported_native_transport_role_rejects_reuse_like_export(publication):
    inspect, archive, _, _, _, _, files, _, _ = publication
    assert inspect(archive)['publication_action'] == 'no_publication'
    files[morning.CACHE + 'share_sources/000001/new-vendor.evidence'] = '6' * 64
    with pytest.raises(ValueError, match='no supported transport role'):
        morning.transport_files(files)
    result = inspect()
    assert result['publication_action'] == 'resolve_unconfirmed_publication'
    assert 'no supported transport role' in result['publication_error']


def test_changed_controller_requires_revalidation_before_export_or_reuse(publication):
    import json
    inspect, archive, pointer, _, _, _, _, _, control = publication
    assert inspect(archive)['publication_action'] == 'no_publication'
    result_path = control['directory'] / 'result.json'
    result = json.loads(result_path.read_bytes())
    result['context']['orchestrator_sources']['microcap_local_workers.py'] = '6' * 64
    morning.atomic(result_path, morning.encode(result))
    pointer['target'] = '2026-11-25'
    decision = inspect()
    assert decision['publication_action'] == 'resolve_unconfirmed_publication'
    assert 'microcap_local_workers.py' in decision['publication_error']
