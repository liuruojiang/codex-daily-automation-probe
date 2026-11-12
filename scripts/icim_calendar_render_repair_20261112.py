"""Inspect the retained 2026-11-12 failed-render artifact, without collection."""
from pathlib import Path, PurePosixPath
import hashlib
import io
import json
import os
import sys
import urllib.error
import urllib.request
import zipfile

REPO = 'liuruojiang/codex-daily-automation-probe'
ARTIFACT = 13958841337
RUN = 43247959416
HEAD = '3cbb6fb1f0e8e2097fd086eaca48ba30a5c4d64e'
DIGEST = '8219545cdd4e6f5a7682efad0350c7fb15cc65e7429b003c5be3562f07fdf8c9'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(path):
    return urllib.request.Request('https://api.github.com/repos/' + REPO + path,
        headers={'Authorization': 'Bearer ' + os.environ['GITHUB_TOKEN'],
                 'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'})


def main():
    with urllib.request.urlopen(request('/actions/artifacts/' + str(ARTIFACT)), timeout=30) as response:
        meta = json.load(response)
    assert meta['id'] == ARTIFACT and meta['expired'] is False
    assert meta['name'] == 'ic-im-v1-4-r1-readiness-' + str(RUN)
    assert meta['workflow_run']['id'] == RUN and meta['workflow_run']['head_sha'] == HEAD
    assert meta['size_in_bytes'] == 112526 and meta.get('digest') == 'sha256:' + DIGEST
    opener = urllib.request.build_opener(NoRedirect)
    try:
        opener.open(request('/actions/artifacts/' + str(ARTIFACT) + '/zip'), timeout=30)
        raise RuntimeError('artifact archive must use its authenticated redirect')
    except urllib.error.HTTPError as exc:
        if exc.code != 302:
            raise
        location = exc.headers['Location']
    assert location.startswith('https://')
    with urllib.request.urlopen(location, timeout=30) as response:
        raw = response.read(112527)
    assert len(raw) == 112526 and hashlib.sha256(raw).hexdigest() == DIGEST
    archive = zipfile.ZipFile(io.BytesIO(raw))
    assert len(archive.infolist()) <= 40 and sum(i.file_size for i in archive.infolist()) < 20000000
    names = archive.namelist()
    print('ICIM_REPAIR_ARCHIVE ' + json.dumps({'artifact': ARTIFACT, 'run': RUN, 'sha256': DIGEST, 'members': names}))
    retained = Path('retained-readiness')
    retained.mkdir(exist_ok=False)
    for info in archive.infolist():
        path = PurePosixPath(info.filename)
        assert not path.is_absolute() and '..' not in path.parts and not (info.external_attr >> 16) & 0o170000 == 0o120000
        if info.is_dir():
            continue
        assert info.filename.startswith('readiness-artifacts/') or info.filename.startswith('readiness-mail/')
        data = archive.read(info)
        target = retained.joinpath(*path.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        if '--render-input' in sys.argv or path.suffix not in {'.json', '.md', '.txt'} or path.name.startswith(('fear-', 'jiu', 'baifenwei')):
            continue
        assert len(data) <= 1048576
        encoded = data.hex()
        parts = [encoded[i:i+16000] for i in range(0, len(encoded), 16000)] or ['']
        for number, part in enumerate(parts):
            print('ICIM_REPAIR_BLOB ' + json.dumps({'name': info.filename, 'sha256': hashlib.sha256(data).hexdigest(),
                  'part': number, 'parts': len(parts), 'hex': part}))
    result = json.loads((retained / 'readiness-artifacts/result.json').read_text())
    assert result['status'] == 'ok' and result['market_date'] == result['verified_day'] == '2026-11-12'
    assert result['sequence'] == 35 and result['publication_mode'] == 'close_confirmed'
    assert result['build'] == 'v1.4-20261008-r1-ic-csi500-abs40-fix11'
    expected_files = {'result.json': 'fa958de57636ea18a02a1f6245e7fdcbd4a9bda5ae49a60edd6437ef90ab6d53',
        'ic_im_v1_4_close_signal.md': '6f388854e5e44a7010ff8383b62ef4d2d2e8b73f3de76554f66536dae93b5c3b',
        'program-source.json': '4f4520d7613bed38b90750fc564057e2026926d81a326c6d6c4cc9606d9e41a9'}
    assert all(hashlib.sha256((retained / 'readiness-artifacts' / name).read_bytes()).hexdigest() == digest
               for name, digest in expected_files.items())
    Path('repair-artifacts').mkdir(exist_ok=False)
    Path('repair-artifacts/reuse-verification.json').write_text(json.dumps({
        'source_artifact': ARTIFACT, 'source_run': RUN, 'archive_sha256': DIGEST,
        'strategy_sha': '9ec8b91c5256155101faae3c84dba9ff95632db5',
        'market_date': result['market_date'], 'verified_day': result['verified_day'], 'sequence': result['sequence'],
        'original_file_sha256': expected_files, 'producer_rerun': False, 'formal_ledger_written': False,
        'message_sent': False}, indent=2) + '\n')
    print('ICIM_REPAIR_RESULT ' + json.dumps({key: result.get(key) for key in
          ['status', 'market_date', 'verified_day', 'sequence', 'build', 'publication_mode']}, ensure_ascii=True))
    print('ICIM_REPAIR_CALENDAR ' + json.dumps({product: signal.get('calendar_source_evidence')
          for product, signal in result['signals'].items()}, ensure_ascii=True))


if __name__ == '__main__':
    main()
