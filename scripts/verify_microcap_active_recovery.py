"""Bind the active release to an independently accepted recovery pair.

This is a release identity check, not current-day data or email acceptance.
The full two-run verifier downloads original API-bound artifacts once at release;
daily runs authenticate its immutable receipt rather than downloading them again.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re


def verify(root):
    root = Path(root).resolve()
    config = json.loads((root / 'config/microcap_approved_state.json').read_bytes())
    if re.fullmatch('[0-9a-f]{40}', str(config.get('strategy_sha', ''))) is None:
        raise ValueError('Active recovery requires an immutable runtime commit')
    binding = config.get('recovery_readiness')
    if not isinstance(binding, dict) or set(binding) != {'path', 'sha256'}:
        raise ValueError('Active runtime requires an exact recovery acceptance binding')
    if binding['path'] != 'config/microcap_recovery_readiness.json':
        raise ValueError('Recovery acceptance must use the fixed release evidence path')
    path = root / binding['path']
    if path.is_symlink() or path.resolve().parent != root / 'config':
        raise ValueError('Recovery acceptance path escapes release')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != binding['sha256']:
        raise ValueError('Recovery acceptance bytes changed')
    receipt = json.loads(raw)
    required = {'schema': 'microcap_recovery_readiness_v1', 'pass': True,
                'strategy_sha': config['strategy_sha'], 'seed_date': config['state_date'],
                'bundle_sha256': config['state_sha256'],
                'repository': 'liuruojiang/codex-daily-automation-probe',
                'fallback_used_by_consumer': False, 'recovery_only': True,
                'current_market_authorized': False, 'email_delivery_proven': False}
    for key, expected in required.items():
        if receipt.get(key) != expected or type(receipt.get(key)) is not type(expected):
            raise ValueError('Active recovery acceptance mismatch: ' + key)
    for key in ('producer_run', 'consumer_run', 'source_artifact', 'bundle_bytes'):
        if type(receipt.get(key)) is not int or receipt[key] <= 0:
            raise ValueError('Missing actual recovery identity: ' + key)
    if receipt['producer_run'] == receipt['consumer_run']:
        raise ValueError('Recovery requires two distinct actual runs')
    workflow = (root / '.github/workflows/microcap-realtime-digest.yml').read_text(encoding='utf-8')
    repository = r"(?:liuruojiang/microcap|'liuruojiang/microcap'|\"liuruojiang/microcap\")"
    entries = re.findall(r'(?m)^[ \t]*repository:[^\r\n]*$', workflow)
    refs = re.findall(r'(?m)^[ \t]*repository:[ \t]*' + repository
                      + r'[ \t]*(?:#[^\r\n]*)?\r?\n[ \t]*ref:[ \t]*([^\r\n]+)', workflow)
    pins = []
    for ref in refs:
        ref = ref.strip()
        if ref == '${{ steps.recovery_pin.outputs.sha }}':
            ref = config['strategy_sha']
        elif len(ref) >= 2 and ref[0] in ('\"', "'") and ref[-1] == ref[0]:
            ref = ref[1:-1]
        pins.append(ref)
    if (not entries or len(refs) != len(entries) or len(re.findall(r'\brepository\s*:', workflow)) != len(entries)
            or set(pins) != {config['strategy_sha']}):
        raise ValueError('Delivery workflow differs from accepted runtime')
    return {'pass': True, 'strategy_sha': config['strategy_sha'],
            'recovery_acceptance_sha256': binding['sha256'], 'current_session_verified': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.root)))
