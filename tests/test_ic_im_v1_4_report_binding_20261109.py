"""Normal-body attachments must identify the same immutable result."""
import hashlib
import json
from pathlib import Path
import sys
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import build_ic_im_v1_4_digest as digest

def sample(tmp_path):
 payload={'publication_mode':'close_confirmed','verified_day':'2026-11-09','sequence':32,'digest':'a'*64,'report_file':'native.md'}
 text='\n'.join(f'- {label}：`{payload[key]}`' for label,key in [('已核验日期','verified_day'),('账本序号','sequence'),('账本摘要','digest')])
 path=tmp_path/'native.md';path.write_text(text,encoding='utf-8')
 return payload,path

def test_legacy_exact_ledger_identity_is_accepted(tmp_path):
 payload,path=sample(tmp_path)
 assert Path(digest.required_success_report(tmp_path/'result.json','',payload))==path

@pytest.mark.parametrize('key',['verified_day','sequence','digest'])
def test_legacy_changed_ledger_record_rejects_report(tmp_path,key):
 payload,path=sample(tmp_path)
 payload[key]='changed'
 with pytest.raises(ValueError,match='ledger identity'):
  digest.required_success_report(tmp_path/'result.json','',payload)

def test_bound_report_accepts_exact_bytes_and_rejects_one_changed_byte(tmp_path):
 payload,path=sample(tmp_path)
 payload['report_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
 digest.required_success_report(tmp_path/'result.json','',payload)
 with path.open('ab') as f:f.write(b'!')
 with pytest.raises(ValueError,match='hash'):
  digest.required_success_report(tmp_path/'result.json','',payload)

@pytest.mark.parametrize('bad',[None,True,'invalid','0'*64])
def test_bad_hash_never_falls_back_to_matching_ledger_text(tmp_path,bad):
 payload,path=sample(tmp_path);payload['report_sha256']=bad
 with pytest.raises(ValueError,match='hash'):
  digest.required_success_report(tmp_path/'result.json','',payload)

def test_verified_v3_proof_requires_report_hash_before_normal_render(tmp_path,monkeypatch):
 path=tmp_path/'result.json';path.write_text(json.dumps({'status':'ok'}),encoding='utf8')
 monkeypatch.setattr(digest,'source_provenance',lambda *a:{'release':'20261109-fear-source-round2-v3'})
 monkeypatch.setattr(sys,'argv',['digest','--result',str(path),'--out-dir',str(tmp_path/'mail')])
 with pytest.raises(ValueError,match='requires report_sha256'):digest.main()
 assert not (tmp_path/'mail').exists()

@pytest.mark.parametrize('quoted',[False,True])
def test_legacy_duplicate_or_quoted_new_identity_does_not_hide_old_report(tmp_path,quoted):
 payload,path=sample(tmp_path)
 old=path.read_text(encoding='utf8').replace('2026-11-09','2026-11-06')
 matching=path.read_text(encoding='utf8')
 if quoted:matching='\n'.join('> '+line for line in matching.splitlines())
 path.write_text(old+'\n'+matching,encoding='utf8')
 with pytest.raises(ValueError,match='ledger identity'):
  digest.required_success_report(tmp_path/'result.json','',payload)
