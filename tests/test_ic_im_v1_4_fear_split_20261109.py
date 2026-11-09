"""Both published metrics stay visible; release proofs remain exact."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import build_ic_im_v1_4_digest as digest


def test_both_metrics_visible_with_actual_dates_and_roles():
    signal = dict(fear_greed_index=18.97, fear_data_date='2026-11-06',
                  fear_data_status='close_previous_session',
                  fear_source_warning='旧版六项情绪18.97，用于网格；新增综合恐贪10.01，数据日2026-11-06，仅观察；页面更新时间2026-11-07T06:19+08:00')
    signal['fear_composite_reference'] = dict(value=10.01, data_date='2026-11-06', role='reference_only',
        page_updated_at='2026-11-07T06:19+08:00', retrieved_at='2026-11-09T14:22:26+08:00',
        selected_source='csv', cross_validation='matched', source_values={'csv':10.01, 'page':10.01})
    value = digest.fear_observation_text(dict(status='ok', market_date='2026-11-09', signals={'IC': signal}))
    for text in ('18.97', '10.01', '2026-11-06', '仅观察', '沿用上一交易日', '2026-11-07T06:19'):
        assert text in value


@pytest.mark.parametrize('fault', [None, 'hash', 'release'])
def test_split_source_proof_is_bound_to_new_manifest(tmp_path, fault):
    raw = (ROOT / 'docs/ic_im_fear_source_split_20261109_manifest.json').read_bytes()
    m = json.loads(raw)
    proof = dict(status='verified', strategy_sha='a'*40, release=m['release'],
                 manifest_sha256=hashlib.sha256(raw).hexdigest(), source_sha256=deepcopy(m['files']),
                 verified_at=datetime.now(timezone.utc).isoformat())
    if fault == 'hash': proof['source_sha256']['ic_im_fear_grid_v1.py'] = '0'*64
    if fault == 'release': proof['release'] = 'unknown-new-source'
    path = tmp_path/'proof.json'; path.write_text(json.dumps(proof),encoding='utf8')
    if fault:
        with pytest.raises(ValueError): digest.source_provenance(str(path), 'a'*40)
    else:
        assert digest.source_provenance(str(path), 'a'*40) == proof
