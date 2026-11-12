"""Exact local reader contract; execution and publication are distinct pins."""
import json
import re
from pathlib import Path


LOCAL_READ_POLICY = {
    'schema': 'member_daily_local_runtime_v1',
    'policy': 'ab214_reads_477_member_checkpoint_v2',
    'execution_strategy_sha': 'ab214c20d92ca9bbda1dde7e447bb153e7f59028',
    'checkpoint_strategy_sha': '4774966da38f0b5af18617f73cc67c39c9e41a8e',
    'core_strategy_sha': 'ad4de441d35442616f91aa613146ffdf8cd479f3',
    'rebalance_producer_shas': [
        '1634a27c5a609a46ab35fc29ca2c65b94182671b',
        '14e9a9fa6f0487f1d5547598435089c43f2f9fd1',
        'a7c1b9ac33454be4367880a0989dfc1466640503',
        '95d896fe58cc7cb84c0fcb7bd76534f62c9430f4',
        '906eca8a5dfede7fa1674fb17cd3c6ce12205e6d',
    ],
}


def validate_read_policy(value, execution_sha=None):
    # This is one reviewed relationship, not a configurable version alias list.
    if value != LOCAL_READ_POLICY or (execution_sha is not None
            and execution_sha != value['execution_strategy_sha']):
        raise ValueError('Unreviewed member checkpoint reader relationship')
    return dict(value)


def load_read_policy(path, execution_sha=None):
    return validate_read_policy(json.loads(Path(path).read_bytes()), execution_sha)


def local_runtime(automation_root):
    """Keep cloud publication config intact while selecting the local reader."""
    root = Path(automation_root)
    cloud = json.loads((root / 'config/microcap_member_runtime.json').read_bytes())
    path = root / 'config/microcap_member_local_runtime.json'
    if not path.exists():
        return cloud['strategy_sha'], None
    policy = load_read_policy(path)
    if (cloud.get('strategy_sha') != policy['checkpoint_strategy_sha']
            or cloud.get('core_strategy_sha') != policy['core_strategy_sha']):
        raise ValueError('Local checkpoint reader differs from the exact cloud runtime contract')
    return policy['execution_strategy_sha'], path


def validate_checkpoint_payload(checkpoint, policy):
    if policy is None:
        return
    marker = checkpoint.get('strategy_sha')
    if marker == policy['core_strategy_sha']:
        return
    if marker not in policy['rebalance_producer_shas']:
        raise ValueError('Checkpoint core identity or reviewed producer differs from the local reader contract')
    from microcap_recovery_handoff import M8,M9,MODE
    if marker in (M8,M9) and checkpoint.get('handoff_mode')!=MODE:
        raise ValueError('The reviewed M8/M9 producer requires its formal recovery proof route')
    # Native R handoff records the exact full-state producer, not the older
    # model-core marker. The 477 daily reader preserves this original lineage.
    if (checkpoint.get('handoff_mode') not in ('native_close_extends_previous', 'same_close_period_switch', 'native_formal_gap_recovery_v1')
            or not isinstance(checkpoint.get('handoff_from'), str)
            or re.fullmatch('[0-9a-f]{64}', checkpoint['handoff_from']) is None):
        raise ValueError('Reviewed rebalance checkpoint lacks its original handoff lineage')
    if checkpoint.get('handoff_mode')=='native_formal_gap_recovery_v1':
        from microcap_recovery_handoff import reviewed_controller_identity
        reviewed_controller_identity(checkpoint)


def validate_checkpoint_core(raw, policy):
    """Authenticate the original payload identity; never rewrite checkpoint bytes."""
    if policy is None:
        return
    import io
    import zipfile
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        checkpoint = json.loads(archive.read('checkpoint.json'))
    validate_checkpoint_payload(checkpoint, policy)
    from microcap_recovery_handoff import validate_recovery_payload
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        payload={key:archive.read(checkpoint[key]['path']) for key in ('period','history','turnover','version_state','prices')}
    validate_recovery_payload(checkpoint,payload)


def validate_checkpoint_directory(path, policy):
    """Apply the recovery proof gate to cached and freshly restored packages."""
    path = Path(path).resolve()
    checkpoint = json.loads(path.read_bytes())
    validate_checkpoint_payload(checkpoint, policy)
    from microcap_recovery_handoff import validate_recovery_payload
    payload = {}
    for role in ('period', 'history', 'turnover', 'version_state', 'prices'):
        name = checkpoint[role]['path']
        relative = Path(name)
        if (relative.is_absolute() or relative.drive or '..' in relative.parts
                or '\\' in name or ':' in name):
            raise ValueError('Cached recovery payload path escapes its package')
        target = path.parent / relative
        if (not target.resolve().is_relative_to(path.parent)
                or any(p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction())
                       for p in (target, *target.parents) if p.is_relative_to(path.parent))):
            raise ValueError('Cached recovery payload contains a link or escapes its package')
        payload[role] = target.read_bytes()
    validate_recovery_payload(checkpoint, payload)
