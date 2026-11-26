"""Reconstruct a native-checked archive without changing a byte."""
from gzip import open as open_gzip
from hashlib import file_digest
from pathlib import Path
import json
import os


def digest(path):
    with path.open('rb') as raw:
        return file_digest(raw,'sha256').hexdigest()


recipe=Path('whole-return-guard-delta.json')
assert digest(recipe)==os.environ['RECIPE_SHA']
spec=json.loads(recipe.read_bytes())
base=Path('microcap-whole-delivery-state-20261126-4608a82d.zip')
delta=Path('whole-return-guard-literals.gz')
out=Path(os.environ['ARCHIVE'])
assert (spec['base_name'],spec['delta_name'],spec['target_name'])==(base.name,delta.name,out.name)
assert spec['base_sha256']=='173a6bcbb39a53615f98cf36cdd621c4daa74652d50a9286ea32c8ede39ee90f'
assert spec['target_sha256']==os.environ['ARCHIVE_SHA'] and spec['target_bytes']==int(os.environ['ARCHIVE_SIZE'])
for path,kind in ((base,'base'),(delta,'delta')):
    assert path.stat().st_size==spec[kind+'_bytes'] and digest(path)==spec[kind+'_sha256']
assert not out.exists() and sum(n for _,_,n in spec['ops'])==spec['target_bytes']
part=out.with_suffix('.zip.partial')
with base.open('rb') as old, open_gzip(delta,'rb') as literals, part.open('xb') as new:
    for role,start,count in spec['ops']:
        assert type(start) is int and type(count) is int and start>=0 and count>0
        if role=='copy':
            assert start+count<=spec['base_bytes']
            old.seek(start)
            source=old
        else:
            assert role=='literal' and start==0
            source=literals
        while count:
            block=source.read(min(count,1024*1024))
            assert block
            new.write(block)
            count-=len(block)
    assert not literals.read(1)
assert part.stat().st_size==spec['target_bytes'] and digest(part)==spec['target_sha256']
part.rename(out)
receipt={k:spec[k] for k in ('base_sha256','delta_sha256','delta_bytes','target_sha256','target_bytes')}
receipt.update(status='exact_native_archive_bytes_reconstructed',transport_only=True,sending='skipped')
with Path('relay-result.json').open('x',encoding='utf-8') as record:
    json.dump(receipt,record,indent=2)
print(json.dumps(receipt),flush=True)
