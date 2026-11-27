"""Read the installed local strategy contract and its native evidence."""
import argparse
import hashlib
import json
from pathlib import Path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verified_json(path, expected):
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError(f'Content differs from the installed digest: {path}')
    return json.loads(raw)


def bound_file(root, relative):
    path = root / relative
    if not path.resolve().is_relative_to(root) or Path(relative).is_absolute():
        raise ValueError(f'Installed path escapes its bundle: {relative}')
    return path


def inspect(home, family, *, gate=None, checkpoint_receipt=None):
    pointer_path = home / ('member_daily_install.json' if family == 'microcap' else 'icim_local_install.json')
    pointer_raw = pointer_path.read_bytes()
    pointer = json.loads(pointer_raw)
    expected_schema = 'member_daily_local_install_v1' if family == 'microcap' else 'icim_local_install_v1'
    if pointer.get('schema') != expected_schema:
        raise ValueError('Local installation schema differs')
    root = Path(pointer['automation_root']).resolve(strict=True)
    manifest = verified_json(root / 'manifest.json', pointer['manifest_sha256'])
    if manifest['automation_pin'] != pointer['automation_pin'] or not manifest['files']:
        raise ValueError('Installed bundle identity differs')
    for relative, expected in manifest['files'].items():
        if digest(bound_file(root, relative)) != expected:
            raise ValueError(f'Installed bundle file differs: {relative}')
    evidence = {
        'schema': 'local_session_install_readback_v1',
        'family': family,
        'pointer': str(pointer_path),
        'pointer_sha256': hashlib.sha256(pointer_raw).hexdigest(),
        'installation': pointer,
        'bundle_file_count': len(manifest['files']),
    }
    if family == 'microcap':
        if checkpoint_receipt is not None:
            reader = json.loads(checkpoint_receipt.read_bytes())
            if (reader.get('schema') != 'microcap_member_local_checkpoint_readback_v1'
                    or reader.get('native_verify_exit_code') != 0
                    or reader.get('execution_strategy_sha') != pointer['strategy_pin']):
                raise ValueError('Checkpoint readback differs from the installed strategy')
            path = Path(reader['checkpoint'])
            checkpoint = verified_json(path, reader['checkpoint_sha256'])
            if (checkpoint['as_of_date'] != reader['as_of_date']
                    or checkpoint.get('period_status', 'current') != reader['period_status']
                    or checkpoint['period']['sha256'] != reader['period_sha256']):
                raise ValueError('Checkpoint readback differs from the package')
            evidence['checkpoint_receipt'] = {
                'path': str(checkpoint_receipt), 'sha256': digest(checkpoint_receipt), 'readback': reader,
            }
    else:
        prepared = Path(pointer['prepared']).resolve(strict=True)
        receipt = verified_json(prepared / 'prepared.json', pointer['prepared_receipt_sha256'])
        release_path = bound_file(prepared / 'icim', receipt['release_manifest'])
        release = verified_json(release_path, receipt['release_manifest_sha256'])
        evidence['icim_release'] = {
            'strategy_pin': receipt['strategy_pins']['icim'],
            'release': release['release'],
            'manifest': str(release_path),
            'manifest_sha256': receipt['release_manifest_sha256'],
            'official_test_files': len(release['required_local_tests']),
            'independent_test_files': len(receipt['icim_supplemental_tests']),
        }
        if gate is not None:
            gate_raw = gate.read_bytes()
            value = json.loads(gate_raw)
            if (value['prepared_sha256'] != pointer['prepared_receipt_sha256']
                    or Path(value['prepared']).resolve() != prepared
                    or value['workflow']['strategy_pin'] != receipt['strategy_pins']['icim']):
                raise ValueError('Gate evidence belongs to a different IC/IM installation')
            formal = value['groups'].get('icim47', {}).get('official_receipt', {})
            independent = value['groups'].get('independent5', {})
            evidence['icim_gate'] = {
                'path': str(gate), 'sha256': hashlib.sha256(gate_raw).hexdigest(),
                'pass': value['pass'], 'release_for_intraday': value['release_for_intraday'],
                'calendar': value['calendar'], 'workflow': value['workflow'],
                'official_counts': formal.get('counts'), 'independent_counts': independent.get('counts'),
            }
    if pointer_path.read_bytes() != pointer_raw:
        raise ValueError('Installation pointer changed during readback')
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, required=True)
    parser.add_argument('--family', choices=('microcap', 'icim'), required=True)
    parser.add_argument('--gate', type=Path)
    parser.add_argument('--checkpoint-receipt', type=Path)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    if (args.gate and args.family != 'icim') or (args.checkpoint_receipt and args.family != 'microcap'):
        parser.error('Evidence option is for a different strategy')
    report = inspect(args.home.resolve(), args.family, gate=args.gate, checkpoint_receipt=args.checkpoint_receipt)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    if args.out:
        with args.out.open('x', encoding='utf-8') as stream:
            stream.write(rendered)
    print(rendered, end='')


if __name__ == '__main__':
    main()
