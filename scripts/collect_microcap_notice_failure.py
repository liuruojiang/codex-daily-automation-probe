"""Bounded diagnostics for this refresh only; never validates/promotes ST evidence."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat

PARENT = '.microcap_index_cache/st_notice_sources'
POINTER = '.microcap_index_cache/st_notice_archive_pointer.json'
GEN = re.compile(r'refresh_[0-9]{8}T[0-9]{12}Z_[0-9a-f]{12}')
PAGE = re.compile(r'page_[0-9]{4}\.((receipt\.)?json)')
MAX_EXISTING = 10000
MAX_GENERATIONS = 4
MAX_DAYS = 32
MAX_FILES = 2048
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024


def stamp():
    return datetime.now(timezone.utc).isoformat()


def safe(root: Path, relative: str) -> Path:
    parts = PurePosixPath(relative)
    if (not relative or '\\' in relative or ':' in relative or parts.is_absolute()
            or any(p in ('..', '.') for p in relative.split('/'))):
        raise ValueError('unsafe evidence path')
    path = root
    for part in parts.parts:
        path = path / part
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
            raise ValueError('linked evidence path')
    if not path.resolve().is_relative_to(root):
        raise ValueError('evidence path escapes root')
    return path


def bounded_names(folder: Path, limit: int) -> list[str]:
    if not folder.exists():
        return []
    names = []
    with os.scandir(folder) as entries:
        for entry in entries:
            names.append(entry.name)
            if len(names) > limit:
                raise ValueError('diagnostic directory count limit exceeded')
    return sorted(names)


class Capture:
    def __init__(self, root: Path, out: Path):
        self.root, self.out = root.resolve(), out.absolute()
        # Diagnostics are independent artifacts, never files inside strategy state.
        if self.out.resolve().is_relative_to(self.root) or self.root.is_relative_to(self.out.resolve()):
            raise ValueError('diagnostic output overlaps source root')
        for path in (self.out, *self.out.parents):
            if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
                raise ValueError('linked diagnostic output')
        self.out.mkdir(parents=True, exist_ok=True)
        self.records, self.errors, self.total = [], [], 0

    def copy(self, relative: str, section: str) -> bytes:
        source = safe(self.root, relative)
        before = source.stat()
        if not stat.S_ISREG(before.st_mode):
            raise ValueError('evidence is not a regular file')
        if len(self.records) >= MAX_FILES or before.st_size > MAX_FILE_BYTES or self.total + before.st_size > MAX_TOTAL_BYTES:
            raise ValueError('diagnostic byte/file limit exceeded')
        with source.open('rb') as stream:
            raw = stream.read(min(MAX_FILE_BYTES, MAX_TOTAL_BYTES - self.total) + 1)
        after = source.stat()
        identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if len(raw) != before.st_size or identity(after) != identity(before):
            raise ValueError('evidence changed or exceeded bound during read')
        target = safe(self.out, section + '/' + relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream:
            stream.write(raw)
        digest = hashlib.sha256(raw).hexdigest()
        record = dict(source=relative, artifact=target.relative_to(self.out).as_posix(), bytes=len(raw), sha256=digest)
        try:
            json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            record['json_error'] = type(exc).__name__  # Preserve truncated/error HTTP bodies verbatim.
        self.records.append(record)
        self.total += len(raw)
        return raw

    def attempt(self, fn):
        try:
            return fn()
        except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
            self.errors.append(f'{type(exc).__name__}: {exc}')
            return None

    def summary(self, phase: str, **extra):
        result = dict(schema=1, phase=phase, recorded_at=stamp(), diagnostic_only=True,
                      source_validation=False, complete_capture=not self.errors,
                      files=self.records, bytes=self.total, errors=self.errors, **extra)
        safe(self.out, phase + '_summary.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf8')
        return result


def snapshot(root: Path, out: Path, baseline: Path, run_id: str) -> dict:
    capture = Capture(root, out)
    if baseline.absolute() != capture.out / 'baseline.json':
        raise ValueError('baseline must be diagnostic output/baseline.json')
    safe(capture.out, 'baseline.json')
    if baseline.exists():
        raise ValueError('baseline must be new; refusing to overwrite run boundary')
    def existing():
        parent = safe(capture.root, PARENT)
        return [n for n in bounded_names(parent, MAX_EXISTING) if GEN.fullmatch(n)]
    names = capture.attempt(existing)
    # A valid name baseline still permits new-generation diagnostics when old authority is malformed.
    if names is not None:
        baseline.parent.mkdir(parents=True, exist_ok=True)
        baseline.write_text(json.dumps(dict(schema=1, root=str(capture.root), run_id=run_id,
                                            started_at=stamp(), existing=names)) + '\n', encoding='utf8')
    def old_boundary():
        pointer = json.loads(capture.copy(POINTER, 'old_boundary'))
        archive_path = pointer['manifest_path']
        if not archive_path.startswith((PARENT+'/', 'data/st_notice_evidence/')) or PurePosixPath(archive_path).name != 'archive.json':
            raise ValueError('old authority outside notice evidence scope')
        archive_raw = capture.copy(archive_path, 'old_boundary')
        if hashlib.sha256(archive_raw).hexdigest() != pointer['manifest_sha256']:
            raise ValueError('old authority hash differs from pointer')
        archive = json.loads(archive_raw)
        boundary = archive['daily_increments'][-1]
        day = date.fromisoformat(boundary['date']).isoformat()
        ref = boundary['manifest']
        if not ref['path'].startswith((PARENT+'/', 'data/st_notice_evidence/')) or PurePosixPath(ref['path']).name != 'manifest.json':
            raise ValueError('old boundary outside notice evidence scope')
        manifest_raw = capture.copy(ref['path'], 'old_boundary')
        if hashlib.sha256(manifest_raw).hexdigest() != ref['sha256']:
            raise ValueError('old boundary manifest hash differs')
        manifest = json.loads(manifest_raw)
        if manifest['date'] != day or len(manifest['pages']) > MAX_FILES // 2:
            raise ValueError('old boundary date or page count invalid')
        folder = PurePosixPath(ref['path']).parent
        for page in manifest['pages']:
            for key in ('response', 'receipt'):
                name = page[key]
                if not isinstance(name, str) or not PAGE.fullmatch(name):
                    raise ValueError('old boundary page name invalid')
                raw = capture.copy((folder/name).as_posix(), 'old_boundary')
                if hashlib.sha256(raw).hexdigest() != page[key + '_sha256']:
                    raise ValueError('old boundary page hash differs')
    capture.attempt(old_boundary)
    return capture.summary('snapshot', run_id=run_id, baseline_written=names is not None)


def collect(root: Path, out: Path, baseline: Path, run_id: str) -> dict:
    capture = Capture(root, out)
    if baseline.absolute() != capture.out / 'baseline.json':
        raise ValueError('baseline must be diagnostic output/baseline.json')
    safe(capture.out, 'baseline.json')
    new = []
    def work():
        nonlocal new
        if baseline.is_symlink() or baseline.stat().st_size > 2 * 1024 * 1024:
            raise ValueError('baseline is linked or oversized')
        original = json.loads(baseline.read_text(encoding='utf8'))
        if original['schema'] != 1 or original['root'] != str(capture.root) or original['run_id'] != run_id:
            raise ValueError('baseline does not belong to this run/root')
        started = datetime.fromisoformat(original['started_at'])
        now = datetime.now(timezone.utc)
        if started.tzinfo is None or started > now:
            raise ValueError('invalid baseline start clock')
        old = original['existing']
        if not isinstance(old, list) or len(old) > MAX_EXISTING or len(set(old)) != len(old) or any(not isinstance(n, str) or not GEN.fullmatch(n) for n in old):
            raise ValueError('invalid baseline generation list')
        current = bounded_names(safe(capture.root, PARENT), MAX_EXISTING)
        new = [n for n in current if GEN.fullmatch(n) and n not in old]
        if len(new) > MAX_GENERATIONS:
            raise ValueError('new generation count exceeds diagnostic bound')
        for generation in new:
            generated = datetime.strptime(generation.split('_')[1], '%Y%m%dT%H%M%S%fZ').replace(tzinfo=timezone.utc)
            if not started <= generated <= now:
                raise ValueError('generation is outside this refresh interval')
            base = PARENT + '/' + generation
            names = bounded_names(safe(capture.root, base), MAX_DAYS + 4)
            if sum(bool(re.fullmatch(r'\d{4}-\d{2}-\d{2}', n)) for n in names) > MAX_DAYS:
                raise ValueError('generation day count exceeds diagnostic bound')
            for name in names:
                relative = base + '/' + name
                if name in ('archive.json', 'proof.json', 'previous_pointer.json', 'previous_freshness.json'):
                    capture.attempt(lambda rel=relative: capture.copy(rel, 'new_generations'))
                    continue
                if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', name) or date.fromisoformat(name).isoformat() != name:
                    raise ValueError('unexpected new-generation path')
                for filename in bounded_names(safe(capture.root, relative), MAX_FILES):
                    if filename not in ('manifest.json', 'invalid_manifest.json') and not PAGE.fullmatch(filename):
                        raise ValueError('unexpected day evidence path')
                    capture.attempt(lambda rel=relative+'/'+filename: capture.copy(rel, 'new_generations'))
    capture.attempt(work)
    return capture.summary('collection', run_id=run_id, new_generations=new)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('snapshot', 'collect'))
    for name in ('root', 'out', 'baseline'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args()
    result = (snapshot if args.phase == 'snapshot' else collect)(args.root, args.out, args.baseline, args.run_id)
    print(json.dumps({k:v for k,v in result.items() if k != 'files'}))
    return 0 if result['complete_capture'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
