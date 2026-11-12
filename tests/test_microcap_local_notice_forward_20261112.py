"""Local 09:00 wiring with original D11 proof/archive bytes; no D12 promotion.

The full Native read-only closure verifier and Git transport are explicit
software boundaries here. Anchor checks, protected role decisions, journals,
CAS, byte preservation and the actual maintenance main path execute for real.
An intentional BaseException stops main immediately before Native refresh.
"""
import base64
from copy import deepcopy
from datetime import date, datetime
import gzip
import hashlib
import json
from pathlib import Path
import socket
import sys
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import maintain_microcap_local as local

AB214 = 'ab214c20d92ca9bbda1dde7e447bb153e7f59028'
M8 = '95d896fe58cc7cb84c0fcb7bd76534f62c9430f4'
POINTER = '.microcap_index_cache/st_notice_archive_pointer.json'
PREFIX = '.microcap_index_cache/st_notice_sources/'
FRESHNESS = PREFIX + 'freshness.json'
PROXY = 'outputs/wind_microcap_top_100_biweekly_thursday_16y_cached.csv'
FORMAL = 'outputs/top100_delivery_manifest.json'
ADV_PATH = PREFIX + 'refresh_20261111T143208796316Z_2e7092817919/proof.json'
SELF_PATH = PREFIX + 'refresh_20261112T010443196822Z_1489267b2428/proof.json'
ADV_SHA = '1afcedebbb8e0e2adbe1a529ffbb7b1d3728ec15fe91c0f8550c3daa1bfe672c'
SELF_SHA = '651db4226dbdb200222be74017b7ca332adb838bd57bec213f18670a809a256b'
FLOOR = datetime.fromisoformat('2026-11-12T01:00:00+00:00')


def put(root, name, raw):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw if isinstance(raw, bytes) else local.encode(raw))
    return path


def files(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}


@pytest.fixture
def case(tmp_path, monkeypatch):
    def no_http(*_a, **_k):
        pytest.fail('This local selector test cannot collect market data')
    monkeypatch.setattr(socket, 'create_connection', no_http)
    monkeypatch.setattr(socket.socket, 'connect', no_http)
    state = tmp_path / 'local-state'
    root = state / 'runtime'
    protected = tmp_path / 'actual-local-AB214'
    root.mkdir(parents=True)
    protected.mkdir()
    packet_raw = (ROOT / 'tests/fixtures/microcap_local_notice_forward_20261112.json.gz').read_bytes()
    assert hashlib.sha256(packet_raw).hexdigest() == 'bdd7acbfeb1fc7c79676f2977a93bfaab1a7403ae657b7dcecdb5855d61acabd'
    packet = json.loads(gzip.decompress(packet_raw))
    immutable = {}
    for name, obj in packet['objects'].items():
        raw = base64.b64decode(obj['base64'], validate=True)
        assert len(raw) == obj['bytes'] and local.sha(raw) == obj['sha256']
        immutable[name] = raw
    for filename, name, digest in (('advance-proof.json', ADV_PATH, ADV_SHA), ('self-proof.json', SELF_PATH, SELF_SHA)):
        raw = (ROOT / 'tests/fixtures/microcap_notice_prefix_20261111' / filename).read_bytes()
        assert local.sha(raw) == digest
        immutable[name] = raw
    advance = json.loads(immutable[ADV_PATH])
    self_review = json.loads(immutable[SELF_PATH])
    start, end = advance['previous_authority'], advance['authority']
    assert self_review['previous_authority'] == self_review['authority'] == end
    for name, raw in immutable.items():
        put(root, name, raw)
    put(root, POINTER, dict(manifest_path=end[0], manifest_sha256=end[1]))
    put(root, FRESHNESS, immutable[SELF_PATH])
    protected_pointer = local.encode(dict(manifest_path=start[0], manifest_sha256=start[1]))
    put(protected, POINTER, protected_pointer)
    put(protected, start[0], immutable[start[0]])
    # Explicit software reference, independently bound to its actual local
    # protected object. It is never an economic/history production result.
    reference = b'date,nav\n2026-11-05,1.0\n'
    put(state, 'reference-proxy.csv', reference)
    put(protected, PROXY, reference)
    put(protected, FORMAL, dict(ok=True, status='complete', scope='whole_workspace_delivery',
        expected_date='2026-11-05', release_sha=AB214,
        inputs={POINTER: local.sha(protected_pointer)}, artifacts={PROXY: local.sha(reference)}))
    symbols = [f'{n:06d}' for n in range(1, 301)]
    bootstrap = dict(status='complete', runtime_sha=local.LEGACY_P2, source_root=str(protected),
        symbols=symbols, initialized_symbols=symbols, failures={},
        reference_consumed_through='2026-11-05', reference_proxy_sha256=local.sha(reference),
        files={POINTER: local.sha(protected_pointer), PROXY: local.sha(reference),
               start[0]: local.sha(immutable[start[0]])})
    put(state, 'bootstrap.json', bootstrap)
    master = b'symbol\n' + b''.join((s + '\n').encode() for s in symbols)
    put(root, local.CACHE + 'security_master.csv', master)
    candidates = dict(candidates=[dict(symbol=s, current_member=i < 100) for i, s in enumerate(symbols)])
    candidate_raw = local.encode(candidates)
    put(tmp_path, 'pool/candidates.json', candidate_raw)
    pool = put(tmp_path, 'pool/latest.json', dict(schema='microcap_local_screen_preparation_v1',
        status='PREPARATION_VERIFIED', date='2026-11-11', run_directory=str(tmp_path / 'pool'),
        candidates_sha256=local.sha(candidate_raw)))
    c = SimpleNamespace(root=root, protected=protected, state=state, pool=pool, symbols=symbols,
        immutable=immutable, advance=advance, self_review=self_review, start=start, end=end,
        out=state / 'selection', protected_role=AB214, native_calls=[], git_calls=[])

    def git_transport(argv, **kw):
        c.git_calls.append(list(map(str, argv)))
        assert argv[:2] == ['git', '-C'], 'Only explicit software Git identity transport is admitted'
        where = Path(argv[2]).resolve()
        if argv[3:] == ['rev-parse', 'HEAD']:
            value = local.RUNTIME if where == root else c.protected_role if where == protected else None
            assert value is not None, 'Unknown Git root'
            value += '\n'
        elif argv[3:5] == ['diff', '--name-only']:
            value = ''
        else:
            pytest.fail('Unexpected external process in local-only software fixture: ' + repr(argv))
        return value if kw.get('text') or kw.get('encoding') else value.encode()

    monkeypatch.setattr(local.subprocess, 'check_output', git_transport)
    monkeypatch.setattr(local, 'validate_runtime', lambda *_a, **_k: local.RUNTIME)

    def native(runtime, data, mode, profile_root=None, requested_target=None):
        assert Path(runtime).resolve() == root and Path(data).resolve() == root and mode == 'forward'
        c.native_calls.append((str(runtime), mode, requested_target))
        pointer = json.loads((root / POINTER).read_bytes())
        authority = [pointer['manifest_path'], pointer['manifest_sha256']]
        raw = (root / FRESHNESS).read_bytes()
        digest = local.sha(raw)
        if digest not in (SELF_SHA, ADV_SHA):
            raise ValueError('Software Native boundary rejects an unknown active original')
        result = dict(mode='forward', native_runtime=local.RUNTIME, authority=authority,
            authority_day='2026-11-11', requested_target=requested_target, active_sha256=digest,
            old_prefix_unchanged=True, original_clocks_retained=True, state='noop',
            reason='same_target_or_original_advancing_receipt')
        if requested_target > '2026-11-11' and digest == SELF_SHA:
            for name, original in immutable.items():
                assert (root / name).read_bytes() == original
            result.update(state='select', selected_spec=dict(path=ADV_PATH, sha256=ADV_SHA),
                active_spec=dict(path=SELF_PATH, sha256=SELF_SHA), selected_previous_authority=start,
                source_files={name: local.sha(raw) for name, raw in immutable.items()})
        elif digest == ADV_SHA:
            result['selected_spec'] = dict(path=ADV_PATH, sha256=ADV_SHA)
        return result
    c.native = native
    c.protected_before = files(protected)
    c.bootstrap_before = (state / 'bootstrap.json').read_bytes()
    c.reference_before = (state / 'reference-proxy.csv').read_bytes()
    return c


def select(c, **options):
    return local.select_local_notice_forward(root=c.root, protected_root=c.protected, state=c.state,
        symbols=c.symbols, target=options.pop('target', '2026-11-12'), out=c.out,
        native=options.pop('native', c.native), **options)


def originals_unchanged(c):
    assert files(c.protected) == c.protected_before
    assert (c.state / 'bootstrap.json').read_bytes() == c.bootstrap_before
    assert (c.state / 'reference-proxy.csv').read_bytes() == c.reference_before
    assert all((c.root / name).read_bytes() == raw for name, raw in c.immutable.items())


def test_local_original_AB214_anchor_selects_advance_without_claiming_M8_or_D12(case):
    c = case
    pointer = (c.root / POINTER).read_bytes()
    result = select(c)
    assert (c.root / FRESHNESS).read_bytes() == c.immutable[ADV_PATH]
    assert (c.root / POINTER).read_bytes() == pointer
    assert (c.out / 'original-active-self.json').read_bytes() == c.immutable[SELF_PATH]
    originals_unchanged(c)
    text = json.dumps(result)
    assert AB214 in text and M8 not in text
    assert result['target_freshness_granted'] is False
    assert result['current_signal_ready'] is False
    assert c.native_calls and all(call[2] == '2026-11-12' for call in c.native_calls)


def test_same_day_self_review_is_kept_and_forward_can_resume_without_rewriting(case, monkeypatch):
    c = case
    before = (c.root / FRESHNESS).read_bytes()
    select(c, target='2026-11-11')
    assert (c.root / FRESHNESS).read_bytes() == before
    select(c)
    first = (c.root / FRESHNESS).read_bytes()
    def forbid_rewrite(*_a, **_k):
        pytest.fail('An already selected original must not be written twice')
    import microcap_notice_prefix as prefix
    monkeypatch.setattr(prefix, '_write_pair_member', forbid_rewrite)
    select(c)
    assert (c.root / FRESHNESS).read_bytes() == first
    originals_unchanged(c)


@pytest.mark.parametrize('fault', ['reference', 'protected_pointer', 'bootstrap_owner', 'coverage', 'formal_binding'])
def test_local_selection_requires_its_original_bootstrap_and_formal_anchor(case, fault):
    c = case
    if fault == 'reference':
        put(c.state, 'reference-proxy.csv', b'changed software reference')
    elif fault == 'protected_pointer':
        put(c.protected, POINTER, dict(manifest_path=c.end[0], manifest_sha256=c.end[1]))
    elif fault in ('bootstrap_owner', 'coverage'):
        record = json.loads((c.state / 'bootstrap.json').read_bytes())
        if fault == 'bootstrap_owner':
            record['source_root'] = str(c.protected.parent / 'other-owner')
        else:
            record['initialized_symbols'].pop()
        put(c.state, 'bootstrap.json', record)
    else:
        formal = json.loads((c.protected / FORMAL).read_bytes())
        formal['artifacts'][PROXY] = 'f' * 64
        put(c.protected, FORMAL, formal)
    before = (c.root / FRESHNESS).read_bytes(), (c.root / POINTER).read_bytes()
    with pytest.raises((ValueError, RuntimeError)):
        select(c)
    assert ((c.root / FRESHNESS).read_bytes(), (c.root / POINTER).read_bytes()) == before
    assert c.native_calls == []


def test_unknown_local_protected_role_never_borrows_a_cloud_consumer_identity(case):
    c = case
    c.protected_role = 'f' * 40
    before = files(c.root)
    with pytest.raises((ValueError, RuntimeError)):
        select(c)
    assert files(c.root) == before and c.native_calls == []
    c.protected_role = M8
    result = select(c)
    assert files(c.root) == before and c.native_calls == []
    assert 'not_applicable' in json.dumps(result)


def test_unrecognised_active_proof_and_mutated_native_role_never_write(case):
    c = case
    put(c.root, FRESHNESS, dict(schema='explicit software unknown active original', target='2026-11-11'))
    before = files(c.root)
    with pytest.raises((ValueError, RuntimeError)):
        select(c)
    assert files(c.root) == before
    put(c.root, FRESHNESS, c.immutable[SELF_PATH])
    def wrong_role(*args, **kw):
        result = c.native(*args, **kw)
        result['native_runtime'] = local.LEGACY_P2
        return result
    before = files(c.root)
    with pytest.raises((ValueError, RuntimeError)):
        select(c, native=wrong_role)
    assert files(c.root) == before


def test_local_foreign_pointer_during_native_read_is_never_overwritten(case):
    c = case
    active = (c.root / FRESHNESS).read_bytes()
    foreign = local.encode(dict(manifest_path=PREFIX + 'other/archive.json', manifest_sha256='f' * 64))
    def race(*args, **kw):
        result = c.native(*args, **kw)
        put(c.root, POINTER, foreign)
        return result
    with pytest.raises((ValueError, RuntimeError)):
        select(c, native=race)
    assert (c.root / POINTER).read_bytes() == foreign
    assert (c.root / FRESHNESS).read_bytes() == active
    originals_unchanged(c)


def test_lost_active_write_response_is_reconciled_once_then_reused(case, monkeypatch):
    c = case
    import microcap_notice_prefix as prefix
    original = prefix._write_pair_member
    writes = []
    def lost(path, raw):
        writes.append(Path(path))
        original(path, raw)
        raise OSError('Explicit software response loss after the completed atomic replace')
    monkeypatch.setattr(prefix, '_write_pair_member', lost)
    result = select(c)
    assert result['write_response_lost_reconciled'] is True
    assert len(writes) == 1
    assert (c.root / FRESHNESS).read_bytes() == c.immutable[ADV_PATH]
    second = select(c)
    assert second['selection_reconciled'] is True and len(writes) == 1
    originals_unchanged(c)


def test_unknown_prior_write_without_selected_bytes_is_not_repeated(case, monkeypatch):
    c = case
    c.out.mkdir(parents=True)
    pending = local.encode(dict(schema='explicit software unknown prior selection', status='intent'))
    (c.out / 'selection-intent.json').write_bytes(pending)
    before = files(c.root)
    import microcap_notice_prefix as prefix
    monkeypatch.setattr(prefix, '_write_pair_member', lambda *_a: pytest.fail('Unknown write cannot be repeated'))
    for _ in range(2):
        with pytest.raises((ValueError, RuntimeError), match='unknown'):
            select(c)
        assert files(c.root) == before
        assert (c.out / 'selection-intent.json').read_bytes() == pending
    originals_unchanged(c)


class StopAtNativeRefresh(BaseException):
    """No Native refresh or later worker is actually executed in this test."""


def test_actual_local_main_selects_original_before_entering_the_native_refresh(case, monkeypatch):
    c = case
    prefix = ModuleType('scripts')
    prefix.__path__ = []
    monkeypatch.setitem(sys.modules, 'scripts', prefix)
    native_modules = {
        'exchange_calendar': dict(latest_completed_session=lambda: date(2026, 11, 12), is_trading_day=lambda _d: True),
        'st_notice_refresh': dict(FRESHNESS=FRESHNESS, freshness_source_files=lambda _r: [ADV_PATH, SELF_PATH]),
        'st_history_scope': dict(source_closure=lambda _r: {}, resolve_archive_reference=lambda _r: c.end),
        'stock_input_contract': dict(source_fingerprint=lambda _r: {}),
    }
    seen = []
    def stop_refresh(**kwargs):
        seen.append(('refresh', (c.root / FRESHNESS).read_bytes()))
        assert kwargs['target'] == '2026-11-12'
        assert seen[-1][1] == c.immutable[ADV_PATH], 'The old local path entered Native with the later self proof'
        raise StopAtNativeRefresh()
    native_modules['st_notice_refresh']['refresh_notice_archive'] = stop_refresh
    for name, attrs in native_modules.items():
        mod = ModuleType('scripts.' + name)
        mod.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, 'scripts.' + name, mod)
    strategy = ModuleType('microcap_top100_mom16_biweekly_live_v2_0')
    strategy.base_mod = SimpleNamespace(freq_mod=SimpleNamespace(ROOT=c.root))
    monkeypatch.setitem(sys.modules, strategy.__name__, strategy)
    workers = ModuleType('microcap_local_workers')
    workers.run_workers = lambda *_a, **_k: pytest.fail('No source worker belongs to this entry-point test')
    monkeypatch.setitem(sys.modules, workers.__name__, workers)
    monkeypatch.setattr(local, 'observation_floor', lambda: FLOOR)
    monkeypatch.setattr(local, 'select_notice_generation', lambda *_a: None)
    actual = local.select_local_notice_forward
    def actual_with_software_native(**kwargs):
        assert kwargs['writer_locked'] is True, 'Main already owns the writer lock'
        assert Path(kwargs['protected_root']).resolve() == c.protected
        seen.append(('selector', kwargs['target']))
        return actual(**dict(kwargs, native=c.native))
    monkeypatch.setattr(local, 'select_local_notice_forward', actual_with_software_native)
    with pytest.raises(StopAtNativeRefresh):
        local.main(['run', '--root', str(c.root), '--protected-root', str(c.protected),
            '--state-root', str(c.state), '--pool', str(c.pool), '--as-of', '2026-11-12'])
    assert [item[0] for item in seen] == ['selector', 'refresh']
    originals_unchanged(c)
    assert (c.root / POINTER).read_bytes() == local.encode(dict(manifest_path=c.end[0], manifest_sha256=c.end[1]))
