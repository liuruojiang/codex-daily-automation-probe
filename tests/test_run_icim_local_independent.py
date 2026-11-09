"""IC/IM authorization is independent of all microcap consumer/state files."""
import copy
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import run_icim_local_independent as independent


@pytest.mark.parametrize('check_window', [True, False])
def test_independent_command_runs_real_child_and_preserves_window_guard(tmp_path, monkeypatch, check_window):
    run = independent.IndependentRun.__new__(independent.IndependentRun)
    run.out, run.day = tmp_path, '2026-11-09'
    checked = []
    monkeypatch.setattr(independent.shared, 'require_window', checked.append)
    record = run.command('probe', [sys.executable, '-B', '-c', 'print("child completed")'],
                         tmp_path, check_window=check_window)
    assert record['exit_code'] == 0 and record['status'] == 'completed'
    assert checked == ([run.day] if check_window else [])
    assert (tmp_path / 'probe.stdout.log').read_text().strip() == 'child completed'


def test_bindings_do_not_read_microcap_consumer_or_state(tmp_path, monkeypatch):
    scripts = tmp_path / 'candidate/scripts'
    scripts.mkdir(parents=True)
    home = tmp_path / 'home'
    (home / 'scripts').mkdir(parents=True)
    for name in ('run_icim_local_independent.py', 'run_local_intraday.py', 'build_local_intraday_report.py', 'build_ic_im_v1_4_digest.py'):
        (scripts / name).write_text('immutable ' + name)
    for name in ('realtime_daily_gate.py', 'daily_microcap_test_worker.py'):
        (home / 'scripts' / name).write_text('generic original helper ' + name)
    monkeypatch.setattr(independent, '__file__', str(scripts / 'run_icim_local_independent.py'))
    monkeypatch.setattr(independent, 'native_module', lambda home: SimpleNamespace(local_import_closure=lambda root, entries: set(entries)))
    before = independent.bindings(home)
    (scripts / 'build_microcap_realtime_digest.py').write_text('unrelated changed consumer')
    assert independent.bindings(home) == before
    (scripts / 'build_ic_im_v1_4_digest.py').write_text('relevant changed consumer')
    assert independent.bindings(home) != before


def successful_evidence():
    counts = dict(tests=1193, failures=0, errors=0, skipped=0)
    return dict(groups={
        'icim47': dict(pass_=True, **{'pass': True}, exit_code=0, official_receipt=dict(counts=counts)),
        'independent5': dict(**{'pass': True}, exit_code=0, counts=dict(counts, tests=105))})


@pytest.mark.parametrize('case', ['missing5', 'skip', 'timeout', 'nonzero', 'empty'])
def test_unqualified_group_evidence_cannot_release(case):
    evidence = successful_evidence()
    if case == 'missing5': evidence['groups'].pop('independent5')
    if case == 'skip': evidence['groups']['icim47']['official_receipt']['counts']['skipped'] = 1
    if case == 'timeout': evidence['groups']['independent5']['timed_out'] = True
    if case == 'nonzero': evidence['groups']['icim47']['exit_code'] = 1
    if case == 'empty': evidence['groups']['independent5']['counts']['tests'] = 0
    with pytest.raises(ValueError):
        independent.verify_group_evidence(evidence)


def test_native_complete_groups_are_accepted():
    independent.verify_group_evidence(successful_evidence())


@pytest.mark.parametrize('has_result', [False, True])
def test_nonzero_runner_only_continues_with_current_completed_artifact(tmp_path, monkeypatch, has_result):
    run = independent.IndependentRun.__new__(independent.IndependentRun)
    run.out = tmp_path
    independent.shared.write(tmp_path / 'icim_realtime.command.json', dict(status='completed', exit_code=1))
    if has_result:
        (tmp_path / 'icim').mkdir()
        (tmp_path / 'icim/diagnostic_report.md').write_text('Original partial research information')
    def failed(*args, **kwargs):
        raise RuntimeError('icim_realtime exit 1')
    monkeypatch.setattr(independent.shared.Run, 'command', failed)
    if has_result:
        record = run.command('icim_realtime', [], tmp_path)
        assert record['exit_code'] == run.partial_runner_exit == 1
        assert independent.shared.read(tmp_path / 'icim_realtime.command.json')['exit_code'] == 1
    else:
        with pytest.raises(RuntimeError): run.command('icim_realtime', [], tmp_path)


def test_failed_partial_consumer_keeps_original_diagnostic_without_signal_synthesis(tmp_path, monkeypatch):
    run = independent.IndependentRun.__new__(independent.IndependentRun)
    run.out, run.day, run.partial_runner_exit = tmp_path, '2026-11-06', 1
    (tmp_path / 'icim').mkdir()
    original = b'Original diagnostic: missing next option contract.'
    (tmp_path / 'icim/diagnostic_report.md').write_bytes(original)
    def failed(*args, **kwargs): raise ValueError('Original consumer cannot render partial shape')
    monkeypatch.setattr(independent.shared.Run, 'render', failed)
    result = run.render('icim', [])
    assert result['status'] == 'partial' and result['runner_exit_code'] == 1
    assert (tmp_path / 'icim_diagnostic_report/diagnostic_report.md').read_bytes() == original
    manifest = independent.shared.read(tmp_path / 'icim_diagnostic_report/manifest.json')
    assert manifest['signal_fields_synthesized'] is False and manifest['status'] == 'partial'
