"""Software name-transport invariants; no simulated market result is published."""
import csv
from datetime import datetime,timezone
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import pandas as pd
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import maintain_microcap_local as m


@pytest.fixture
def names(tmp_path,monkeypatch):
    root=tmp_path/'producer';root.mkdir();(root/'microcap_top100_mom16_biweekly_live_v2_0.py').write_bytes(b'software fixture')
    symbols=[f'{i:06}' for i in range(1,301)]
    def csv_body(rows):
        s=io.StringIO();w=csv.DictWriter(s,fieldnames=['symbol','name','code']);w.writeheader();w.writerows(rows);return s.getvalue().encode()
    rows=[dict(symbol='sz'+s,name='ordinary '+s,code=s) for s in symbols]
    rows += [dict(symbol='sz300295',name='*ST short',code='300295'),dict(symbol='sh600999',name='cold preserved',code='600999')]
    path=m.bound(root,m.CACHE+'active_universe.csv');path.parent.mkdir(parents=True);path.write_bytes(csv_body(rows))
    prior=path.read_bytes();calls=[]
    def st(**kwargs):m.atomic(m.bound(root,m.CACHE+'current_st.csv'),b'code,name\n300295,*ST complete\n')
    def quote(requested):
        calls.append(requested)
        return pd.DataFrame([dict(code=s,name='*ST complete' if s=='300295' else 'ordinary '+s,
            fetched_at=datetime.now(timezone.utc).isoformat(),quote_source='tencent_batch_free') for s in requested])
    monkeypatch.setitem(sys.modules,'scripts.bounded_market_data',SimpleNamespace(fetch_akshare_frame=lambda *a,**k:None))
    native=SimpleNamespace(fetch_current_st_codes=st,fetch_active_universe=lambda **kw:pytest.fail('must not recollect all market quotes'))
    m.refresh_native_current_names(root,'2000-01-04',native,symbols,quote)
    return root,symbols,prior,calls


def test_native_names_query_only_maintained_pool_and_actual_st_conflict(names):
    root,symbols,prior,calls=names
    assert calls==[sorted(symbols+['300295'])]
    files=m.current_names_proof(root,'2000-01-04',symbols=symbols)
    assert len(files)==4
    receipt=json.loads(m.bound(root,m.CACHE+'local_current_names/2000-01-04.json').read_bytes())
    original=receipt['scoped_observation']['prior_universe']
    assert m.bound(root,original['path']).read_bytes()==prior
    assert m.pd_read_names(m.bound(root,m.CACHE+'active_universe.csv').read_bytes())['600999']=='cold preserved'


@pytest.mark.parametrize('fault',['outside','quote_clock','quote_source','coverage','roster','prior','scope','full_scope','fake_conflict'])
def test_forged_or_incomplete_name_observation_is_not_a_complete_stage(names,fault):
    root,symbols,prior,calls=names;path=m.bound(root,m.CACHE+'local_current_names/2000-01-04.json')
    value=json.loads(path.read_bytes());w=value['scoped_observation']
    if fault=='outside':
        u=m.bound(root,m.CACHE+'active_universe.csv');raw=u.read_bytes().replace(b'cold preserved',b'changed cold');u.write_bytes(raw);value['universe_sha256']=m.sha(raw)
    elif fault=='quote_clock':w['quote_rows'][0]['fetched_at']='1999-01-01T00:00:00+00:00'
    elif fault=='quote_source':w['quote_rows'][0]['quote_source']='unknown'
    elif fault=='coverage':w['quote_rows'].pop()
    elif fault=='roster':w['maintained_symbols'][0]='600888'
    elif fault=='prior':m.bound(root,w['prior_universe']['path']).write_bytes(b'changed')
    elif fault=='scope':value.pop('publication_name_scope')
    elif fault=='full_scope':value['publication_name_scope']='full_native_name_snapshot'
    else:
        w['conflict_symbols'].append('600999');w['requested_symbols'].append('600999')
        w['quote_rows'].append(dict(code='600999',name='cold preserved',fetched_at=value['observed_at'],quote_source='tencent_batch_free'))
    path.write_bytes(m.encode(value))
    with pytest.raises(ValueError):m.current_names_proof(root,'2000-01-04',symbols=symbols)
