"""Transport invariants; no synthetic quote is treated as a source response."""
from pathlib import Path
import sys
import pytest
import requests
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from microcap_local_http import pooled_public_gets


class Session:
    def __init__(self):self.cookies=requests.cookies.RequestsCookieJar();self.calls=[];self.closed=False
    def get(self,url,*args,**kwargs):
        assert not list(self.cookies)
        self.calls.append((url,args,kwargs));self.cookies.set('server_cookie','value')
        if url=='failure':raise requests.Timeout('native timeout')
        return object()
    def close(self):self.closed=True


def test_same_connection_pool_preserves_request_policy_without_cross_request_cookies():
    original=requests.get;session=Session()
    with pooled_public_gets(lambda:session):
        for _ in range(2):requests.get('https://public.example/data',params={'issuer':'000001'},timeout=20,verify=True,headers={'User-Agent':'Mozilla/5.0'})
    assert requests.get is original and session.closed and len(session.calls)==2
    assert session.calls[0][2]==session.calls[1][2]==dict(params={'issuer':'000001'},timeout=20,verify=True,headers={'User-Agent':'Mozilla/5.0'})
    assert not list(session.cookies)


def test_transport_does_not_add_retry_or_swallow_native_timeout():
    original=requests.get;session=Session()
    with pytest.raises(requests.Timeout):
        with pooled_public_gets(lambda:session):requests.get('failure',timeout=20,verify=True)
    assert len(session.calls)==1 and session.closed and requests.get is original and not list(session.cookies)
