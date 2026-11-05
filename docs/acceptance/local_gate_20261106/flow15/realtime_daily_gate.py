"""Prepare and run the local daily gates; never run strategy or mail entrypoints.

The prepared fixture is frozen research evidence, not today's freshness proof.
Both preparation and daily validation authenticate bytes, not mtimes or a path.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import time
import zipfile
from datetime import datetime, timedelta
from contextlib import contextmanager
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET

HOME = Path(__file__).resolve().parents[1]
CONFIG = HOME / 'scripts/realtime_daily_gate.json'
ACTIVE = HOME / 'scripts/realtime_daily_active.json'
UNCONFIRMED_CHILDREN = []
CORE = '''test_realtime_preflight.py test_realtime_state_guard_20260915.py test_top100_delivery.py test_top100_cloud_delivery.py test_full_rebalance_cache_bundle.py test_top100_data_guards.py test_adversarial_microcap_delivery.py test_script_delivery_adversarial.py test_realtime_exchange_calendar.py test_exchange_calendar_provider.py test_realtime_publication_deadline.py test_v20_plain_promotion.py test_v23_plain_promotion.py test_v25_plain_promotion.py test_adversarial_v20_plain_20260904.py test_adversarial_v23_plain_20260904.py test_adversarial_delivery_plain_20260904.py test_adversarial_delivery_state_consistency.py test_v23_identity_adversarial.py test_index_history_preflight.py test_panel_exchange_session_guard_20261012.py test_security_meta_current_st_history_resolution_20261012.py test_security_meta_preserve_20260917.py test_adversarial_recovery_boundaries_20261013.py test_cninfo_history_fail_closed_20261015.py test_exchange_session_completeness_20261015.py test_return_integrity_20261015.py test_security_meta_fail_closed_20261015.py test_v25_realtime_artifact_invalidation.py test_v23_no_overheat_promotion.py'''.split()
RELEASE = '''test_close_confirmation_1500_20261023.py test_realtime_quote_integrity_20261024.py test_st_notice_state_machine_20261024.py test_st_notice_release_audit_20261024.py test_realtime_commit_guard_20261024.py test_whole_delivery_member_cache_gate_20261024.py'''.split()
SHARE_SOURCE = ['test_share_change_source.py', 'test_share_unchanged_tail_20261103.py']
# Preserved separately by the official delivery-regression workflow, outside 47.
ICIM_SUPPLEMENTAL = [
    'test_poe_ic_im_v1_3_state.py', 'test_run_ic_im_v1_3_github_digest.py',
    'test_external_review_fixes_20260911.py', 'test_ic_im_month_spreads_20261018.py',
    'quant_research_runs/20261018_monthly_spreads_adversarial_v1/data/test_independent_numeric_isolation.py',
]
assert len(CORE) == len(set(CORE)) == 30 and len(RELEASE) == 6
assert len(SHARE_SOURCE) == len(set(SHARE_SOURCE)) == 2 and not set(SHARE_SOURCE) & set(CORE + RELEASE)


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def now():
    return datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf8')
    temp.replace(path)


def run(cmd, **kwargs):
    kwargs.setdefault('timeout', 20 if Path(cmd[0]).stem == 'gh' else 60)
    return subprocess.check_output(cmd, text=True, encoding='utf8', **kwargs).strip()


@contextmanager
def coordinator_lock():
    path = HOME / 'runtime/local_daily_gate.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    # Existing owner is never killed or unlinked by a competing process.
    with path.open('x', encoding='utf8') as stream:
        stream.write(json.dumps({'pid': os.getpid(), 'started_at': now(), 'command': sys.argv}))
    try:
        yield
    finally:
        owner = json.loads(path.read_text(encoding='utf8'))
        if owner['pid'] == os.getpid() and not UNCONFIRMED_CHILDREN:
            path.unlink()


def pin_from_workflow(text, repository, dynamic_refs=None):
    refs = re.findall(r'repository:\s*' + re.escape(repository)
                      + r'\s+ref:\s*(\$\{\{[^\r\n]+?\}\}|[^\s#]+)', text)
    # The reviewed recovery checkout reads strategy_sha from the same immutable
    # approved-state config. Unknown expressions remain forbidden.
    refs = [(dynamic_refs or {}).get(ref, ref) for ref in refs]
    if not refs or any(re.fullmatch(r'[a-f0-9]{40}', ref) is None for ref in refs) or len(set(refs)) != 1:
        raise ValueError('all workflow strategy checkouts must bind one exact commit: ' + repository)
    return refs[0]


def consumer_bindings(root):
    """Bind exact workflow, seed and both consumer import closures."""
    names = {'.github/workflows/microcap-realtime-digest.yml', '.github/workflows/ic-im-v1-4-daily-digest.yml',
             'config/microcap_approved_state.json', 'docs/ic_im_daily_release_20261023_repairs_v3_r3_manifest.json'}
    queue = ['scripts/build_microcap_realtime_digest.py', 'scripts/build_ic_im_v1_4_digest.py']
    approved = json.loads(safe_path(root, 'config/microcap_approved_state.json').read_text(encoding='utf-8-sig'))
    recovery = approved.get('recovery_readiness')
    if recovery is not None:
        if not isinstance(recovery, dict) or recovery.get('path') != 'config/microcap_recovery_readiness.json':
            raise ValueError('invalid fixed recovery acceptance dependency')
        names.add(recovery['path'])
    if approved.get('daily_test_source') is not None:
        queue.append('scripts/verify_microcap_daily_test_source.py')
    # A stable workflow can invoke a changed recovery/dedupe helper.
    # Bind actual automation entrypoints as well as the renderers.
    for workflow in sorted(name for name in names if name.endswith('.yml')):
        text = safe_path(root, workflow).read_text(encoding='utf-8-sig')
        queue.extend(re.findall(r'automation/(scripts/[A-Za-z0-9_./-]+\.py)', text))
    while queue:
        name = queue.pop()
        if name in names:
            continue
        path = safe_path(root, name)
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        names.add(name)
        for node in ast.walk(tree):
            modules = [node.module or ''] if isinstance(node, ast.ImportFrom) else ([n.name for n in node.names] if isinstance(node, ast.Import) else [])
            for module in modules:
                rel = module.replace('.', '/') + '.py'
                for candidate in (rel, 'scripts/' + rel):
                    if (root / candidate).is_file() and candidate not in names:
                        queue.append(candidate)
    result = {}
    for name in names:
        data = safe_path(root, name).read_bytes().replace(b'\r\n', b'\n')
        result[name] = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
    return result


def verify_remote(receipt):
    remote = run(['gh', 'api', 'repos/liuruojiang/codex-daily-automation-probe/git/ref/heads/main', '--jq', '.object.sha'])
    if remote != receipt['automation_pin']:
        tree = json.loads(run(['gh', 'api', 'repos/liuruojiang/codex-daily-automation-probe/git/trees/' + remote + '?recursive=1']))
        if tree.get('truncated'):
            raise ValueError('remote tree incomplete')
        blobs = {x['path']: x['sha'] for x in tree['tree'] if x['type'] == 'blob'}
        changed = [k for k, v in receipt['consumer_bindings'].items() if blobs.get(k) != v]
        if changed:
            # Prepared consumer bindings use LF transport identity. Authenticate
            # raw Git objects first, then compare the same normalized identity.
            import base64
            for name in list(changed):
                blob_id = blobs.get(name)
                if blob_id is None:
                    continue
                obj = json.loads(run(['gh', 'api', 'repos/liuruojiang/codex-daily-automation-probe/git/blobs/' + blob_id]))
                if obj.get('encoding') != 'base64':
                    raise ValueError('unsupported Git blob encoding: ' + name)
                raw = base64.b64decode(obj['content'])
                def blob_sha(data):
                    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
                if blob_sha(raw) != blob_id:
                    raise ValueError('remote Git blob integrity mismatch: ' + name)
                if blob_sha(raw.replace(b'\r\n', b'\n')) == receipt['consumer_bindings'][name]:
                    changed.remove(name)
            if changed:
                raise ValueError('review/prepare changed workflow, seed or consumer dependencies: ' + repr(changed))
    return remote


def local_import_closure(root, entries):
    pending, seen = list(entries), set()
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        trees = [ast.parse(safe_path(root, name).read_text(encoding='utf-8-sig'))]
        for tree in trees:
            for node in ast.walk(tree):
                candidates = []
                if isinstance(node, ast.Import):
                    candidates += [x.name.replace('.', '/') + '.py' for x in node.names]
                elif isinstance(node, ast.ImportFrom):
                    module = (node.module or '').replace('.', '/')
                    if node.level:
                        base = Path(name).parent
                        for _ in range(node.level - 1): base = base.parent
                        module = (base / module).as_posix()
                    candidates += [module + '.py'] + [module + '/' + x.name + '.py' for x in node.names]
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    value = node.value
                    if value.endswith('.py') and len(value) < 200:
                        candidates.append(value)
                    if '\n' in value and 'import ' in value and len(value) < 2000000:
                        try: trees.append(ast.parse(value))
                        except SyntaxError: pass
                for candidate in candidates:
                    try: path = safe_path(root, candidate)
                    except ValueError: continue
                    canonical_name = path.relative_to(root.resolve()).as_posix()
                    if path.is_file() and canonical_name not in seen:
                        pending.append(canonical_name)
    return seen


def production_bindings(target):
    root = target / 'microcap'
    env = {**clean_test_env(), 'PYTHONPATH': str(root), 'PYTHONNOUSERSITE': '1', 'PYTHONDONTWRITEBYTECODE': '1'}
    names = json.loads(run([sys.executable, '-B', '-X', 'utf8', '-c',
        'import json; from scripts.top100_delivery import RELEASE_FILES; print(json.dumps(RELEASE_FILES))'], cwd=root, env=env))
    names = set(names) | local_import_closure(root, [name for name in names if name.endswith('.py')])
    micro = {}
    for name in names:
        data = safe_path(root, name).read_bytes()
        # This registered execution config follows stock source_fingerprint's
        # LF transport identity. All other authority/evidence JSON stays raw.
        normalized = name.endswith('.py') or name == 'data/share_source_future_plan_reviews.json'
        micro[name] = {'sha256': hashlib.sha256(data.replace(b'\r\n', b'\n') if normalized else data).hexdigest(), 'lf': normalized}
    manifest = json.loads((target / 'icim/docs/ic_im_daily_release_20261023_repairs_v3_r3_manifest.json').read_text(encoding='utf8'))
    return {'microcap': micro, 'icim': {name: {'sha256': value, 'lf': False} for name, value in manifest['files'].items()}}


def verify_production_sources(receipt):
    config = json.loads(CONFIG.read_text(encoding='utf8'))
    for strategy, files in receipt['production_bindings'].items():
        root = Path(config[strategy + '_source'])
        for name, binding in files.items():
            data = safe_path(root, name).read_bytes()
            actual = hashlib.sha256(data.replace(b'\r\n', b'\n') if binding['lf'] else data).hexdigest()
            if actual != binding['sha256']:
                raise ValueError('production source/authority differs from approved workflow snapshot: ' + strategy + '/' + name)


def safe_path(root, relative):
    p = (root / relative).resolve()
    if not p.is_relative_to(root.resolve()) or p == root.resolve():
        raise ValueError('unsafe inventory path: ' + relative)
    return p


def inventory(root):
    result = {}
    for directory, dirs, files in os.walk(root):
        for name in dirs + files:
            p = Path(directory) / name
            if name == '.git':
                raise ValueError('unexpected git metadata in archived source: ' + str(p))
            if p.is_symlink() or p.is_junction():
                raise ValueError('linked fixture path: ' + str(p))
        for name in files:
            p = Path(directory) / name
            if p.suffix in {'.pyc', '.pyo'} or '__pycache__' in p.parts:
                raise ValueError('untrusted compiled bytecode in prepared source: ' + str(p))
            result[p.relative_to(root).as_posix()] = sha(p)
    return result


def verify_inventory(root, expected):
    actual = inventory(root)
    if actual != expected:
        changed = sorted(k for k in set(actual) | set(expected) if actual.get(k) != expected.get(k))
        raise ValueError('prepared source/fixture inventory changed: ' + repr(changed[:12]))


def archive_checkout(source, pin, target, archive, *, exact_git_bytes=False):
    target.mkdir(parents=True, exist_ok=False)
    listing = run(['git', '-C', str(source), 'ls-tree', '-z', pin]).split('\0')
    selected = []
    for line in listing:
        if not line:
            continue
        kind, name = line.split('\t', 1)
        if kind.startswith('100') or name in {'data', 'docs', 'scripts', 'tests', 'poe_bots', '.github', 'config'}:
            selected.append(name)
    authority = 'outputs/microcap_top100_mom16_biweekly_live_v2_0_base_frozen_tail_authority.json'
    if subprocess.run(['git', '-C', str(source), 'cat-file', '-e', pin + ':' + authority], capture_output=True).returncode == 0:
        # This is versioned authority, supplied by the exact strategy commit;
        # the state ZIP intentionally does not replace it.
        selected.append(authority)
    settings = ['-c', 'core.autocrlf=false', '-c', 'core.eol=lf'] if exact_git_bytes else []
    subprocess.run(['git', *settings, '-C', str(source), 'archive', '--format=tar', '--output', str(archive), pin, '--', *selected], check=True)
    with tarfile.open(archive) as tf:
        for item in tf.getmembers():
            safe_path(target, item.name)
            if item.issym() or item.islnk():
                raise ValueError('linked source archive member')
        tf.extractall(target, filter='data')


def verify_installed_micro(config, root, pin):
    """Resume development preparation only after byte-verifying the entire subset."""
    listing = run(['git', '-C', config['microcap_source'], 'ls-tree', '-rz', pin]).split('\0')
    allowed = set()
    for line in listing:
        if not line:
            continue
        fields, name = line.split('\t', 1)
        top = name.split('/')[0]
        selected = '/' not in name or top in {'data', 'docs', 'scripts', 'tests', 'poe_bots', '.github', 'config'} or name.endswith('base_frozen_tail_authority.json')
        if not selected:
            continue
        allowed.add(name)
        mode, kind, blob = fields.split()
        if mode == '120000':
            raise ValueError('linked source')
        data = safe_path(root, name).read_bytes()
        actual = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        try:
            data.decode('utf8')
            text_file = b'\0' not in data
        except UnicodeDecodeError:
            text_file = False
        normalized = data.replace(b'\r\n', b'\n') if text_file and not name.startswith('outputs/') else data
        normalized_blob = hashlib.sha1(b'blob ' + str(len(normalized)).encode() + b'\0' + normalized).hexdigest()
        if actual != blob and normalized_blob != blob:
            raise ValueError('incomplete/changed source: ' + name)
    with zipfile.ZipFile(config['microcap_archive']) as z:
        seen = set()
        for item in z.infolist():
            if item.filename in seen:
                raise ValueError('duplicate ZIP member')
            seen.add(item.filename)
            if not item.is_dir() and selected_micro_fixture(item.filename):
                allowed.add(item.filename)
                with z.open(item) as stream:
                    expected = hashlib.file_digest(stream, 'sha256').hexdigest()
                if sha(safe_path(root, item.filename)) != expected:
                    raise ValueError('incomplete/changed authenticated fixture: ' + item.filename)
    actual = set(inventory(root))
    if actual != allowed:
        raise ValueError('source/fixture inventory not exact: ' + repr(sorted(actual ^ allowed)[:12]))
    compatibility(root)


def selected_micro_fixture(name):
    # Exact, explicitly bounded test fixture closure. This is not a production restore.
    if name == '.microcap_index_cache/st_notice_archive_pointer.json':
        # A production archive selector changes synthetic test semantics. The
        # immutable real-data baseline contract does not contain this selector.
        return False
    return name.startswith('outputs/') or (
        name.startswith('.microcap_index_cache/') and
        (len(Path(name).parts) == 2 or Path(name).parts[1] in
         {'prices_raw', 'prices_qfq', 'share_change', 'security_meta',
          'realtime_notice_audits'}))


def extract_fixture(archive, target):
    seen = set()
    with zipfile.ZipFile(archive) as z:
        for item in z.infolist():
            if item.filename in seen:
                raise ValueError('duplicate ZIP inventory member')
            seen.add(item.filename)
            p = safe_path(target, item.filename)
            if item.is_dir() or not selected_micro_fixture(item.filename):
                continue
            p.parent.mkdir(parents=True, exist_ok=True)
            with z.open(item) as src, p.open('wb') as dst:
                import shutil
                shutil.copyfileobj(src, dst)


def copy_fixture(source, target, relative):
    import shutil
    src, dst = safe_path(source, relative), safe_path(target, relative)
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)
    elif src.is_file():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    else:
        raise FileNotFoundError('required fixture: ' + str(src))
    if src.is_dir():
        if inventory(src) != inventory(dst):
            raise ValueError('fixture source changed or incomplete copy: ' + relative)
    elif sha(src) != sha(dst):
        raise ValueError('fixture source changed: ' + relative)


def install_icim_bound_files(config, root):
    manifest = json.loads((root / 'docs/ic_im_daily_release_20261023_repairs_v3_r3_manifest.json').read_text(encoding='utf8'))
    for rel, expected in {**manifest['files'], **manifest.get('fixture_sha256', {})}.items():
        dest = safe_path(root, rel)
        if dest.is_file() and sha(dest) == expected:
            continue
        source = safe_path(Path(config['icim_source']), rel)
        if not source.is_file() or sha(source) != expected:
            raise ValueError('missing/unapproved ICIM manifest-bound dependency: ' + rel)
        copy_fixture(Path(config['icim_source']), root, rel)
    for rel in manifest['required_local_tests'] + manifest['required_test_helpers']:
        if not safe_path(root, rel).is_file():
            raise ValueError('missing ICIM test/helper: ' + rel)


def compatibility(root):
    manifest = json.loads((root / 'outputs/top100_delivery_manifest.json').read_text(encoding='utf-8-sig'))
    if not (manifest.get('ok') and manifest.get('status') == 'complete' and manifest.get('scope') == 'whole_workspace_delivery'):
        raise ValueError('incomplete authenticated fixture manifest')
    successor = {}
    approval_path = root.parent / 'preparation_tools/microcap_approved_state.json'
    if approval_path.is_file():
        approved = json.loads(approval_path.read_bytes())
        transition = approved.get('approved_source_successor')
        if transition is not None:
            allowed = {'scripts/share_unchanged_tail.py', 'data/share_source_future_plan_reviews.json',
                       'scripts/realtime_state_bundle.py', 'scripts/sse_share_daily_unchanged_20261104.py'}
            if (transition.get('bootstrap_sha') != '6fa0b9d73b12fff548200d987c953d1b1a7d74d7'
                    or not ((transition.get('approval_package_sha256') == '3af53c7fea86b3334321112981747560e9fcea9dba5ee7667c9f011d8a70f4ff'
                             and set(transition.get('changes', {})) in (allowed, allowed | {'scripts/st_notice_boundary_review.py'}))
                            or (transition.get('approval_package_sha256') == '7fe387b7a1fbcd8548fff0533852c6421d7b3c59502cee361d0da86734b18dad'
                                and set(transition.get('changes', {})) in (
                                    allowed | {'scripts/st_notice_boundary_review.py', 'scripts/share_change_source.py'},
                                    allowed | {'scripts/st_notice_boundary_review.py', 'scripts/share_change_source.py', 'microcap_top100_mom16_biweekly_live_v2_0.py'})))
                    or manifest.get('release_sha') != transition['bootstrap_sha']
                    or manifest['expected_date'] != approved.get('state_date')
                    or approved.get('state_sha256') != '6155ca0cc8ca94481cf41ce92af3f6d337549c5543a95dd9eb6e903d38944f7b'):
                raise ValueError('unapproved frozen fixture source successor')
            successor = transition['changes']
    for name, expected in {**manifest['inputs'], **manifest['artifacts']}.items():
        data = safe_path(root, name).read_bytes()
        if not name.startswith('data/st_notice_evidence/'):
            data = data.replace(b'\r\n', b'\n')
        actual = hashlib.sha256(data).hexdigest()
        # Authenticate old economic data with its original manifest. Only the
        # exact reviewed source pair may differ; never rewrite that manifest.
        if actual != expected and not (name in manifest['inputs'] and name in successor
                                       and successor[name] == [expected, actual]):
            raise ValueError('fixture manifest binding mismatch: ' + name)
    meta = json.loads((root / 'outputs/microcap_top100_mom16_biweekly_live_v2_0_base_proxy_meta.json').read_text(encoding='utf8'))
    policy = 'delist_zero_writeoff_equal_survivors_v1'
    if meta.get('delisting_valuation_policy') != policy or meta.get('core_params', {}).get('delisting_valuation_policy') != policy:
        raise ValueError('fixture incompatible with current terminal valuation policy')
    return manifest


def microcap_test_source(config, auto, auto_pin, approved, runtime_pin, target):
    """Authenticate a separately published test source; never change runtime identity."""
    declaration = approved.get('daily_test_source')
    if declaration is None:
        return runtime_pin, {}
    if not isinstance(declaration, dict) or declaration.get('runtime_sha') != runtime_pin:
        raise ValueError('test-source declaration differs from production runtime')
    test_pin = declaration.get('source_sha', '')
    if not isinstance(test_pin, str) or re.fullmatch('[a-f0-9]{40}', test_pin) is None:
        raise ValueError('test-source commit must be an exact published Git object')
    tools_dir = safe_path(target, 'preparation_tools')
    tools_dir.mkdir(parents=True, exist_ok=True)
    exported = {}
    for relative in ('scripts/verify_microcap_daily_test_source.py', 'config/microcap_approved_state.json'):
        raw = subprocess.check_output(['git', '-C', str(auto), 'show', auto_pin + ':' + relative], timeout=60)
        path = safe_path(tools_dir, Path(relative).name)
        if path.exists() and path.read_bytes() != raw:
            raise ValueError('previous preparation tool differs from fixed automation source')
        if not path.exists():
            path.write_bytes(raw)
        exported[relative] = path
    verifier = exported['scripts/verify_microcap_daily_test_source.py']
    declaration_path = exported['config/microcap_approved_state.json']
    if json.loads(declaration_path.read_bytes()) != approved:
        raise ValueError('exported test-source configuration differs from fixed Git configuration')
    verifier_before, config_before = sha(verifier), sha(declaration_path)
    proof = tools_dir / ('test_source_verified_' + datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%dT%H%M%S%f') + '.json')
    if proof.exists():
        raise ValueError('test-source proof output already exists')
    env = {**clean_test_env(), 'PYTHONNOUSERSITE': '1', 'PYTHONDONTWRITEBYTECODE': '1'}
    run([sys.executable, '-B', '-X', 'utf8', str(verifier), 'verify',
         '--runtime-root', str(Path(config['microcap_source']).resolve()),
         '--config', str(declaration_path), '--out', str(proof)], cwd=tools_dir, env=env, timeout=60)
    if sha(verifier) != verifier_before or sha(declaration_path) != config_before:
        raise ValueError('fixed test-source verifier or configuration changed during verification')
    binding = {'runtime_sha': runtime_pin, 'test_source_sha': test_pin, 'automation_pin': auto_pin,
               'proof_path': proof.relative_to(target).as_posix(), 'proof_sha256': sha(proof),
               'verifier_path': verifier.relative_to(target).as_posix(), 'verifier_sha256': verifier_before,
               'config_path': declaration_path.relative_to(target).as_posix(), 'config_sha256': config_before}
    validate_microcap_test_source_binding(target, binding, approved, runtime_pin)
    return test_pin, binding


def validate_microcap_test_source_binding(target, binding, approved, runtime_pin):
    declaration = approved.get('daily_test_source')
    if not declaration:
        if binding:
            raise ValueError('test source was not declared by fixed automation configuration')
        return
    if not binding or binding.get('runtime_sha') != runtime_pin or binding.get('test_source_sha') != declaration.get('source_sha'):
        raise ValueError('runtime and test source roles are missing or inconsistent')
    if declaration.get('runtime_sha') != runtime_pin:
        raise ValueError('test-source runtime declaration changed')
    for stem in ('proof', 'verifier', 'config'):
        path = safe_path(target, binding[stem + '_path'])
        if sha(path) != binding[stem + '_sha256']:
            raise ValueError('test-source ' + stem + ' bytes changed')
    if json.loads(safe_path(target, binding['config_path']).read_bytes()) != approved:
        raise ValueError('test-source configuration no longer matches the consumer')
    proof = json.loads(safe_path(target, binding['proof_path']).read_bytes())
    if (proof.get('schema') != 'microcap_daily_test_source_verification_v1'
            or proof.get('ok') is not True or proof.get('verified') is not True
            or proof.get('runtime_equivalent') is not True or proof.get('git_objects_verified') is not True
            or proof.get('origin') != 'https://github.com/liuruojiang/microcap.git'
            or proof.get('verifier_sha256') != binding['verifier_sha256']
            or proof.get('config_sha256') != binding['config_sha256']
            or proof.get('runtime_sha') != runtime_pin
            or proof.get('test_source_sha') != declaration['source_sha']):
        raise ValueError('complete successful test-source equivalence proof required')



def icim_supplemental_bindings(root):
    # Complete imported local source closure; fixtures also remain covered by
    # the complete prepared inventory and existing authenticated manifest.
    names = local_import_closure(root, ICIM_SUPPLEMENTAL)
    return {name: sha(safe_path(root, name)) for name in sorted(names)}


def install_icim_supplemental(source, pin, root):
    # The nested independent test is not in archive_checkout's bounded trees.
    # Read all five from the same immutable workflow pin, never local dirt.
    for name in ICIM_SUPPLEMENTAL:
        data = subprocess.run(['git', '-C', str(source), 'show', pin + ':' + name],
                              capture_output=True, check=True).stdout
        dest = safe_path(root, name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    return icim_supplemental_bindings(root)


def icim_supplemental_group(target, out, worker):
    root = target / 'icim'
    before = icim_supplemental_bindings(root)
    tmp = out / 'icim_supplemental_temp'
    tmp.mkdir()
    env = {**clean_test_env(), 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1',
           'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1', 'PYTEST_ADDOPTS': '',
           'ICIM_REQUIRE_MIGRATION': '0', 'ICIM_STATE_DIR': str(tmp / 'state'),
           'ICIM_DISABLE_EVIDENCE_CAPTURE': '1', 'ICIM_DISABLE_INTERNAL_REFRESH': '1',
           'PYTHONPATH': str(root), 'TEMP': str(tmp), 'TMP': str(tmp),
           'HYPOTHESIS_STORAGE_DIRECTORY': str(tmp / 'hypothesis'),
           'PYTHONPYCACHEPREFIX': str(tmp / 'bytecode')}
    group = out / 'icim_supplemental5'
    result = subprocess_gate([sys.executable, '-B', '-X', 'utf8', str(worker), str(group),
                              *ICIM_SUPPLEMENTAL], root, env, group, 'icim_supplemental5', timeout=180)
    result['pass'] = False
    result['source_before'] = before
    result['test_files'] = list(ICIM_SUPPLEMENTAL)
    try:
        after = icim_supplemental_bindings(root)
        result['source_after'] = after
        result['counts'] = junit_counts(group / 'regression.xml', ICIM_SUPPLEMENTAL)
        validate_collection(group / 'collection.json', result['counts'])
        collection = json.loads((group / 'collection.json').read_text(encoding='utf8'))
        actual_files = {node.split('::', 1)[0] for node in collection['selected']}
        if actual_files != set(ICIM_SUPPLEMENTAL):
            raise ValueError('supplemental collection differs from the approved five files')
        result['pass'] = (result['exit_code'] == 0 and not result.get('timed_out', False)
                          and before == after and result['counts']['tests'] > 0
                          and all(result['counts'][key] == 0 for key in ('failures', 'errors', 'skipped')))
        result['artifacts'] = {name: sha(group / name) for name in ('regression.xml', 'collection.json')}
    except Exception as exc:
        result['validation_error'] = str(exc)
    write(group / 'gate.json', result)
    return result


def prepare(config, target, resume=False):
    """One-time/off-window preparation. Never reconstruct a historical manifest."""
    target.mkdir(parents=True, exist_ok=resume)
    if (target / 'prepared.json').exists():
        raise ValueError('already sealed; create a new preparation instead')
    auto = Path(config['automation_source'])
    auto_pin = run(['git', '-C', str(auto), 'rev-parse', 'HEAD'])
    workflows = {}
    approved_config = json.loads(run(['git', '-C', str(auto), 'show', auto_pin + ':config/microcap_approved_state.json']))
    for key, file, repo in [('microcap', 'microcap-realtime-digest.yml', 'liuruojiang/microcap'),
                            ('icim', 'ic-im-v1-4-daily-digest.yml', 'liuruojiang/ic-im-rolling-arbitrage')]:
        text = run(['git', '-C', str(auto), 'show', auto_pin + ':.github/workflows/' + file])
        aliases = {'${{ steps.recovery_pin.outputs.sha }}': approved_config['strategy_sha']} if key == 'microcap' else {}
        workflows[key] = pin_from_workflow(text, repo, aliases)
    if workflows['microcap'] != approved_config['strategy_sha']:
        raise ValueError('workflow and approved-state strategy pins differ')
    archive = Path(config['microcap_archive'])
    if sha(archive) != config['microcap_archive_sha256']:
        raise ValueError('unapproved fixture archive SHA256')
    test_pin, test_binding = microcap_test_source(config, auto, auto_pin, approved_config, workflows['microcap'], target)
    micro, icim = target / 'microcap', target / 'icim'
    if resume:
        verify_installed_micro(config, micro, test_pin)
    else:
        if test_binding:
            archive_checkout(Path(config['microcap_source']), test_pin, micro, target / 'microcap.tar', exact_git_bytes=True)
        else:
            archive_checkout(Path(config['microcap_source']), test_pin, micro, target / 'microcap.tar')
        print('Microcap exact source and evidence extracted; installing authenticated frozen dataset.', flush=True)
        extract_fixture(archive, micro)
    manifest = compatibility(micro)
    print('Microcap fixture manifest and terminal policy authenticated.', flush=True)
    # All tracked evidence, helpers and data are part of git archive; no directory allowlist.
    missing = [str(micro / 'tests' / x) for x in CORE + RELEASE + SHARE_SOURCE if not (micro / 'tests' / x).is_file()]
    if missing:
        raise ValueError('missing daily tests: ' + repr(missing))
    if resume and icim.is_dir() and not any(icim.iterdir()):
        icim.rmdir()
    archive_checkout(Path(config['icim_source']), workflows['icim'], icim, target / 'icim.tar')
    for rel in config['icim_extra_fixtures']:
        copy_fixture(Path(config['icim_source']), icim, rel)
    install_icim_bound_files(config, icim)
    supplemental = install_icim_supplemental(Path(config['icim_source']), workflows['icim'], icim)
    runner = icim / 'run_ic_im_v1_4_delivery_tests.py'
    before = runner.read_bytes()
    runner.with_suffix('.published.py').write_bytes(before)
    text = before.decode('utf8')
    for old, new in [('child.wait(timeout=60)', 'child.wait(timeout=180)'),
                     ("'timeout_seconds': 60", "'timeout_seconds': 180"),
                     ('exceeded the 60-second child limit', 'exceeded the 180-second child limit')]:
        if old not in text:
            raise ValueError('published runner adaptation no longer applicable: ' + old)
        text = text.replace(old, new)
    runner.write_text(text, encoding='utf8', newline='\n')
    consumer = target / 'consumer'
    archive_checkout(auto, auto_pin, consumer, target / 'consumer.tar')
    guard_dir = target / 'guard'
    guard_dir.mkdir()
    import shutil
    shutil.copy2(HOME / 'scripts/daily_test_guard.py', guard_dir / 'sitecustomize.py')
    receipt = {'schema': 1, 'prepared_at': now(), 'automation_pin': auto_pin,
               'strategy_pins': workflows, 'fixture_date': manifest['expected_date'],
               'test_source_bindings': {'microcap': test_binding} if test_binding else {},
               'fixture_manifest_sha256': sha(micro / 'outputs/top100_delivery_manifest.json'),
               'fixture_archive_sha256': sha(archive), 'fixture_scope': 'frozen_offline_regression_only',
               'consumer_bindings': consumer_bindings(consumer),
               'production_bindings': production_bindings(target),
               'icim_supplemental_bindings': supplemental,
               'icim_supplemental_tests': list(ICIM_SUPPLEMENTAL),
               'icim_runner_published_sha256': hashlib.sha256(before).hexdigest(),
               'icim_runner_adapted_sha256': sha(runner),
               'inventories': {name: inventory(target / name) for name in
                               (('microcap', 'icim', 'consumer', 'guard', 'preparation_tools') if test_binding
                                else ('microcap', 'icim', 'consumer', 'guard'))}}
    write(target / 'prepared.json', receipt)
    return receipt


def validate_prepared(target):
    receipt = json.loads((target / 'prepared.json').read_text(encoding='utf8'))
    for key, files in receipt['inventories'].items():
        verify_inventory(target / key, files)
    if receipt.get('icim_supplemental_tests') != ICIM_SUPPLEMENTAL or receipt.get('icim_supplemental_bindings') != icim_supplemental_bindings(target / 'icim'):
        raise ValueError('missing or changed approved ICIM supplemental dependencies')
    compatibility(target / 'microcap')
    approved = json.loads((target / 'consumer/config/microcap_approved_state.json').read_bytes())
    validate_microcap_test_source_binding(target, receipt.get('test_source_bindings', {}).get('microcap', {}),
                                         approved, receipt['strategy_pins']['microcap'])
    return receipt


def junit_counts(path, files):
    tree = ET.parse(path).getroot()
    suites = tree.findall('.//testsuite')
    if tree.tag == 'testsuite':
        suites = [tree]
    counts = {k: sum(int(s.get(k, 0)) for s in suites) for k in ('tests', 'failures', 'errors', 'skipped')}
    cases = tree.findall('.//testcase')
    actual = {key: sum(1 for c in cases if c.find(tag) is not None) for key, tag in
              [('failures', 'failure'), ('errors', 'error'), ('skipped', 'skipped')]}
    if any(actual[key] != counts[key] for key in actual):
        raise ValueError('JUnit aggregates disagree with testcase outcomes')
    missing = [f for f in files if not any(c.get('classname', '').split('.')[-1] == Path(f).stem for c in cases)]
    if missing or counts['tests'] != len(cases):
        raise ValueError('incomplete JUnit collection: ' + repr(missing))
    return counts


def validate_collection(path, counts):
    proof = json.loads(path.read_text(encoding='utf8'))
    selected = proof['selected']
    if not selected or len(set(selected)) != len(selected) or proof['deselected'] or proof['collected'] != selected or proof['executed'] != selected or len(selected) != counts['tests']:
        raise ValueError('test selection/execution incomplete or unexpectedly filtered')


def clean_test_env():
    allowed = {'PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'SYSTEMDRIVE', 'TEMP', 'TMP',
               'USERPROFILE', 'HOMEDRIVE', 'HOMEPATH', 'APPDATA', 'LOCALAPPDATA', 'PROGRAMDATA',
               'PROGRAMFILES', 'PROGRAMFILES(X86)', 'NUMBER_OF_PROCESSORS', 'PROCESSOR_ARCHITECTURE', 'OS',
               'LANG', 'LC_ALL', 'TZ'}
    return {k: v for k, v in os.environ.items() if k.upper() in allowed}


def guard_evidence(path):
    events = [json.loads(line) for line in path.read_text(encoding='utf8').splitlines() if line.strip()]
    kinds = {event['kind'] for event in events}
    blocked = kinds & {'NETWORK_BLOCKED', 'PRODUCTION_WRITE_BLOCKED', 'EXTERNAL_SUBPROCESS_BLOCKED', 'NETWORK_SUBPROCESS_BLOCKED'}
    return 'GUARD_STARTED' in kinds, sorted(blocked)


def independent_calendar(root):
    # This import is the registered calendar, not a price-derived calendar.
    config = json.loads(CONFIG.read_text(encoding='utf8'))
    actual = hashlib.sha256((root / 'poe_ic_im_mainline_v1_4_bot.py').read_bytes().replace(b'\r\n', b'\n')).hexdigest()
    if actual != config['calendar_module_lf_sha256']:
        raise ValueError('formal calendar module differs from reviewed source')
    sys.path.insert(0, str(root))
    import poe_ic_im_mainline_v1_4_bot as formal
    clock = datetime.now(ZoneInfo('Asia/Shanghai'))
    formal._require_official_exchange_calendar(clock.date(), 'local daily start')
    return {'checked_at': clock.isoformat(), 'today': clock.date().isoformat(),
            'is_trading_day': formal._is_exchange_trading_day(clock.date()),
            'latest_completed_session': formal._latest_completed_exchange_day(clock).isoformat(),
            'next_session': formal._roll_forward_exchange_day(clock.date() + timedelta(days=1), require_official=True).isoformat()}


def check_isolation(target, out, config):
    protected = [Path(config[k]).resolve() for k in ('microcap_source', 'icim_source', 'automation_source')]
    paths = [target.resolve()] + ([out.resolve()] if out is not None else [])
    for path in paths:
        for prod in protected:
            if path.is_relative_to(prod) or prod.is_relative_to(path):
                raise ValueError('test path overlaps protected source: ' + str(path))
    if out is not None and (out.resolve().is_relative_to(target.resolve()) or target.resolve().is_relative_to(out.resolve())):
        raise ValueError('test output and prepared snapshot must be disjoint')


def subprocess_gate(cmd, cwd, env, out, label, timeout=180):
    out.mkdir(parents=True, exist_ok=False)
    record = {'label': label, 'start': now(), 'command': cmd, 'cwd': str(cwd), 'hard_timeout_seconds': timeout}
    tick = time.monotonic()
    interrupt = None
    with (out / 'process.log').open('w', encoding='utf8') as log:
        p = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT)
        record['pid'] = p.pid
        try:
            record['exit_code'] = p.wait(timeout=timeout)
        except BaseException as exc:
            if isinstance(exc, subprocess.TimeoutExpired):
                record.update(exit_code=124, timed_out=True)
            else:
                record.update(exit_code=1, wait_error=type(exc).__name__ + ': ' + str(exc))
            if not isinstance(exc, Exception):
                interrupt = exc
            try:
                if p.poll() is None:
                    killed = subprocess.run(['taskkill', '/PID', str(p.pid), '/T', '/F'], capture_output=True, timeout=10)
                    record['termination_exit_code'] = killed.returncode
                    p.wait(timeout=5)
                    if killed.returncode != 0:
                        raise RuntimeError('child-tree termination was not confirmed')
                record['termination_confirmed'] = True
            except Exception as exc:
                record['termination_confirmed'] = False
                record['termination_error'] = str(exc)
                UNCONFIRMED_CHILDREN.append(p.pid)
    record.update(end=now(), elapsed_seconds=time.monotonic() - tick)
    write(out / 'process.json', record)
    if interrupt is not None:
        raise interrupt
    return record



# This compatibility exception is deliberately one-time and source-bound.
# It cannot promote another failed gate or bypass the normal daily full run.
SHARE_RESUME_REVIEW = {'gate': 'D:/ct/icim_gate_20261105/flow_final_0241/gate.json', 'gate_sha256': '16469f50fc9c2d3229547781da0ad3c2b25f18abf24976142a1abedb28b7a70c', 'old_launcher_sha256': '33a0672f0fda884d81f281e17e6263946f48f8bc069d807f4fc04c5d57986438', 'lifecycle_record': 'D:\\Codex\\test-lifecycle\\records\\e11379ce4d8c4d89a19f4005c46d7616', 'lifecycle_sha256': {'run.json': 'f9ddc048777088e4b392cacb63c6b829d1710295de72e9817d578dd5546de58b', 'evidence-manifest.json': '6e7a78c89196a14752e21ff90f260c886aec9d7eb6afdc0afc6b071fe4022da5', 'acceptance.zip': '97efe52ef271cbb60ac185420de3b037319f2ef9bb133a6b4d34e3cc86a33506'}}


CORE_RESUME_REVIEW = {'gate': 'D:\\ct\\icim_gate_20261105\\flow9_acceptance\\gate.json', 'gate_sha256': 'c6a354c5731737a4f3eec2086359d7d2f00a7faa175fb030ed0c970c584d804a', 'old_launcher_sha256': 'abc0e0787e31642dd03d50c1088982082800792550a2bb5e17c0fca6cb36ed56', 'lifecycle_record': 'D:\\Codex\\test-lifecycle\\records\\dc101208aa6f4f4bbb2d4675e92bb7e2', 'lifecycle_sha256': {'run.json': 'a835de942bd9f43990a03fa438cf0cb284bbc410e962f69d19fc36a00aa0bd8c', 'evidence-manifest.json': '047105e4a3d1f4b900d00b1b024d5dd26d06aff649c01d1b029f61e5935b78bb', 'acceptance.zip': '2d07fa36e95e53ae7a080ccd2fd7b6bef05977682ea8b3768bdc2cbe8b1bdbd7'}, 'evidence_files': 144, 'scheduling_only_worker_change': {'before': 'aeeb2b63218f71177e94ae064f9b4eb0b4310263610f09b160671dfeac57a9a1', 'after': '52a8f33a84b65c5b99be2131dc24a3b7d21865207090165a6f429824ad83b7ba'}}


def _read_json(path):
    return json.loads(path.read_text(encoding='utf8'))


def _same_day_clock(*values):
    stamps = [datetime.fromisoformat(value) for value in values]
    current = datetime.now(ZoneInfo('Asia/Shanghai'))
    if any(value.tzinfo is None or value.astimezone(ZoneInfo('Asia/Shanghai')).date() != current.date()
           for value in stamps) or stamps != sorted(stamps) or stamps[-1] > current:
        raise ValueError('resume requires ordered same-day, nonfuture clocks')


ICIM_UNCHANGED_REUSE_REVIEW = {'gate': 'D:\\ct\\icim_gate_20261106\\flow10_acceptance\\gate.json', 'gate_sha256': '25d6644cb0263582c967a9469707f8e0bdff7d2bc0ec37adcbfa2c66efb61ac6', 'lifecycle_record': 'D:\\Codex\\test-lifecycle\\records\\1e2dcc95acac4ff1b0cd15d00108c968', 'lifecycle_sha256': {'run.json': 'df0a3f74e16903b180fdb78c65bc9cdf3643d55d4e28a327f2acd433a17c6ad4', 'evidence-manifest.json': '83fee6d69791093b75c2e37eebe0040f984063fed8a7f90cbe746511206c6246', 'acceptance.zip': 'e46549bbace10cf45736a66eac65adebd414c68d9598c7e34e1581c63e936049'}, 'evidence_files': 155, 'exit_code': 0, 'prepared': 'D:\\CodexDaily\\flow10\\prepared.json', 'prepared_sha256': 'a549bfffc804a980c7fb3dabad5a7a538649edcc2fe477d3641a11251088aeed'}


def _resume_archive(source, review=None):
    review = SHARE_RESUME_REVIEW if review is None else review
    if source.resolve() != Path(review['gate']).resolve() or sha(source) != review['gate_sha256']:
        raise ValueError('resume source is not the exact reviewed failed gate')
    records = Path(review['lifecycle_record'])
    for name, digest in review['lifecycle_sha256'].items():
        if sha(records / name) != digest:
            raise ValueError('original lifecycle evidence changed: ' + name)
    run_record = _read_json(records / 'run.json')
    if run_record['state'] != 'completed' or run_record['exit_code'] != review.get('exit_code', 1) or Path(run_record['gate_out']).resolve() != source.parent.resolve():
        raise ValueError('original gate process was not confirmed completed')
    entries = _read_json(records / 'evidence-manifest.json')
    if len(entries) != review.get('evidence_files', 155) or len({row['path'] for row in entries}) != len(entries):
        raise ValueError('original archived evidence inventory incomplete')
    with zipfile.ZipFile(records / 'acceptance.zip') as archive:
        expected = [f'{index:05d}-' + Path(row['path']).name for index, row in enumerate(entries)]
        if archive.namelist() != expected + ['manifest.json'] or json.loads(archive.read('manifest.json')) != entries:
            raise ValueError('original acceptance archive inventory differs')
        for member, row in zip(expected, entries):
            path = Path(row['path'])
            if not path.resolve().is_relative_to(source.parent.resolve()) or path.is_symlink():
                raise ValueError('unsafe original evidence path')
            data = archive.read(member)
            if len(data) != row['size'] or hashlib.sha256(data).hexdigest() != row['sha256']:
                raise ValueError('original archive evidence hash differs')
            if path.stat().st_size != row['size'] or sha(path) != row['sha256']:
                raise ValueError('original gate evidence missing or changed: ' + str(path))
    return {'gate_path': str(source.resolve()), 'gate_sha256': review['gate_sha256'],
            'lifecycle_record': str(records), 'lifecycle_sha256': review['lifecycle_sha256'],
            'evidence_files': len(entries)}


def _resume_group(root, label, expected, files, *, collection_root=None, allowed_deselections=()):
    directory = root / label
    if _read_json(directory / 'gate.json') != expected or not expected.get('pass'):
        raise ValueError('original group does not match the complete successful gate: ' + label)
    process = _read_json(directory / 'process.json')
    if any(expected.get(key) != value for key, value in process.items()) or process['exit_code'] != 0 or process.get('timed_out') or process.get('termination_confirmed') is False:
        raise ValueError('original group process did not finish successfully: ' + label)
    _same_day_clock(process['start'], process['end'])
    evidence = root / collection_root if collection_root else directory
    counts = junit_counts(evidence / 'regression.xml', files)
    reported = expected['official_receipt']['counts'] if label == 'icim' else expected['counts']
    if counts != reported or counts['tests'] <= 0 or any(counts[key] for key in ('failures','errors','skipped')):
        raise ValueError('original group XML is not a complete success: ' + label)
    proof = _read_json(evidence / 'collection.json')
    selected, collected = proof['selected'], proof['collected']
    if (not selected or len(set(selected)) != len(selected) or len(set(collected)) != len(collected)
        or proof['executed'] != selected or len(selected) != counts['tests']
        or set(proof['deselected']) != set(allowed_deselections)
        or len(proof['deselected']) != len(allowed_deselections)
        or set(collected) != set(selected) | set(proof['deselected'])
        or set(selected) & set(proof['deselected'])
        or {node.split('::',1)[0] for node in selected} != set(files)):
        raise ValueError('original group collection or execution incomplete: ' + label)
    if label in ('core30', 'release6', 'share_source2'):
        started, blocked = guard_evidence(root / (label + '_guard.log'))
        if not started or blocked or not expected['guard_started'] or expected['guard_blocked']:
            raise ValueError('original offline guard rejected a reused group')
    return counts


def validate_share_resume(source, target, out, receipt, remote, bindings):
    root = source.resolve().parent
    if out.resolve().is_relative_to(root) or root.is_relative_to(out.resolve()):
        raise ValueError('resume output must not overlap original evidence')
    evidence = _resume_archive(source)
    previous = _read_json(source)
    _same_day_clock(previous['started_at'], previous['completed_at'])
    if previous.get('pass') is not False or previous.get('calendar') is not None or previous.get('release_for_intraday') is not False or previous.get('resume'):
        raise ValueError('only the original failed maintenance gate may resume')
    if previous.get('launcher_sha256') != SHARE_RESUME_REVIEW['old_launcher_sha256']:
        raise ValueError('unreviewed launcher compatibility')
    if any(previous.get(key) != value for key, value in bindings.items()):
        raise ValueError('resume helper/config/runtime binding changed')
    for key in ('automation_pin', 'strategy_pins', 'test_source_bindings'):
        if previous.get(key, {}) != receipt.get(key, {}):
            raise ValueError('resume prepared source identity changed: ' + key)
    if previous['prepared_receipt_sha256'] != sha(target / 'prepared.json') or remote != previous['observed_automation_main']:
        raise ValueError('resume prepared or current remote pin changed')
    groups = {row['label']: row for row in previous['microcap']}
    if len(groups) != len(previous['microcap']) or set(groups) != {'core30','release6','share_source2'}:
        raise ValueError('original microcap groups incomplete')
    failed = groups['share_source2']
    if failed.get('pass') is not False or failed.get('exit_code') != 1 or failed.get('counts') != dict(tests=128,failures=1,errors=0,skipped=0):
        raise ValueError('not the reviewed share-source path failure')
    _resume_group(root,'core30',groups['core30'],['tests/'+name for name in CORE])
    _resume_group(root,'release6',groups['release6'],['tests/'+name for name in RELEASE])
    _resume_group(root,'icim_supplemental5',previous['icim_supplemental5'],ICIM_SUPPLEMENTAL)
    manifest = _read_json(target / 'icim/docs/ic_im_daily_release_20261023_repairs_v3_r3_manifest.json')
    _resume_group(root,'icim',previous['icim'],manifest['required_local_tests'],
                  collection_root='icim_results',allowed_deselections=manifest['daily_gate_deselections'])
    official = _read_json(root / 'icim_results/receipt.json')
    if official != previous['icim']['official_receipt'] or not official['source_before_equals_after'] or official['exit_code'] != 0:
        raise ValueError('original official ICIM receipt rejected')
    for name,digest in official['artifacts'].items():
        if sha(safe_path(root / 'icim_results',name)) != digest:
            raise ValueError('original ICIM artifact mismatch')
    if _read_json(root/'icim_results/source_before.json') != _read_json(root/'icim_results/source_after.json'):
        raise ValueError('original ICIM source changed during tests')
    supplemental = previous['icim_supplemental5']
    if supplemental['source_before'] != supplemental['source_after'] or supplemental['source_after'] != receipt['icim_supplemental_bindings']:
        raise ValueError('original supplemental dependencies differ')
    group = _read_json(root/'core30/group.json')
    if not group['all_expected_nodeids_executed_once'] or any(group['exit_codes']):
        raise ValueError('original core shards did not finish successfully')
    return previous, evidence



def _resume_processes_finished(run_record):
    """Read actual recorded process identities; unknown/live originals fail closed."""
    import ctypes
    from ctypes import wintypes
    if os.name != 'nt':
        raise ValueError('This exact Windows core resume needs Windows process readback')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    identities = [run_record['owner'], *run_record['children']]
    for item in identities:
        pid, created_at = item.get('pid'), item.get('created')
        if type(pid) is not int or pid <= 0 or type(created_at) not in (int, float):
            raise ValueError('Original process identity is incomplete')
        handle = kernel.OpenProcess(0x1000 | 0x100000, False, pid)
        if not handle:
            if ctypes.get_last_error() in (87, 1168):
                continue
            raise ValueError('Original process termination is unknown')
        try:
            status = kernel.WaitForSingleObject(handle, 0)
            if status == 0:
                continue
            if status != 0x102:
                raise ValueError('Original process termination is unknown')
            times = [wintypes.FILETIME() for _ in range(4)]
            if not kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
                raise ValueError('Original process creation clock is unreadable')
            current_created = ((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime) / 10_000_000 - 11644473600
            if abs(current_created - created_at) < .01:
                raise ValueError('Original gate or descendant is still running')
        finally:
            kernel.CloseHandle(handle)


def validate_core_resume(source, target, out, receipt, remote, bindings):
    """One reviewed timeout: rerun all core30, never reuse its partial shards."""
    root = source.resolve().parent
    if out.resolve().is_relative_to(root) or root.is_relative_to(out.resolve()):
        raise ValueError('resume output must not overlap original evidence')
    evidence = _resume_archive(source, CORE_RESUME_REVIEW)
    _resume_processes_finished(_read_json(Path(CORE_RESUME_REVIEW['lifecycle_record']) / 'run.json'))
    previous = _read_json(source)
    _same_day_clock(previous['started_at'], previous['completed_at'])
    if previous.get('pass') is not False or previous.get('calendar') is not None or previous.get('release_for_intraday') is not False or previous.get('resume'):
        raise ValueError('only the original failed maintenance gate may resume')
    if previous.get('launcher_sha256') != CORE_RESUME_REVIEW['old_launcher_sha256']:
        raise ValueError('unreviewed core launcher compatibility')
    schedule_change = CORE_RESUME_REVIEW['scheduling_only_worker_change']
    if (previous.get('group_worker_sha256') != schedule_change['before']
            or bindings.get('group_worker_sha256') != schedule_change['after']
            or any(previous.get(key) != value for key, value in bindings.items() if key != 'group_worker_sha256')):
        raise ValueError('resume helper/config/runtime binding changed')
    for key in ('automation_pin', 'strategy_pins', 'test_source_bindings'):
        if previous.get(key, {}) != receipt.get(key, {}):
            raise ValueError('resume prepared source identity changed: ' + key)
    if previous['prepared_receipt_sha256'] != sha(target / 'prepared.json') or remote != previous['observed_automation_main']:
        raise ValueError('resume prepared or current remote pin changed')
    groups = {row['label']: row for row in previous['microcap']}
    if len(groups) != len(previous['microcap']) or set(groups) != {'core30', 'release6', 'share_source2'}:
        raise ValueError('original microcap groups incomplete')
    failed = groups['core30']
    process = _read_json(root / 'core30/process.json')
    if (_read_json(root / 'core30/gate.json') != failed or failed.get('pass') is not False
            or any(failed.get(key) != value for key, value in process.items())
            or process.get('exit_code') != 124 or process.get('timed_out') is not True
            or process.get('termination_confirmed') is not True or process.get('hard_timeout_seconds') != 300):
        raise ValueError('not the exact reviewed terminated core30 timeout')
    _same_day_clock(process['start'], process['end'])
    collection = _read_json(root / 'core30/expected_collection.json')
    nodes = collection['selected']
    if (not nodes or collection['deselected'] != [] or collection['collected'] != nodes
            or len(set(nodes)) != len(nodes) or {n.split('::', 1)[0] for n in nodes} != {'tests/' + n for n in CORE}):
        raise ValueError('original full core collection is incomplete')
    for label, files in (('release6', RELEASE), ('share_source2', SHARE_SOURCE)):
        _resume_group(root, label, groups[label], ['tests/' + n for n in files])
    _resume_group(root, 'icim_supplemental5', previous['icim_supplemental5'], ICIM_SUPPLEMENTAL)
    manifest = _read_json(target / 'icim/docs/ic_im_daily_release_20261023_repairs_v3_r3_manifest.json')
    _resume_group(root, 'icim', previous['icim'], manifest['required_local_tests'],
                  collection_root='icim_results', allowed_deselections=manifest['daily_gate_deselections'])
    official = _read_json(root / 'icim_results/receipt.json')
    if official != previous['icim']['official_receipt'] or not official['source_before_equals_after'] or official['exit_code'] != 0:
        raise ValueError('original official ICIM receipt rejected')
    for name, digest in official['artifacts'].items():
        if sha(safe_path(root / 'icim_results', name)) != digest:
            raise ValueError('original ICIM artifact mismatch')
    if _read_json(root / 'icim_results/source_before.json') != _read_json(root / 'icim_results/source_after.json'):
        raise ValueError('original ICIM source changed during tests')
    supplemental = previous['icim_supplemental5']
    if supplemental['source_before'] != supplemental['source_after'] or supplemental['source_after'] != receipt['icim_supplemental_bindings']:
        raise ValueError('original supplemental dependencies differ')
    return previous, evidence


def validate_unchanged_icim_reuse(source, receipt, bindings):
    """Reuse only the exact completed same-day ICIM closure after Microcap edits."""
    review = ICIM_UNCHANGED_REUSE_REVIEW
    evidence = _resume_archive(source, review)
    _resume_processes_finished(_read_json(Path(review['lifecycle_record']) / 'run.json'))
    previous = _read_json(source)
    _same_day_clock(previous['started_at'], previous['completed_at'])
    if previous.get('pass') is not True or previous.get('calendar') is not None:
        raise ValueError('only complete maintenance acceptance may be reused')
    prepared = Path(review['prepared'])
    if sha(prepared) != review['prepared_sha256'] or previous['prepared_receipt_sha256'] != sha(prepared):
        raise ValueError('original prepared identity changed')
    old = _read_json(prepared)
    if old['strategy_pins']['icim'] != receipt['strategy_pins']['icim']:
        raise ValueError('ICIM runtime changed')
    for key in ('icim_supplemental_bindings','icim_supplemental_tests','icim_runner_published_sha256','icim_runner_adapted_sha256'):
        if old[key] != receipt[key]: raise ValueError('ICIM dependency changed: ' + key)
    if old['inventories']['icim'] != receipt['inventories']['icim']:
        raise ValueError('ICIM complete source/fixture inventory changed')
    if any(previous.get(key) != value for key,value in bindings.items()):
        raise ValueError('ICIM worker/environment binding changed')
    root=source.parent
    manifest=_read_json(prepared.parent/'icim/docs/ic_im_daily_release_20261023_repairs_v3_r3_manifest.json')
    _resume_group(root,'icim',previous['icim'],manifest['required_local_tests'],collection_root='icim_results',allowed_deselections=manifest['daily_gate_deselections'])
    _resume_group(root,'icim_supplemental5',previous['icim_supplemental5'],ICIM_SUPPLEMENTAL)
    official=_read_json(root/'icim_results/receipt.json')
    if official != previous['icim']['official_receipt'] or not official['source_before_equals_after']:
        raise ValueError('original ICIM official evidence differs')
    for name,digest in official['artifacts'].items():
        if sha(safe_path(root/'icim_results',name)) != digest: raise ValueError('ICIM artifact changed')
    if previous['icim_supplemental5']['source_before'] != previous['icim_supplemental5']['source_after'] or previous['icim_supplemental5']['source_after'] != receipt['icim_supplemental_bindings']:
        raise ValueError('supplemental source binding changed')
    return previous, evidence


def gate(target, out, live_identity=True, calendar=None, resume_from=None, reuse_icim_from=None):
    started = now()
    launcher_before = sha(Path(__file__))
    worker = HOME / 'scripts/daily_microcap_test_worker.py'
    worker_before = sha(worker)
    group_worker = HOME / 'scripts/daily_microcap_group_worker.py'
    group_before = sha(group_worker)
    pytest_config = HOME / 'scripts/daily_pytest.ini'
    pytest_config_before = sha(pytest_config)
    readiness = HOME / 'scripts/microcap_readiness.py'
    readiness_before = sha(readiness)
    resident = HOME / 'scripts/microcap_resident_readiness.py'
    resident_before = sha(resident)
    guard_source = HOME / 'scripts/daily_test_guard.py'
    guard_source_before = sha(guard_source)
    config_before = sha(CONFIG)
    receipt = validate_prepared(target)
    if sha(target / 'guard/sitecustomize.py') != guard_source_before:
        raise ValueError('prepared test guard differs from current reviewed guard source')
    verify_production_sources(receipt)
    remote = verify_remote(receipt) if live_identity else None
    import importlib.metadata
    runtime = {'python': sys.version, 'executable': sys.executable,
               'packages': {name: importlib.metadata.version(name) for name in ('pytest','hypothesis','pandas','numpy')}}
    reuse = None
    resume_evidence = None
    resume_label = 'share_source2'
    icim_reuse = None
    icim_reuse_evidence = None
    icim_bindings = {key: value for key,value in (
        ('worker_sha256',worker_before),('pytest_config_sha256',pytest_config_before),
        ('guard_source_sha256',guard_source_before),('config_sha256',config_before),('runtime',runtime))}
    if reuse_icim_from is not None:
        if resume_from is not None or not live_identity or calendar is not None:
            raise ValueError('unchanged ICIM reuse is maintenance-only and cannot combine with resume')
        icim_reuse,icim_reuse_evidence = validate_unchanged_icim_reuse(Path(reuse_icim_from),receipt,icim_bindings)
    if resume_from is not None:
        if not live_identity or calendar is not None:
            raise ValueError('resume is maintenance-gate only with live remote verification')
        if Path(resume_from).resolve() == Path(CORE_RESUME_REVIEW['gate']).resolve():
            resume_label = 'core30'
        validator = validate_core_resume if resume_label == 'core30' else validate_share_resume
        reuse, resume_evidence = validator(Path(resume_from), target, out, receipt, remote, {
            'worker_sha256': worker_before, 'group_worker_sha256': group_before,
            'pytest_config_sha256': pytest_config_before, 'microcap_readiness_sha256': readiness_before,
            'resident_worker_sha256': resident_before, 'guard_source_sha256': guard_source_before,
            'config_sha256': config_before, 'runtime': runtime})
    out.mkdir(parents=True, exist_ok=False)
    from concurrent.futures import ThreadPoolExecutor

    def micro_groups():
        results = []
        groups = [('core30', CORE), ('release6', RELEASE), ('share_source2', SHARE_SOURCE)]
        for group_index, (label, files) in enumerate(groups):
            if reuse is not None and label != resume_label:
                results.append(next(row for row in reuse['microcap'] if row['label'] == label))
                continue
            tmp = out / (label + '_temp'); tmp.mkdir()
            env = {**clean_test_env(), 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1',
                   'PYTHONPATH': str(target / 'guard') + os.pathsep + str(target / 'microcap'),
                   'MICROCAP_TEST_DATA_ROOT': str(target / 'microcap'),
                   'MICROCAP_DIGEST_SOURCE': str(target / 'consumer/scripts/build_microcap_realtime_digest.py'),
                   'MICROCAP_OFFLINE_AUDIT_LOG': str(out / (label + '_guard.log')),
                   'TOP100_REALTIME_REQUIRE_STATE': '1', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1',
                   'PYTEST_ADDOPTS': '', 'PYTHONPYCACHEPREFIX': str(tmp / 'bytecode'), 'DAILY_PRODUCTION_ROOTS': json.dumps([json.loads(CONFIG.read_text(encoding='utf8'))[k] for k in ('microcap_source','icim_source')]),
                   'HYPOTHESIS_STORAGE_DIRECTORY': str(tmp / 'hypothesis'), 'TEMP': str(tmp), 'TMP': str(tmp)}
            xml = out / label / 'regression.xml'
            selected_worker = group_worker if label == 'core30' else worker
            cmd = [sys.executable, '-B', '-X', 'utf8', str(selected_worker), str(out / label)] + ['tests/' + f for f in files]
            if label == 'share_source2':
                # Keep pytest's leaf directories under this fresh owned TEMP,
                # without its default pytest-of-user/pytest-N nesting on Windows.
                cmd += ['--basetemp', str(tmp / 'p')]
            result = subprocess_gate(cmd, target / 'microcap', env, out / label, label, timeout=300)
            result['pass'] = False
            try:
                if xml.exists():
                    result['counts'] = junit_counts(xml, files)
                result['pass'] = result['exit_code'] == 0 and result.get('counts', {}).get('tests', 0) > 0 and all(result['counts'][x] == 0 for x in ('failures', 'errors', 'skipped'))
                if result['pass']:
                    validate_collection(out / label / 'collection.json', result['counts'])
                result['guard_started'], result['guard_blocked'] = guard_evidence(Path(env['MICROCAP_OFFLINE_AUDIT_LOG']))
                result['pass'] = result['pass'] and result['guard_started'] and not result['guard_blocked']
            except Exception as exc:
                result.update(pass_=False, validation_error=str(exc))
                result['pass'] = False
            write(out / label / 'gate.json', result); results.append(result)
            if UNCONFIRMED_CHILDREN:
                for pending_label, _ in groups[group_index + 1:]:
                    results.append({'label': pending_label, 'pass': False, 'status': 'not_started_due_to_unconfirmed_child'})
                break
        return results

    def icim_group():
        env = {**clean_test_env(), 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1',
               'ICIM_REQUIRE_MIGRATION': '0', 'ICIM_STATE_DIR': str(out / 'isolated_state'), 'PYTHONPATH': str(target / 'icim'),
               'HYPOTHESIS_STORAGE_DIRECTORY': str(out / 'icim_hypothesis'),
               'PYTHONPYCACHEPREFIX': str(out / 'icim_bytecode')}
        result = subprocess_gate([sys.executable, '-B', '-X', 'utf8', str(target / 'icim/run_ic_im_v1_4_delivery_tests.py'), '--out-dir', str(out / 'icim_results')], target / 'icim', env, out / 'icim', 'icim47', timeout=195)
        evidence = out / 'icim_results/receipt.json'
        result['pass'] = False
        if result['exit_code'] == 0 and evidence.exists():
            result['official_receipt'] = json.loads(evidence.read_text(encoding='utf8'))
            result['pass'] = result['official_receipt']['source_before_equals_after'] and result['official_receipt']['exit_code'] == 0 and all(result['official_receipt']['counts'][k] == 0 for k in ('failures','errors','skipped'))
        write(out / 'icim/gate.json', result)
        return result

    # These CPU/IO-heavy suites share one 12-thread, 16GB Windows host.
    # Serial families avoid resource contention without changing either deadline.
    try:
        icim = icim_reuse['icim'] if icim_reuse is not None else (reuse['icim'] if reuse is not None else icim_group())
    except Exception as exc:
        icim = {'pass': False, 'error': str(exc)}
    if UNCONFIRMED_CHILDREN:
        supplemental = {'pass': False, 'status': 'not_started_due_to_unconfirmed_child'}
    else:
        try:
            supplemental = icim_reuse['icim_supplemental5'] if icim_reuse is not None else (reuse['icim_supplemental5'] if reuse is not None else icim_supplemental_group(target, out, worker))
        except Exception as exc:
            supplemental = {'pass': False, 'error': str(exc)}
    if UNCONFIRMED_CHILDREN:
        micro = [{'pass': False, 'status': 'not_started_due_to_unconfirmed_child'}]
    else:
        try:
            micro = micro_groups()
        except Exception as exc:
            micro = [{'pass': False, 'error': str(exc)}]
    # No success certificate survives mutation of source, helper, consumer or fixture.
    validate_prepared(target)
    if resident_before != sha(resident):
        raise ValueError('resident worker changed during tests')
    if launcher_before != sha(Path(__file__)) or worker_before != sha(worker) or group_before != sha(group_worker) or pytest_config_before != sha(pytest_config) or readiness_before != sha(readiness) or guard_source_before != sha(guard_source) or config_before != sha(CONFIG):
        raise ValueError('launcher/worker/readiness/config/guard changed during tests')
    import importlib.metadata
    if reuse is not None:
        # Recheck original evidence and live identities after the failed group;
        # a changed remote/source/config cannot inherit successful stages.
        verify_production_sources(receipt)
        if verify_remote(receipt) != remote:
            raise ValueError('remote changed during resume')
        _same_day_clock(reuse['started_at'], reuse['completed_at'], started)
        if resume_label == 'core30':
            _resume_archive(Path(resume_from), CORE_RESUME_REVIEW)
            _resume_processes_finished(_read_json(Path(CORE_RESUME_REVIEW['lifecycle_record']) / 'run.json'))
            original = _read_json(Path(resume_from).parent / 'core30/expected_collection.json')
            core_result = next(row for row in micro if row.get('label') == 'core30')
            if core_result.get('pass') is True:
                current = _read_json(out / 'core30/collection.json')
                if (any(current.get(key) != original[key] for key in ('collected', 'selected', 'deselected'))
                        or current.get('executed') != original['selected']
                        or core_result.get('counts', {}).get('tests') != len(original['selected'])):
                    raise ValueError('resumed entire core30 node identities or execution changed')
            else:
                core_result['resume_failure_reason'] = 'Full core30 failed; missing or partial collection cannot authorize success'
                core_result['resume_collection_status'] = ('not_accepted_after_failed_core'
                    if (out / 'core30/collection.json').exists() else 'not_generated_after_failed_core')
            current_runtime = {'python': sys.version, 'executable': sys.executable,
                               'packages': {name: importlib.metadata.version(name) for name in ('pytest','hypothesis','pandas','numpy')}}
            if current_runtime != reuse['runtime']:
                raise ValueError('resume runtime changed during core30')
        else:
            _resume_archive(Path(resume_from))
            if next((row for row in micro if row.get('label') == 'share_source2'), {}).get('counts', {}).get('tests') != 128:
                raise ValueError('resumed share-source full collection changed')
            if _read_json(out / 'share_source2/collection.json')['selected'] != _read_json(Path(resume_from).parent / 'share_source2/collection.json')['selected']:
                raise ValueError('resumed share-source test node identities changed')
    result = {'schema': 1, 'started_at': reuse['started_at'] if reuse is not None else started, 'completed_at': now(), 'prepared_receipt_sha256': sha(target / 'prepared.json'),
              'runtime': {'python': sys.version, 'executable': sys.executable,
                          'packages': {name: importlib.metadata.version(name) for name in ('pytest','hypothesis','pandas','numpy')}},
              'launcher_sha256': sha(Path(__file__)),
              'worker_sha256': worker_before,
              'group_worker_sha256': group_before,
              'pytest_config_sha256': pytest_config_before,
              'microcap_readiness_sha256': readiness_before,
              'resident_worker_sha256': resident_before,
              'guard_source_sha256': guard_source_before, 'config_sha256': config_before,
              'automation_pin': receipt['automation_pin'], 'strategy_pins': receipt['strategy_pins'],
              'observed_automation_main': remote,
              'test_source_bindings': receipt.get('test_source_bindings', {}),
              'microcap': micro, 'icim': icim, 'icim_supplemental5': supplemental,
              'pass': all(x['pass'] for x in micro) and icim['pass'] and supplemental['pass'],
              'production_authorized_by_this_receipt': False,
              'calendar': calendar, 'release_for_intraday': False,
              'note': 'Daily gate only. Production also requires current calendar, state, locks, dates and publication checks.'}
    if reuse is not None:
        result['resumed_at'] = started
        result['resume'] = {**resume_evidence, 'reason': ('reviewed_core30_timeout_entire_group_replay' if resume_label == 'core30' else 'reviewed_windows_pytest_basetemp_only'),
                            'reused_groups': ['icim47','icim_supplemental5','release6','share_source2'] if resume_label == 'core30' else ['icim47','icim_supplemental5','core30','release6'],
                            'rerun_groups': [resume_label], 'original_completed_at': reuse['completed_at']}
    if icim_reuse is not None:
        validate_unchanged_icim_reuse(Path(reuse_icim_from),receipt,icim_bindings)
        result['unchanged_icim_reuse'] = {**icim_reuse_evidence, 'groups':['icim47','icim_supplemental5'], 'original_started_at':icim_reuse['started_at'], 'original_completed_at':icim_reuse['completed_at']}
    write(out / 'gate.json', result)
    return result


def main():
    if sys.flags.optimize != 0:
        raise RuntimeError('optimized Python is not an acceptance environment')
    p = argparse.ArgumentParser()
    p.add_argument('mode', choices=['prepare', 'resume-prepare', 'validate', 'gate', 'calendar', 'activate', 'daily'])
    p.add_argument('--prepared', type=Path)
    p.add_argument('--out', type=Path)
    p.add_argument('--resume-from', type=Path)
    p.add_argument('--reuse-icim-from', type=Path)
    args = p.parse_args()
    if (args.resume_from is not None or args.reuse_icim_from is not None) and args.mode != 'gate':
        p.error('--resume-from is only permitted for maintenance gate')
    config = json.loads(CONFIG.read_text(encoding='utf8'))
    if args.mode == 'calendar':
        result = independent_calendar(Path(config['icim_source']))
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.mode == 'daily':
        calendar = independent_calendar(Path(config['icim_source']))
        print(json.dumps(calendar, ensure_ascii=False), flush=True)
        if not calendar['is_trading_day']:
            print('Exchange closed; no tests, state or signals run.')
            return 0
        if datetime.fromisoformat(calendar['checked_at']).hour >= 15:
            raise ValueError('intraday window closed; daily gate will not start a signal workflow')
        active = json.loads(ACTIVE.read_text(encoding='utf8'))
        args.prepared = Path(active['prepared'])
        if sha(args.prepared / 'prepared.json') != active['prepared_receipt_sha256']:
            raise ValueError('active prepared certificate changed')
        for key, path in [('launcher_sha256', Path(__file__)), ('worker_sha256', HOME / 'scripts/daily_microcap_test_worker.py'),
                          ('group_worker_sha256', HOME / 'scripts/daily_microcap_group_worker.py'),
                          ('pytest_config_sha256', HOME / 'scripts/daily_pytest.ini'),
                          ('microcap_readiness_sha256', HOME / 'scripts/microcap_readiness.py'),
                          ('resident_worker_sha256', HOME / 'scripts/microcap_resident_readiness.py'),
                          ('guard_source_sha256', HOME / 'scripts/daily_test_guard.py'), ('config_sha256', CONFIG)]:
            if active.get(key) != sha(path):
                raise ValueError('active launcher dependency changed: ' + key)
    if args.prepared is None:
        p.error('--prepared required')
    check_isolation(args.prepared, args.out, config)
    with coordinator_lock():
        if args.mode in ('prepare', 'resume-prepare'):
            result = prepare(config, args.prepared.resolve(), resume=args.mode == 'resume-prepare')
        elif args.mode == 'validate':
            result = validate_prepared(args.prepared.resolve())
        elif args.mode == 'activate':
            if args.out is None:
                p.error('--out must identify the completed gate audit')
            result = validate_prepared(args.prepared.resolve())
            acceptance = json.loads((args.out / 'gate.json').read_text(encoding='utf8'))
            if not acceptance.get('pass') or acceptance['prepared_receipt_sha256'] != sha(args.prepared / 'prepared.json') or acceptance['launcher_sha256'] != sha(Path(__file__)) or acceptance['worker_sha256'] != sha(HOME / 'scripts/daily_microcap_test_worker.py') or acceptance['group_worker_sha256'] != sha(HOME / 'scripts/daily_microcap_group_worker.py') or acceptance['pytest_config_sha256'] != sha(HOME / 'scripts/daily_pytest.ini') or acceptance.get('microcap_readiness_sha256') != sha(HOME / 'scripts/microcap_readiness.py') or acceptance.get('guard_source_sha256') != sha(HOME / 'scripts/daily_test_guard.py') or acceptance.get('config_sha256') != sha(CONFIG) or acceptance.get('resident_worker_sha256') != sha(HOME / 'scripts/microcap_resident_readiness.py'):
                raise ValueError('activation requires unchanged launcher and complete successful gate')
            write(ACTIVE, {'prepared': str(args.prepared.resolve()), 'prepared_receipt_sha256': sha(args.prepared / 'prepared.json'),
                           'launcher_sha256': sha(Path(__file__)), 'worker_sha256': sha(HOME / 'scripts/daily_microcap_test_worker.py'),
                           'group_worker_sha256': sha(HOME / 'scripts/daily_microcap_group_worker.py'),
                           'pytest_config_sha256': sha(HOME / 'scripts/daily_pytest.ini'),
                           'microcap_readiness_sha256': sha(HOME / 'scripts/microcap_readiness.py'),
                           'resident_worker_sha256': sha(HOME / 'scripts/microcap_resident_readiness.py'),
                           'guard_source_sha256': sha(HOME / 'scripts/daily_test_guard.py'), 'config_sha256': sha(CONFIG),
                           'activated_at': now(), 'acceptance': str(args.out.resolve())})
        else:
            if args.out is None:
                p.error('--out required for gate')
            result = gate(args.prepared.resolve(), args.out.resolve(),
                          calendar=calendar if args.mode == 'daily' else None, resume_from=args.resume_from, reuse_icim_from=args.reuse_icim_from)
            if args.mode == 'daily':
                current = datetime.now(ZoneInfo('Asia/Shanghai'))
                result['release_for_intraday'] = (result['pass'] and calendar['is_trading_day']
                    and current.date().isoformat() == calendar['today'] and current.hour < 15)
                write(args.out / 'gate.json', result)
                if not result['release_for_intraday']:
                    print(json.dumps({'software_gate_pass': result['pass'], 'release_for_intraday': False, 'signals_generated': 0}))
                    return 1
    print(json.dumps({k: result[k] for k in ('schema', 'pass', 'prepared_at', 'completed_at', 'strategy_pins') if k in result}, ensure_ascii=False))
    return 0 if result.get('pass', True) else 1


if __name__ == '__main__':
    try:
        status = main()
    except Exception as exc:
        failure = {'schema': 1, 'pass': False, 'status': 'BLOCKED', 'failed_at': now(),
                   'error': type(exc).__name__ + ': ' + str(exc), 'unconfirmed_owned_children': UNCONFIRMED_CHILDREN}
        print(json.dumps(failure, ensure_ascii=False), flush=True)
        # Preserve a truthful failure receipt even before a test process starts.
        try:
            options = {name: [sys.argv[i + 1] for i, value in enumerate(sys.argv[:-1]) if value == name]
                       for name in ('--out', '--prepared')}
            if options['--out']:
                target = Path(options['--out'][-1]).resolve()
                prepared = Path(options['--prepared'][-1]) if options['--prepared'] else HOME / 'unavailable-prepared'
                check_isolation(prepared, target, json.loads(CONFIG.read_text(encoding='utf8')))
                target.mkdir(parents=True, exist_ok=True)
                write(target / ('failure_' + str(os.getpid()) + '.json'), failure)
        except Exception as diagnostic_error:
            print('Failure receipt could not be safely written: ' + str(diagnostic_error), file=sys.stderr)
        status = 1
    raise SystemExit(status)
