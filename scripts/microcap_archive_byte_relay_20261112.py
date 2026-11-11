"""One-off byte transport of an already native-verified archive, not a data gate."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path

BASE_SHA = '3d9aa183b614b272597bf81cfbd3b6fefdde021347fd36586e8047a6dc025e42'
BASE_BYTES = 673074646
DELTA_SHA = 'ec354f25c61136afcfe0a759e252b84c6e77b3c2111fbd9586bcfa9dbd4411a6'
DELTA_BYTES = 11693277
TARGET_SHA = '3326bf3adec1cb073d72318991a49e94d2e4599426ef0adcf5025baf2e6aa5d1'
TARGET_BYTES = 673248922
LITERAL_BYTES = 30745222


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def transfer(base, delta, output):
    base, delta, output = (Path(p).resolve() for p in (base, delta, output))
    partial = output.with_name(output.name + '.partial')
    if len({base, delta, output, partial}) != 4 or output.exists() or partial.exists():
        raise ValueError('Preserve existing transport inputs and outputs')
    for path, size, sha in ((base, BASE_BYTES, BASE_SHA), (delta, DELTA_BYTES, DELTA_SHA)):
        if path.stat().st_size != size or digest(path) != sha:
            raise ValueError('Exact transport input identity differs')

    def copy(stream, destination, size):
        while size:
            block = stream.read(min(size, 1024 * 1024))
            if not block:
                raise ValueError('Truncated transport range')
            destination.write(block)
            size -= len(block)

    output.parent.mkdir(parents=True, exist_ok=True)
    with base.open('rb') as old, gzip.open(delta, 'rb') as literals, partial.open('xb') as new:
        copy(old, new, 642415586)
        copy(literals, new, 256606)
        old.seek(642497988)
        copy(old, new, 88114)
        copy(literals, new, 7820776)
        copy(literals, new, 22667840)
        if literals.read(1):
            raise ValueError('Unexpected trailing literal bytes')
        new.flush()
        os.fsync(new.fileno())
    if partial.stat().st_size != TARGET_BYTES or digest(partial) != TARGET_SHA:
        raise ValueError('Reconstructed archive differs from the native-verified original')
    os.replace(partial, output)
    return dict(status='exact_native_archive_bytes_reconstructed', base_sha256=BASE_SHA,
                delta_sha256=DELTA_SHA, delta_bytes=DELTA_BYTES,
                sha256=TARGET_SHA, bytes=TARGET_BYTES, literal_bytes=LITERAL_BYTES,
                transport_only=True, current_signal_ready=False,
                sending='skipped', orders_placed=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--delta', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    args = parser.parse_args()
    if args.receipt.exists() or args.receipt.resolve() in {args.base.resolve(), args.delta.resolve(), args.output.resolve()}:
        raise ValueError('Preserve transport evidence')
    value = transfer(args.base, args.delta, args.output)
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    with args.receipt.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(value, indent=2) + '\n')
    print(json.dumps(value))


if __name__ == '__main__':
    main()
