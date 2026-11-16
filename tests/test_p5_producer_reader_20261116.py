from pathlib import Path
import sys,copy
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import import_microcap_producer_inputs as r

def test_exact_p5_retains_p2_reader_and_original_identity():
 old=r.producer_profile('microcap_local_producer_p2_20261112.json',r.P2_RUNTIME)['calculation_sources']
 current=r.producer_profile('microcap_local_producer_runtime.json',r.P5_RUNTIME)['calculation_sources']
 saved=copy.deepcopy(old)
 assert r.exact_p2_successor(r.P2_RUNTIME,old,current,r.P5_RUNTIME)['reader_runtime']==r.P5_RUNTIME
 assert old==saved
 broken=dict(current);broken['scripts/share_change_source.py']='0'*64
 with pytest.raises(ValueError):r.exact_p2_successor(r.P2_RUNTIME,old,broken,r.P5_RUNTIME)
