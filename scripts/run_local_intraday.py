"""Post-gate local research orchestration; never sends mail or restores state.

Every child is an existing formal entrypoint. A durable per-day claim prevents
unknown or successful runs being repeated. An operator must inspect a failed
claim before using the existing, bounded component-recovery procedures.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time as day_time
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from zoneinfo import ZoneInfo

CN = ZoneInfo('Asia/Shanghai')


def clock():
    return datetime.now(CN)


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write(path, value):
    path = Path(path)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    tmp.replace(path)


def require_window(signal_day, now=None):
    now = now or clock()
    if now.tzinfo is None:
        raise ValueError('intraday clock requires a timezone')
    now = now.astimezone(CN)
    if now.date().isoformat() != signal_day or not day_time(14, 15) <= now.time() < day_time(15):
        raise ValueError('new intraday actions require the same session, 14:15 <= now < 15:00')


def isolated_output(out, protected):
    out = Path(out).resolve()
    for p in protected:
        p = Path(p).resolve()
        if out.is_relative_to(p) or p.is_relative_to(out):
            raise ValueError('output overlaps production, prepared or resident state')
    # Neither existing evidence nor an interrupted run is overwritten.
    out.mkdir(parents=True, exist_ok=False)
    return out


def ledger_hashes(state):
    paths = [Path(state) / 'latest.json', Path(state) / 'migration_record.json']
    paths += sorted((Path(state) / 'journal').glob('*.json'))
    if len(paths) <= 2:
        raise ValueError('empty production ledger')
    return {str(p.relative_to(state)): sha(p) for p in paths}


def load_readiness(home):
    scripts = Path(home) / 'scripts'
    sys.path.insert(0, str(scripts))
    spec = importlib.util.spec_from_file_location('_local_native_readiness', scripts / 'microcap_readiness.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_installation(home):
    """Read the installed bundle only from the separately certified pointer.

    The source list is fixed at deployment, not reconstructed from whatever
    modules happen to be present when a daily run starts.
    """
    pointer = read(Path(home) / 'local_intraday_install.json')
    bundle = Path(pointer['bundle']).resolve()
    if Path(__file__).resolve().parent != bundle / 'scripts':
        raise ValueError('run the certified local delivery installation')
    manifest_path = bundle / 'manifest.json'
    if sha(manifest_path) != pointer['manifest_sha256']:
        raise ValueError('installed bundle manifest changed')
    manifest = read(manifest_path)
    required = {'scripts/run_local_intraday.py', 'scripts/build_local_intraday_report.py'}
    if manifest.get('schema') != 1 or not required <= set(manifest.get('files', {})):
        raise ValueError('incomplete installed delivery source manifest')
    for name, expected in manifest['files'].items():
        p = (bundle / name).resolve()
        if not p.is_relative_to(bundle) or p.is_symlink() or sha(p) != expected:
            raise ValueError('installed delivery source changed: ' + name)
    return pointer


def claim_day(home, signal_day, out):
    directory = Path(home) / 'runtime' / 'local_intraday_claims'
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (signal_day + '.json')
    with path.open('x', encoding='utf8') as stream:
        json.dump({'status': 'running_or_unknown', 'pid': os.getpid(),
                   'started_at': clock().isoformat(), 'out': str(out)}, stream)
    return path


@contextmanager
def microcap_writer_lock(root):
    path = Path(root) / 'outputs/top100_delivery.lock'
    payload = str(os.getpid()).encode('ascii')
    with path.open('xb') as stream:
        stream.write(payload)
    try:
        yield
    finally:
        if path.read_bytes() != payload:
            raise ValueError('delivery lock owner changed; preserve unknown owner')
        path.unlink()


class Run:
    def __init__(self, home, gate, resident, ledger_source, out, python=sys.executable):
        self.home, self.gate, self.resident = map(lambda p: Path(p).resolve(), (home, gate, resident))
        self.installation = verify_installation(self.home)
        self.ledger_source = Path(ledger_source).resolve()
        self.config = read(self.home / 'scripts/realtime_daily_gate.json')
        self.micro = Path(self.config['microcap_source']).resolve()
        self.icim = Path(self.config['icim_source']).resolve()
        active = read(self.home / 'realtime_daily_active.json')
        self.out = isolated_output(out, [self.micro, self.icim, active['prepared'], self.resident])
        self.python = str(Path(python).resolve())
        self.native = load_readiness(self.home)
        calendar = read(self.gate)['calendar']
        self.day = calendar['today']
        self.anchor = calendar['latest_completed_session']
        self.next_day = calendar['next_session']
        require_window(self.day)
        self.authorization = self.native.authorize_intraday(self.micro, date.fromisoformat(self.anchor), self.gate)
        self.claim = claim_day(self.home, self.day, self.out)
        self.gate_sha = sha(self.gate)
        write(self.out / 'authorization.json', self.authorization)

    def authorize(self):
        require_window(self.day)
        if verify_installation(self.home) != self.installation:
            raise ValueError('installation changed during intraday run')
        if sha(self.gate) != self.gate_sha:
            raise ValueError('bound gate changed')
        self.native.authorize_intraday(self.micro, date.fromisoformat(self.anchor), self.gate)

    def command(self, label, args, cwd, env=None, *, check_window=True):
        if check_window:
            require_window(self.day)
        record = {'command': [str(a) for a in args], 'cwd': str(cwd),
                  'started_at': clock().isoformat(), 'status': 'running_or_unknown'}
        record_path = self.out / (label + '.command.json')
        if record_path.exists():
            raise ValueError('command already attempted; inspect the original receipt: ' + label)
        write(record_path, record)
        tick = time.monotonic()
        child_env = dict(os.environ)
        child_env.update({'PYTHONDONTWRITEBYTECODE': '1'})
        child_env.update(env or {})
        with (self.out / (label + '.stdout.log')).open('xb') as stdout, (self.out / (label + '.stderr.log')).open('xb') as stderr:
            child = subprocess.Popen(record['command'], cwd=cwd, env=child_env,
                stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            record['pid'] = child.pid
            write(record_path, record)
            # A cutoff does not justify killing an unknown native writer or
            # starting another request. Wait for the actual terminal process.
            code = child.wait()
        record.update(status='completed', exit_code=code, ended_at=clock().isoformat(),
                      elapsed_seconds=time.monotonic() - tick)
        write(record_path, record)
        if code:
            raise RuntimeError(f'{label} exit {code}; no automatic retry, inspect original child/actor state')
        return record

    def py(self, script, *args):
        return [self.python, '-B', '-X', 'utf8', str(script), *map(str, args)]

    def render(self, strategy, inputs):
        output = self.out / (strategy + '_report')
        args = self.py(Path(__file__).with_name('build_local_intraday_report.py'),
                       '--mode', 'intraday', '--signal-day', self.day, '--anchor-day', self.anchor,
                       '--next-session', self.next_day, '--out', output, *inputs)
        # IC/IM may finish after 15:00 only with all validated input snapshots
        # before cutoff. Renderer/native consumer owns that distinction.
        self.command(strategy + '_render', args, Path(__file__).parent,
                     check_window=(strategy != 'icim'))
        event = {'event': 'report_ready_not_published', 'strategy': strategy,
                 'summary': str(output / 'summary.md'), 'report': str(output / 'report.md'),
                 'manifest': str(output / 'manifest.json'), 'created_at': clock().isoformat(),
                 'actual_publication': False,
                 'publication_deadline': self.day + 'T15:00:00+08:00' if strategy == 'microcap' else None}
        if strategy == 'microcap':
            require_window(self.day)
        write(self.out / (strategy + '_ready.json'), event)
        print(json.dumps(event, ensure_ascii=False), flush=True)
        return event

    def run_icim(self):
        self.authorize()
        ledger_source_sha = sha(self.ledger_source)
        fields = ('candidate', 'archive', 'artifact_metadata', 'run_metadata')
        source = read(self.ledger_source)
        if set(source) != set(fields) or any(not isinstance(source[k], str) for k in fields):
            raise ValueError('official candidate specification must contain only four source paths')
        dry = self.out / 'icim_dry_run'
        cmd = self.py(self.home / 'scripts/adopt_icim_official_ledger.py', '--strategy-root', self.icim)
        for key in fields:
            cmd += ['--' + key.replace('_', '-'), source[key]]
        cmd += ['--audit-dir', str(dry)]
        self.command('icim_dry_run', cmd, self.icim)
        report = read(dry / 'adoption_result.json')
        if (report.get('exit_code') != 0 or report.get('applied') is not False
            or report.get('before', {}).get('verified_day') != self.anchor
            or report.get('expected_completed_day') != self.anchor):
            raise ValueError('local ledger requires separate official preparation before intraday execution')
        state = self.icim / 'runtime/ic_im_v1_4_r1'
        before = ledger_hashes(state)
        if read(state / 'latest.json')['digest'] != report['before']['digest']:
            raise ValueError('production ledger changed after official dry-run')
        self.authorize()
        if sha(self.ledger_source) != ledger_source_sha:
            raise ValueError('official candidate specification changed')
        result_dir = self.out / 'icim'
        self.command('icim_realtime', self.py(self.icim / 'run_ic_im_v1_4_github_digest.py',
            '--state-dir', state, '--out-dir', result_dir, '--mode', 'realtime',
            '--expected-market-date', self.day), self.icim,
            {'ICIM_STATE_DIR': str(state), 'ICIM_REQUIRE_MIGRATION': '1'})
        if ledger_hashes(state) != before:
            raise ValueError('intraday runner changed the canonical ledger; report withheld')
        return self.render('icim', ['--icim-result', str(result_dir / 'result.json')])

    def run_microcap(self):
        self.authorize()
        worker = self.home / 'scripts/microcap_resident_readiness.py'
        self.command('resident_intraday', self.py(worker, 'intraday', '--run-dir', self.resident,
                     '--gate', self.gate, '--timeout', '1800'), self.micro)
        response = read(self.out / 'resident_intraday.stdout.log')
        readiness = response.get('readiness', {})
        if (response.get('pass') is not True or readiness.get('pass') is not True
            or readiness.get('source_and_shared_inputs_unchanged') is not True
            or readiness.get('expected_date') != self.anchor
            or readiness.get('mode') != 'intraday'
            or sha(response['readiness_path']) != response.get('readiness_sha256')
            or readiness.get('authorization', {}).get('gate_sha256') != self.gate_sha):
            raise ValueError('resident result or native gate binding is incomplete')
        self.command('resident_owner_before_signals', self.py(worker, 'status', '--run-dir', self.resident), self.micro)
        with microcap_writer_lock(self.micro):
            result = self.microcap_signals(response)
        # A cleanup problem is separate from a validated and already announced report.
        try:
            self.command('resident_shutdown', self.py(worker, 'shutdown', '--run-dir', self.resident),
                         self.micro, check_window=False)
            result['resident_cleanup'] = 'shutdown_command_completed'
        except Exception as exc:
            result['resident_cleanup'] = 'failed_or_unknown'
            result['resident_cleanup_error'] = str(exc)
        return result

    def microcap_signals(self, response):
        inputs = ['--microcap-readiness', response['readiness_path']]
        for version in ('2_0', '2_3', '2_5'):
            self.authorize()
            stem = 'microcap_top100_mom16_biweekly_live_v' + version
            self.command('microcap_v' + version, self.py(self.micro / (stem + '.py'), '实时信号'), self.micro,
                         {'TOP100_REALTIME_REQUIRE_STATE': '1'})
            source = self.micro / 'outputs' / (stem + '_realtime_signal.csv')
            dest = self.out / source.name
            data = source.read_bytes()
            dest.write_bytes(data)
            if sha(source) != sha(dest):
                raise ValueError('signal changed during readback')
            inputs += ['--microcap-csv', 'v' + version.replace('_', '.') + '=' + str(dest)]
        return self.render('microcap', inputs)

    def execute(self):
        results = {}
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {pool.submit(fn): label for label, fn in
                       [('icim', self.run_icim), ('microcap', self.run_microcap)]}
            for future in as_completed(futures):
                label = futures[future]
                try:
                    results[label] = {'status': 'ready_not_published', 'result': future.result()}
                except Exception as exc:
                    results[label] = {'status': 'failed_or_unknown', 'error': str(exc)}
                    print(json.dumps({'event': 'component_failed_or_unknown', 'strategy': label,
                                      'error': str(exc)}, ensure_ascii=False), flush=True)
        receipt = {'status': 'terminal', 'completed_at': clock().isoformat(), 'out': str(self.out),
                   'results': results, 'smtp_invoked': False, 'actual_publication': False}
        write(self.out / 'run.json', receipt)
        write(self.claim, receipt)
        return 0 if all(r['status'] == 'ready_not_published' for r in results.values()) else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('automation-home', 'gate', 'resident', 'ledger-source', 'out'):
        parser.add_argument('--' + name, required=True, type=Path)
    parser.add_argument('--python', default=sys.executable)
    args = parser.parse_args(argv)
    if sys.flags.optimize:
        parser.error('optimized Python is not permitted')
    return Run(args.automation_home, args.gate, args.resident, args.ledger_source, args.out, args.python).execute()


if __name__ == '__main__':
    raise SystemExit(main())
