"""Materialize exact Git objects and a candidate install pointer; never activate."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

from member_checkpoint_policy import validate_read_policy


BUNDLE_FILES = (
    'scripts/run_microcap_member_local.py',
    'scripts/restore_microcap_member_checkpoint.py',
    'scripts/member_checkpoint_policy.py',
    'scripts/restore_microcap_verified_state.py',
    'scripts/build_microcap_member_digest.py',
    'config/microcap_member_runtime.json',
    'config/microcap_member_local_runtime.json',
    'config/microcap_member_checkpoint.json',
    'config/microcap_member_seed_20261109_4774966d.zip',
)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def prepare(source, revision, strategy, out):
    source, strategy, out = (Path(p).resolve() for p in (source, strategy, out))
    if re.fullmatch('[0-9a-f]{40}', revision) is None:
        raise ValueError('An exact automation commit is required')
    if any(out == p or out.is_relative_to(p) or p.is_relative_to(out) for p in (source, strategy)):
        raise ValueError('Candidate bundle must be separate from both source and strategy')
    if out.exists():
        raise ValueError('Candidate bundle destination already exists; retain its evidence')
    candidate = out.parent / (out.name + '-candidate-member_daily_install.json')
    if candidate.exists():
        raise ValueError('Candidate installation receipt already exists')
    bodies = {name: subprocess.check_output(['git', '-C', str(source), 'show', revision + ':' + name])
              for name in BUNDLE_FILES}
    policy = validate_read_policy(json.loads(bodies['config/microcap_member_local_runtime.json']))
    cloud = json.loads(bodies['config/microcap_member_runtime.json'])
    seed = json.loads(bodies['config/microcap_member_checkpoint.json'])
    if (cloud.get('strategy_sha') != policy['checkpoint_strategy_sha']
            or cloud.get('core_strategy_sha') != policy['core_strategy_sha']
            or seed.get('strategy_sha') != policy['checkpoint_strategy_sha']
            or seed.get('bundled_path') != 'microcap_member_seed_20261109_4774966d.zip'
            or seed.get('sha256') != sha(bodies['config/' + seed['bundled_path']])):
        raise ValueError('Candidate local/cloud/seed contracts differ')
    actual = subprocess.check_output(['git', '-C', str(strategy), 'rev-parse', 'HEAD'], text=True).strip()
    if actual != policy['execution_strategy_sha']:
        raise ValueError('Candidate installation requires its exact local strategy reader')
    out.mkdir(parents=True)
    for name, raw in bodies.items():
        path = out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    manifest = dict(schema=1, automation_pin=revision, files={name: sha(raw) for name, raw in bodies.items()})
    manifest_raw = (json.dumps(manifest, indent=2) + '\n').encode()
    (out / 'manifest.json').write_bytes(manifest_raw)
    if any((out / name).read_bytes() != raw for name, raw in bodies.items()):
        raise ValueError('Candidate bundle readback changed')
    pointer = dict(schema='member_daily_local_install_v1', automation_pin=revision,
        automation_root=str(out), strategy_pin=actual, strategy_root=str(strategy), manifest_sha256=sha(manifest_raw))
    candidate.write_text(json.dumps(pointer, indent=2) + '\n', encoding='utf-8')
    return dict(status='local_member_bundle_prepared_not_installed', bundle=str(out), candidate_pointer=str(candidate),
        manifest_sha256=sha(manifest_raw), files=len(bodies), production_installation_changed=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--strategy-root', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.revision, args.strategy_root, args.out)))


if __name__ == '__main__':
    main()
