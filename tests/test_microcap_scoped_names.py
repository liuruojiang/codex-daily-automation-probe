"""Software name-transport invariants; no simulated market result is published."""
import csv
from datetime import datetime,timedelta,timezone
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from urllib.parse import urlencode
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


def risk_board_response(total, code, name):
    return SimpleNamespace(status_code=200, raise_for_status=lambda: None,
                           content=m.encode({'data': {'total': total, 'diff': [{'f12': code, 'f14': name}]}}))


def test_intraday_duplicate_recovers_by_stable_page_order_without_dropping_rows(names, monkeypatch):
    root,symbols,_,_=names
    target = '2000-01-04'
    board = m.bound(root,m.CACHE+'current_st.csv')
    receipt = m.bound(root,m.CACHE+'local_current_names/'+target+'.json')
    original_board = board.read_bytes() + b'300295,*ST complete\r\n'
    board.write_bytes(original_board)
    observation = json.loads(receipt.read_bytes())
    observation.update(count=2, cache_sha256=m.sha(original_board))
    receipt.write_bytes(m.encode(observation))
    floor = datetime.now(timezone.utc) - timedelta(minutes=1)
    with pytest.raises(m.DuplicateCurrentRiskRows):
        m.current_names_proof(root,target,floor,symbols)

    calls = []
    rows = {'300295': '*ST complete', '000010': '*ST other'}
    pages = [risk_board_response(2, code, name) for code,name in rows.items()]
    def get(url, *, params, **kwargs):
        calls.append(params)
        assert kwargs['verify'] is True and kwargs['allow_redirects'] is False
        return pages[int(params['pn'])-1]
    monkeypatch.setattr('requests.get', get)
    native = SimpleNamespace(fetch_current_st_codes=lambda **kw:pytest.fail('duplicate pages must not be queried again'))
    def quote(requested):
        return pd.DataFrame([dict(code=s,name=rows.get(s,'ordinary '+s),
            fetched_at=datetime.now(timezone.utc).isoformat(),quote_source='tencent_batch_free') for s in requested])

    evidence = root / 'evidence'
    m.refresh_current_name_stage(root,target,native,symbols,quote,floor,evidence)
    m.current_names_proof(root,target,floor,symbols)
    assert m.pd_read_names(board.read_bytes()) == rows
    assert [p['pn'] for p in calls] == ['1','2'] and {p['fid'] for p in calls} == {'f12'}
    assert next(evidence.glob('*/2.raw')).read_bytes() == pages[1].content
    assert json.loads(receipt.read_bytes())['native_functions'][0] == 'stable_current_risk_board'
    saved = m.bound(root,m.CACHE+'local_current_names/observations/'+m.sha(original_board)+'.csv')
    assert saved.read_bytes() == original_board


@pytest.mark.parametrize('second',[risk_board_response(2,'300295','*ST repeated'),
                                   risk_board_response(3,'000010','*ST changed total')])
def test_stable_board_rejects_repeated_issuer_or_changing_coverage(tmp_path, monkeypatch, second):
    first = risk_board_response(2,'300295','*ST first')
    monkeypatch.setattr('requests.get', lambda url,*,params,**kw: first if params['pn']=='1' else second)
    evidence = tmp_path / 'risk-board'
    with pytest.raises(ValueError,match='coverage|issuer'):
        m.stable_current_risk_board(evidence, [])
    assert (evidence/'2.raw').read_bytes() == second.content


@pytest.mark.parametrize('host',['81.push2.eastmoney.com','other.example'])
def test_stable_board_follows_only_exact_eastmoney_redirect(tmp_path, monkeypatch, host):
    urls = []
    def get(url, *, params=None, **kwargs):
        urls.append(url)
        if params is None:
            return risk_board_response(1,'300295','*ST first')
        query = urlencode(params)
        return SimpleNamespace(status_code=302, url=url+'?'+query, content=b'',
                               headers={'Location':f'https://{host}/api/qt/clist/get?{query}'})
    monkeypatch.setattr('requests.get',get)
    evidence = tmp_path / 'risk-board'
    trace = []
    if host == 'other.example':
        with pytest.raises(ValueError,match='outside the exact Eastmoney query'):
            m.stable_current_risk_board(evidence,trace)
        assert len(urls) == 1
    else:
        assert m.stable_current_risk_board(evidence,trace) == [{'code':'300295','name':'*ST first'}]
        assert len(urls) == 2 and trace[0]['redirect']['status'] == 302
    assert (evidence/'1-redirect.json').is_file()


def test_reviewed_alias_keeps_both_raw_names_and_native_st_membership(names):
    root,symbols,_,_=names
    def st(**kwargs):
        m.atomic(m.bound(root,m.CACHE+'current_st.csv'),'code,name\n300295,*ST三六\n'.encode())
    def quotes(requested):
        return pd.DataFrame([dict(code=s,name='*ST三六五' if s=='300295' else 'ordinary '+s,
            fetched_at=datetime.now(timezone.utc).isoformat(),quote_source='tencent_batch_free') for s in requested])
    native=SimpleNamespace(fetch_current_st_codes=st)
    m.refresh_native_current_names(root,'2000-01-04',native,symbols,quotes)
    m.current_names_proof(root,'2000-01-04',symbols=symbols)
    assert m.bound(root,m.CACHE+'current_st.csv').read_text(encoding='utf-8')=='code,name\n300295,*ST三六\n'
    assert m.pd_read_names(m.bound(root,m.CACHE+'active_universe.csv').read_bytes())['300295']=='*ST三六五'
    receipt=json.loads(m.bound(root,m.CACHE+'local_current_names/2000-01-04.json').read_bytes())
    assert next(r for r in receipt['scoped_observation']['quote_rows'] if r['code']=='300295')['name']=='*ST三六五'


@pytest.mark.parametrize('code,board,quote',[
    ('300296','*ST三六','*ST三六五'),('300295','ST三六','*ST三六五'),
    ('300295','*ST三六','三六五'),('300295','*ST三六','*ST三六六'),
    ('300295','*ST三六五','*ST三六'),('300295','*ST三','*ST三六五')])
def test_reviewed_alias_never_generalizes_identity_risk_or_truncation(code,board,quote):
    assert not m.current_st_names_agree(code,board,quote)


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
