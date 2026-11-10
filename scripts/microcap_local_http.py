"""Reuse public HTTP connections without carrying cookies across native calls.

URLs, parameters, TLS, timeouts and retry budgets remain with the original
collectors. Each data process serializes its own GET transport; the two-process
and native price-write limits are unchanged.
"""
from contextlib import contextmanager
import threading
import requests


@contextmanager
def pooled_public_gets(session_factory=None):
    original=requests.get;session=(session_factory or requests.Session)();lock=threading.RLock()
    def get(url,*args,**kwargs):
        with lock:
            session.cookies.clear()
            try:return session.get(url,*args,**kwargs)
            finally:session.cookies.clear()
    requests.get=get
    try:yield
    finally:
        if requests.get is not get:
            session.close()
            raise RuntimeError('Native public HTTP transport changed ownership')
        requests.get=original
        session.close()
