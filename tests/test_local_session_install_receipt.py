from hashlib import sha256
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from inspect_local_session_install import member_readback


def write(path, value):
    raw = json.dumps(value).encode()
    path.write_bytes(raw)
    return sha256(raw).hexdigest()


@pytest.mark.parametrize('changed', [None, 'producer_strategy_sha', 'next_trade_date',
                                    'period_package_id', 'source', 'period_bytes'])
def test_readback_matches_machine_handoff(tmp_path, changed):
    package = tmp_path / 'checkpoint'
    package.mkdir()
    period = dict(package_id='period', effective_from='2026-11-27', next_rebalance_date='2026-12-10')
    period_hash = write(package/'period.json', period)
    checkpoint = package/'checkpoint.json'
    checkpoint_hash = write(checkpoint, dict(as_of_date='2026-11-26', next_trade_date='2026-11-27',
                                            strategy_sha='producer', period={'path': 'period.json', 'sha256': period_hash}))
    selected = {'source': 'exact_small_bootstrap_seed', 'archive_sha256': 'source'}
    write(tmp_path/'01.stdout.log', selected)
    receipt = dict(schema='microcap_member_local_checkpoint_readback_v1', native_verify_exit_code=0,
                   execution_strategy_sha='reader', producer_strategy_sha='producer', checkpoint=str(checkpoint),
                   checkpoint_sha256=checkpoint_hash, as_of_date='2026-11-26', next_trade_date='2026-11-27',
                   period_status='current', period_sha256=period_hash, period_package_id=period['package_id'],
                   period_effective_from=period['effective_from'], next_rebalance_date=period['next_rebalance_date'],
                   source=selected)
    if changed == 'source':
        receipt['source'] = {'artifact_id': 15145675086}
    elif changed == 'period_bytes':
        write(package/'period.json', {**period, 'package_id': 'swapped'})
    elif changed:
        receipt[changed] = 'swapped'
    path = tmp_path/'checkpoint_readback.json'
    receipt_hash = write(path, receipt)
    if changed:
        with pytest.raises(ValueError):
            member_readback({'strategy_pin': 'reader'}, path)
    else:
        assert member_readback({'strategy_pin': 'reader'}, path)['sha256'] == receipt_hash
