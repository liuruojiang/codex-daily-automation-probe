"""An earned newer consumer baseline needs no producer-only active receipt."""
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import microcap_notice_prefix as m
from prepare_microcap_local import encode
from test_microcap_notice_prefix import notice_case, put


def baseline_case(c, *, protected_ref=None, formal_hash=None):
    for name,raw in c['bodies'].items():
        put(c['root'],name,raw)
    put(c['root'],m.POINTER,dict(manifest_path=m.END[0],manifest_sha256=m.END[1]))
    (c['root']/m.FRESHNESS).unlink()
    ref=protected_ref or m.END
    pointer=encode(dict(manifest_path=ref[0],manifest_sha256=ref[1]))
    put(c['protected'],m.POINTER,pointer)
    formal=encode(dict(inputs={m.POINTER:formal_hash or hashlib.sha256(pointer).hexdigest()}))
    path=put(c['protected'],'outputs/top100_delivery_manifest.json',formal)
    c['refs'][path]=formal
    def native(runtime,data,mode,profile_root=None):
        if mode=='prefix':
            return c['native'](runtime,data,mode,profile_root)
        return dict(mode='current',native_runtime=m.PRODUCER,
            state='covered_baseline_without_active_freshness',authority=m.END,
            old_prefix_unchanged=True,original_clocks_retained=True)
    return dict(c['args'],native=native)


def test_completed_consumer_baseline_without_active_freshness_is_a_read_only_noop(notice_case):
    c=notice_case;args=baseline_case(c)
    pair=m._pair(c['root']);originals={p:p.read_bytes() for p in c['refs']}
    result=m.import_prefix(**args)
    assert result['status']=='skipped_notice_prefix_already_covered'
    assert result['private_pointer_changed'] is result['private_freshness_changed'] is False
    assert m._pair(c['root'])==pair and not (c['root']/m.FRESHNESS).exists()
    assert all(p.read_bytes()==raw for p,raw in originals.items())
    assert not (args['out']/'transaction.json').exists()


@pytest.mark.parametrize('fault',['producer_ahead','formal_hash_wrong','formal_pointer_missing'])
def test_missing_active_receipt_cannot_hide_an_unearned_producer_advance(notice_case,fault):
    c=notice_case
    args=baseline_case(c,protected_ref=m.START if fault=='producer_ahead' else None,
                       formal_hash='0'*64 if fault=='formal_hash_wrong' else None)
    if fault=='formal_pointer_missing':
        path=c['protected']/'outputs/top100_delivery_manifest.json'
        raw=encode(dict(inputs={}))
        path.write_bytes(raw);c['refs'][path]=raw
    before=m._pair(c['root'])
    with pytest.raises(m.NoticePrefixError,match='actual_formal_consumer_authority'):
        m.import_prefix(**args)
    assert m._pair(c['root'])==before and not (args['out']/'receipt.json').exists()
