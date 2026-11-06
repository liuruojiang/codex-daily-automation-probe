import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
from run_microcap_member_local import reusable_checkpoint

def test_superseded_serialization_is_not_reused_or_deleted(tmp_path):
    cp=tmp_path/'checkpoint.json';raw=json.dumps({'history':{'sha256':'old-exact-sha'}}).encode();cp.write_bytes(raw)
    assert reusable_checkpoint(cp,{'superseded_history_sha256':['old-exact-sha']}) is None
    assert cp.read_bytes()==raw

def test_other_completed_checkpoints_remain_reusable(tmp_path):
    cp=tmp_path/'checkpoint.json';cp.write_text(json.dumps({'history':{'sha256':'valid-current-sha'}}))
    assert reusable_checkpoint(cp,{'superseded_history_sha256':['old-exact-sha']})==cp.resolve()
