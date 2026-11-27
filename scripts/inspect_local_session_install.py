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


def member_readback(pointer, path):
    raw = path.read_bytes()
    reader = json.loads(raw)
    if (reader.get('schema') != 'microcap_member_local_checkpoint_readback_v1'
            or reader.get('native_verify_exit_code') != 0
            or reader.get('execution_strategy_sha') != pointer['strategy_pin']):
        raise ValueError('Checkpoint readback differs from the installed strategy')
    package = Path(reader['checkpoint']).resolve(strict=True)
    checkpoint = verified_json(package, reader['checkpoint_sha256'])
    period = verified_json(bound_file(package.parent, checkpoint['period']['path']), reader['period_sha256'])
    expected = {
        'as_of_date': checkpoint['as_of_date'],
        'next_trade_date': checkpoint.get('next_trade_date'),
        'period_status': checkpoint.get('period_status', 'current'),
        'period_sha256': checkpoint['period']['sha256'],
        'producer_strategy_sha': checkpoint['strategy_sha'],
        'period_package_id': period.get('package_id'),
        'period_effective_from': period.get('effective_from'),
        'next_rebalance_date': period.get('next_rebalance_date'),
    }
    if any(reader[key] != value for key, value in expected.items()):
        raise ValueError('Checkpoint readback differs from the package')
    source = reader['source']
    if source.get('source') == 'verified_local_checkpoint':
        if Path(source['path']).resolve() != package or package.is_relative_to(path.parent / 'checkpoint'):
            raise ValueError('Cached checkpoint provenance differs from its selected path')
    elif (package != (path.parent / 'checkpoint/checkpoint.json').resolve()
          or source != json.loads((path.parent / '01.stdout.log').read_bytes())):
        raise ValueError('Restored checkpoint provenance differs from the native read log')
    return {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest(), 'readback': reader}


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
            evidence['checkpoint_receipt'] = member_readback(pointer, checkpoint_receipt.resolve())
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
