from pathlib import Path
import subprocess
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import microcap_local_workers as workers
from prepare_microcap_local import encode


@pytest.fixture
def another_writer(tmp_path):
    # Real separate native-data process, no model or market-data calls.
    code = ("import sys\nfrom pathlib import Path\n"
            f"sys.path.insert(0,{str(SCRIPTS)!r})\n"
            "from prepare_microcap_local import writer_lock\n"
            "with writer_lock(Path(sys.argv[1])):\n"
            " print('locked',flush=True)\n sys.stdin.readline()\n")
    processes = []
    def start(path):
        process = subprocess.Popen([sys.executable, '-B', '-c', code, str(path)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        assert process.stdout.readline().strip() == 'locked'
        processes.append(process)
        return process
    yield start
    for process in processes:
        if process.poll() is None: process.communicate('\n', timeout=10)
        else: process.wait(timeout=10)
        assert process.returncode == 0


def test_price_queries_are_serialized_across_real_processes(tmp_path, another_writer):
    process = another_writer(tmp_path / 'price-worker.lock')
    with pytest.raises(TimeoutError, match='still busy'):
        with workers.price_slot(tmp_path, deadline_seconds=0.05): pass
    process.communicate('\n', timeout=10)
    with workers.price_slot(tmp_path, deadline_seconds=0.05): pass


def test_orphan_worker_prevents_new_parent_dispatch(tmp_path, another_writer, monkeypatch):
    another_writer(tmp_path / 'worker-slot-0.lock')
    monkeypatch.setattr(workers.subprocess, 'Popen', lambda *a,**k: pytest.fail('duplicate worker dispatch'))
    with pytest.raises(OSError): workers.run_workers(tmp_path / 'runtime', tmp_path, [], {'target':'2026-11-06'})
    assert not (tmp_path / 'runs/2026-11-06/worker-context.json').exists()


def test_worker_rejects_context_outside_dedicated_run(tmp_path):
    with pytest.raises(ValueError, match='escaped'):
        workers.worker(tmp_path / 'runtime', tmp_path / 'state', 0, tmp_path / 'outside.json')


def test_worker_rejects_changed_code_before_any_native_import(tmp_path):
    path = tmp_path / 'runs/2026-11-06/context.json';path.parent.mkdir(parents=True)
    path.write_bytes(encode({'context': {'target':'2026-11-06','orchestrator_sources':
                                       {'microcap_local_workers.py':'0'*64}},'symbols':['000001']}))
    with pytest.raises(ValueError, match='code changed'):
        workers.worker(tmp_path / 'absent-runtime', tmp_path, 0, path)
