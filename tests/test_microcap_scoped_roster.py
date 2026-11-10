"""Software-only transport examples, not market observations."""
import copy
import io
import json
import sys
import zipfile
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import microcap_scoped_roster as m
from prepare_microcap_local import request_params,URL


@pytest.fixture
def roster(tmp_path):
    source=tmp_path/'screen/source-v2-name';source.mkdir(parents=True)
    run=tmp_path/'screen/run';run.mkdir()
    rows=[dict(SECURITY_CODE=f'{i:06}',SECUCODE=f'{i:06}.SZ',TRADE_DATE='2026-11-09',
               SECURITY_NAME_ABBR='fixture',TOTAL_SHARES=100,CLOSE_PRICE=i,TOTAL_MARKET_CAP=i*100)
          for i in range(1,302)]
    body=m.encode(dict(success=True,result=dict(pages=1,count=301,data=rows)))
    (source/'page-01.json').write_bytes(body)
    (source/'page-01.receipt.json').write_bytes(m.encode(dict(url=URL,params=request_params('2026-11-09',1),
        fetched_at='2026-11-09T08:00:00+00:00',sha256=m.sha(body),tls_verify=True)))
    members=[f'{i:06}' for i in range(1,101)];st=b'code,name\n600999,fixture\n'
    candidates=m.encode(m.select_pool(rows,members,{'600999'}))
    (run/'candidates.json').write_bytes(candidates);(run/'st-reference.csv').write_bytes(st)
    summary=dict(schema='microcap_local_screen_preparation_v1',status='PREPARATION_VERIFIED',
        date='2026-11-09',run_directory=str(run),candidates_sha256=m.sha(candidates),st_reference_sha256=m.sha(st))
    pool=source.parent/'latest.json';pool.write_bytes(m.encode(summary))
    bundle=tmp_path/'original.zip';result=m.export(pool,'2026-11-09',bundle)
    return bundle,result['sha256']


def repack(bundle,change):
    with zipfile.ZipFile(bundle) as z:bodies={n:z.read(n) for n in z.namelist()}
    manifest=json.loads(bodies.pop('manifest.json'));change(manifest,bodies)
    manifest['files']={n:m.sha(raw) for n,raw in bodies.items()}
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:
        for name,raw in bodies.items():z.writestr(name,raw)
        z.writestr('manifest.json',m.encode(manifest))
    bundle.write_bytes(out.getvalue());return m.sha(out.getvalue())


def test_roundtrip_reproduces_original_ranking_and_can_export_again(roster,tmp_path):
    bundle,digest=roster;out=tmp_path/'restore'
    result=m.restore(bundle,digest,'2026-11-12',out)
    original=(out/'original-summary.json').read_bytes()
    portable=json.loads(Path(result['pool']).read_bytes())
    assert portable['portable_original_summary_sha256']==m.sha(original)
    assert portable['run_directory']==str(out.resolve())
    m.export(result['pool'],'2026-11-12',tmp_path/'second.zip')


@pytest.mark.parametrize('fault',['stale','sha','source','st_reference','ranking','escape'])
def test_failed_restore_never_publishes_usable_locator(roster,tmp_path,fault):
    bundle,digest=roster;out=tmp_path/'failed'
    def change(manifest,bodies):
        if fault=='source':bodies['source/page-01.json']=b'{}'
        elif fault=='st_reference':bodies['st-reference.csv']=b'code,name\n000001,changed\n'
        elif fault=='escape':bodies['source/../../escape.json']=b'{}'
        elif fault=='ranking':
            v=json.loads(bodies['candidates.json']);v['candidates'][150]['total_market_cap']='0'
            bodies['candidates.json']=m.encode(v)
            summary=json.loads(bodies['original-summary.json']);summary['candidates_sha256']=m.sha(bodies['candidates.json'])
            bodies['original-summary.json']=m.encode(summary)
    if fault not in ('stale','sha'):digest=repack(bundle,change)
    with pytest.raises((ValueError,KeyError)):
        m.restore(bundle,'0'*64 if fault=='sha' else digest,'2026-11-17' if fault=='stale' else '2026-11-12',out)
    assert not (out/'latest.json').exists()
    assert not (tmp_path/'escape.json').exists()
