from pathlib import Path
import copy
import pytest
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import import_microcap_producer_inputs as reader

ROOT=Path(__file__).resolve().parents[1]
P4='ae439a1eec4dae5729483f43356473bcb9b1dff1'

def test_original_p2_sources_read_only_through_exact_p4_profile():
    old=reader.producer_profile('microcap_local_producer_p2_20261112.json',reader.P2_RUNTIME)['calculation_sources']
    new=reader.producer_profile('microcap_local_producer_runtime.json',P4)['calculation_sources']
    original=copy.deepcopy(old)
    result=reader.exact_p2_successor(reader.P2_RUNTIME,old,new,P4)
    assert result['reader_runtime']==P4 and old==original
    changed=dict(new);changed['scripts/share_change_source.py']='0'*64
    with pytest.raises(ValueError):reader.exact_p2_successor(reader.P2_RUNTIME,old,changed,P4)
