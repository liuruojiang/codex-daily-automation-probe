"""One-source diagnostic only: exercise the existing native SDK deadline."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import time

p = argparse.ArgumentParser()
p.add_argument('--source', type=Path, required=True)
p.add_argument('--out', type=Path, required=True)
a = p.parse_args()
a.out.mkdir(parents=True, exist_ok=True)
spec = importlib.util.spec_from_file_location('native_bounded_market', a.source)
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)
result = dict(diagnostic_only=True, provider='stock_zh_a_spot_em', timeout_seconds=360,
              collected_at=datetime.now(timezone.utc).isoformat(),
              source_sha256=hashlib.sha256(a.source.read_bytes()).hexdigest())
started = time.monotonic()
try:
    frame = native.fetch_akshare_frame(result['provider'], timeout=result['timeout_seconds'])
    frame.to_json(a.out/'frame.json', orient='split', force_ascii=False)
    result.update(ok=True, rows=len(frame), columns=list(frame.columns),
                  frame_sha256=hashlib.sha256((a.out/'frame.json').read_bytes()).hexdigest())
except Exception as exc:
    result.update(ok=False, error_type=type(exc).__name__, error=str(exc))
    cause = exc.__cause__
    for field in ('stdout', 'stderr'):
        value = getattr(cause, field, None)
        if value:
            (a.out/(field+'.log')).write_bytes(value if isinstance(value, bytes) else value.encode('utf-8'))
finally:
    result.update(completed_at=datetime.now(timezone.utc).isoformat(), elapsed_seconds=time.monotonic()-started)
    (a.out/'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False), flush=True)
raise SystemExit(0 if result['ok'] else 1)
