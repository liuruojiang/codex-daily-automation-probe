import base64
from datetime import date, datetime
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import check_microcap_morning_publication as morning


@pytest.fixture
def publication(tmp_path, monkeypatch):
    state = tmp_path / 'state'
    root = tmp_path / 'runtime'
    target = '2026-11-26'
    symbols = [f'{n:06d}' for n in range(1, 301)]
    scope = '1' * 64
    context = dict(runtime_sha=morning.RUNTIME, native_sources={})
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
            host = dict(schema='microcap_local_maintenance_host_v1', owner='local', scoped_inputs=pointer)
            return dict(content=base64.b64encode(morning.encode(host)).decode())
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

    def inspect(bundle=None):
        now = datetime.fromisoformat('2026-11-27T11:00:00+08:00')
        return morning.inspect(root, state, now, (now.date(), date(2026, 11, 26)), bundle=bundle, api=api)

    return inspect, archive, pointer, release, calls, reads, files, manifest


def test_published_source_is_read_once_then_no_collection_or_publication(publication):
    inspect, archive, _, _, calls, reads, _, _ = publication
    assert inspect()['publication_action'] == 'verify_existing_asset_only'
    qualified = inspect(archive)
    assert qualified['action'] == 'no_collection'
    assert qualified['publication_action'] == 'no_publication'
    assert Path(qualified['publication_receipt']).is_file()
    assert inspect()['publication_action'] == 'no_publication'
    assert len(calls) == 9 and reads == [archive]


@pytest.mark.parametrize('fault', ['draft', 'digest', 'different_target', 'changed_manifest'])
def test_unverified_or_conflicting_release_never_requests_another_upload(publication, fault):
    inspect, archive, pointer, release, _, _, _, manifest = publication
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
    inspect, archive, _, _, _, _, files, _ = publication
    assert inspect(archive)['publication_action'] == 'no_publication'
    files[next(iter(files))] = '6' * 64
    assert inspect()['publication_action'] == 'resolve_unconfirmed_publication'


def test_old_pointer_keeps_collection_complete_and_reconciles_existing_export(publication):
    inspect, _, pointer, _, calls, reads, _, _ = publication
    pointer['target'] = '2026-11-25'
    result = inspect()
    assert result['action'] == 'no_collection'
    assert result['publication_action'] == 'reconcile_existing_export_or_release'
    assert len(calls) == 2 and not reads
