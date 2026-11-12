"""Two native data workers, not AI agents; preserve original provider limits."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

from prepare_microcap_local import atomic, encode, sha, writer_lock
from microcap_local_http import pooled_public_gets


@contextmanager
def price_slot(state, deadline_seconds=120):
    # The native price writer has a process-wide provider lock. Serialize price
    # stages across our two processes; their share/ratio stages remain separate.
    deadline = time.monotonic() + deadline_seconds
    while True:
        guard = writer_lock(Path(state) / 'price-worker.lock')
        try:
            guard.__enter__()
            break
        except OSError:
            if time.monotonic() >= deadline:
                raise TimeoutError('Native price worker is still busy; no duplicate query')
            time.sleep(0.2)
    try:
        yield
    finally:
        guard.__exit__(None, None, None)


def worker(root, state, slot, context_path):
    import maintain_microcap_local as local
    root, state = Path(root).resolve(), Path(state).resolve()
    path = Path(context_path).resolve()
    if not path.is_relative_to(state / 'runs') or slot not in (0, 1):
        raise ValueError('Worker context escaped the dedicated run')
    assignment = json.loads(path.read_bytes())
    context, symbols = assignment['context'], assignment['symbols'][slot::2]
    target = context['target']
    for name, digest in context['orchestrator_sources'].items():
        if sha((Path(__file__).parent / name).read_bytes()) != digest:
            raise ValueError('Data worker code changed after dispatch')
    dispatch_id = assignment.get('dispatch_id')
    if not isinstance(dispatch_id, str) or len(dispatch_id) != 32 or any(c not in '0123456789abcdef' for c in dispatch_id):
        raise ValueError('Data worker requires an exact dispatch identity')
    sys.path.insert(0, str(root))
    import microcap_top100_mom16_biweekly_live_v2_0 as v2
    from scripts.stock_daily_inputs import refresh_daily_inputs
    from scripts.share_change_source import refresh_share_change
    from scripts.stock_return_pipeline import load_return_prices
    from scripts.stock_input_contract import source_fingerprint
    if source_fingerprint(root) != context['native_sources']:
        raise ValueError('Native source pin changed after dispatch')
    master = v2.base_mod.freq_mod.load_security_master()
    baseline = json.loads((state / 'bootstrap.json').read_bytes())
    journal = local.Journal(state / 'runs' / target, root, context)
    status_path = path.parent / f'worker-{slot}.json'
    failures = {}
    with writer_lock(state / f'worker-slot-{slot}.lock'),pooled_public_gets():
        for number, symbol in enumerate(symbols, 1):
            anchor = local.price_seed_anchor(root, symbol, baseline) if symbol in baseline['initialized_symbols'] else target
            def price_action():
                with price_slot(state): refresh_daily_inputs(root=root, symbols=[symbol], as_of=target)
            stages = {}
            stages['price'] = journal.run(symbol + '-price', price_action,
                lambda: local.price_proof(root, symbol, target, master, anchor))
            stages['share'] = journal.run(symbol + '-share',
                lambda: refresh_share_change(root=root, symbol=symbol,
                    cache_path=local.bound(root, local.CACHE + 'share_change/' + symbol + '.csv'),
                    as_of=target, allow_network=True), lambda: local.share_proof(root, symbol, target))
            if stages['price']['status'] == 'complete':
                stages['ratio'] = journal.run(symbol + '-ratio',
                    lambda: load_return_prices(root=root, symbol=symbol,
                        raw_path=local.bound(root, local.CACHE + 'prices_raw/' + symbol + '.csv'),
                        master=master, as_of=target, allow_network=True),
                    lambda: local.ratio_proof(root, symbol, target, master))
            else: stages['ratio'] = {'status': 'waiting_for_price'}
            missing = {key: {k:v for k,v in value.items() if k in ('status','error','error_type','attempt')}
                       for key,value in stages.items() if value['status'] != 'complete'}
            if missing: failures[symbol] = missing
            result = dict(status='running', context=context, dispatch_id=dispatch_id, total=len(symbols), processed=number,
                          complete=number-len(failures), failures=failures, reused_stages=journal.reused)
            atomic(status_path, encode(result))
            stop = path.parent / ('stop-after-stage-' + dispatch_id + '.json')
            if (stop.exists() and json.loads(stop.read_bytes()).get('context_sha256') == sha(encode(context))
                    and json.loads(stop.read_bytes()).get('dispatch_id') == dispatch_id):
                result.update(status='interrupted_after_symbol')
                atomic(status_path, encode(result))
                return 2
        result.update(status='complete', completed_at=datetime.now(timezone.utc).isoformat())
        atomic(status_path, encode(result))
    return 0


def read_worker_status(path):
    # Windows can briefly deny opening a summary during atomic replacement.
    # Retry only access contention; malformed bytes and identity checks still fail.
    for attempt in range(5):
        try:
            return json.loads(path.read_bytes())
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.1)


def run_workers(root, state, symbols, context):
    state = Path(state); directory = state / 'runs' / context['target']
    # An orphaned native worker continues to own its OS lock. A new parent must
    # wait/inspect that process, never steal it or run a second metadata phase.
    for slot in (0, 1):
        with writer_lock(state / f'worker-slot-{slot}.lock'): pass
    context_path = directory / 'worker-context.json'
    dispatch_id = uuid.uuid4().hex
    atomic(context_path, encode(dict(context=context, symbols=symbols, dispatch_id=dispatch_id)))
    processes, streams = [], []
    try:
        for slot in (0, 1):
            # Old run summaries are not counted until this exact context agrees.
            atomic(directory / f'worker-{slot}.json', encode(dict(status='starting', context=context, dispatch_id=dispatch_id,
                   processed=0, complete=0, failures={}, reused_stages=0)))
            stdout = (directory / f'worker-{slot}.stdout.log').open('a', encoding='utf8')
            stderr = (directory / f'worker-{slot}.stderr.log').open('a', encoding='utf8')
            streams += [stdout, stderr]
            processes.append(subprocess.Popen([sys.executable, '-B', '-X', 'utf8', __file__,
                str(root), str(state), str(slot), str(context_path)], stdout=stdout, stderr=stderr))
        last = -1
        while True:
            statuses = [read_worker_status(directory / f'worker-{slot}.json') for slot in (0,1)]
            if any(s['context'] != context or s.get('dispatch_id') != dispatch_id for s in statuses):
                raise ValueError('Worker summary context or dispatch changed')
            processed = sum(s['processed'] for s in statuses)
            failures = {k:v for s in statuses for k,v in s['failures'].items()}
            progress = dict(schema='microcap_scoped_maintenance_v1', status='running', target=context['target'],
                dispatch_id=dispatch_id,
                scope_sha256=context['scope_sha256'], total=300, processed=processed,
                complete=sum(s['complete'] for s in statuses), failures=failures,
                reused_stages=sum(s['reused_stages'] for s in statuses),
                notice_status='pending_parent_readback', production_outputs_changed=False)
            if processed != last:
                atomic(directory / 'progress.json', encode(progress))
                print(f'[scoped-maintenance] {processed}/300 complete={progress["complete"]} failures={len(failures)}', flush=True)
                last = processed
            if all(p.poll() is not None for p in processes): break
            time.sleep(1)
        if any(p.returncode != 0 for p in processes) or processed != 300:
            progress.update(status='interrupted_after_symbol' if all(s['status']=='interrupted_after_symbol' for s in statuses) else 'failed',
                            worker_exit_codes=[p.returncode for p in processes],stopped_at=datetime.now(timezone.utc).isoformat())
            atomic(directory/'progress.json',encode(progress))
            raise RuntimeError('Native data worker stopped; retain its checkpoints and inspect worker logs')
        return progress
    finally:
        # On ordinary parent errors ask for an orderly boundary, then keep the
        # worker ownership visible. Do not terminate a transactional source write.
        if any(p.poll() is None for p in processes):
            atomic(directory / ('stop-after-stage-' + dispatch_id + '.json'),
                   encode(dict(context_sha256=sha(encode(context)), dispatch_id=dispatch_id)))
        for stream in streams: stream.close()


if __name__ == '__main__':
    import os
    os.environ.setdefault('MICROCAP_DAILY_STOCK_PROVIDER', 'tencent')
    os.environ['MICROCAP_SHARE_DATED_WINDOW'] = '1'
    raise SystemExit(worker(Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3]), Path(sys.argv[4])))
