"""Publish only the exact newly earned v3 reader archive, reusing safe release reconciliation."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

import publish_microcap_boundary_archive_20261112 as original

BASELINE_RUNTIME = original.RUNTIME


def publish(root, bundle, evidence, review):
    root = Path(root).resolve()
    config = json.loads(Path(review).read_bytes())
    runtime = config['runtime_sha']
    if re.fullmatch('[0-9a-f]{40}', runtime) is None or runtime == BASELINE_RUNTIME:
        raise ValueError('A distinct exact reviewed reader is required')
    actual = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    if actual != runtime:
        raise ValueError('Source reader differs from reviewed activation')
    sys.path.insert(0, str(root))
    from scripts.scoped_archive_source_revalidation import REVISION
    if REVISION != 'exact_m3_20261110_latequote_st_observation_source_only_v3':
        raise ValueError('The original v2 archive cannot stand in for newly earned v3')
    previous = original.RUNTIME, original.TAG, original.NAME
    try:
        original.RUNTIME = runtime
        original.TAG = 'microcap-scoped-whole-20261110-' + runtime[:8]
        original.NAME = 'microcap-whole-delivery-state-archive-20261110-m7-observation.zip'
        return original.publish(root, bundle, evidence)
    finally:
        original.RUNTIME, original.TAG, original.NAME = previous


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('root', 'bundle', 'evidence', 'review'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(publish(args.root, args.bundle, args.evidence, args.review)))
