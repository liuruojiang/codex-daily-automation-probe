"""Reuse only an unchanged successful scoped stage and its original objects.

Receipts are private recovery inputs. They never authorize publication or replace
the independent native delivery check. Failed/unknown commands are never cached.
"""
from pathlib import Path
import json
import re
from prepare_microcap_local import atomic, encode, sha


# The Nov 13 patch changes only the recovery-proof reader and this receipt
# reader. Earned Native outputs keep their original producer context.
LEGACY_CONTROLLER_SOURCES = {
    'run_microcap_scoped_rebalance.py': '4c73d0866355b38fb5ed5ef03cad1cab0a9be70f1b315f2100e1b14e1bddcc8e',
    'microcap_scoped_stage_resume.py': 'beaa60ed7865231132ab2f4501b965b7ec12c30365ca8aaa157474ee14bba24d',
    'microcap_recovery_handoff.py': '6056bbc4a25bacefb74d554c3aec02eaae64ab7f824cb7768c9a08936ae8f958',
    'microcap_handoff_witness.py': '91d57913553e45ad2ac9e791dbb419f4c759df368e9f9a7c20e53decb12c80c1',
}
REVIEWED_PROOF_READER = '8558a05482f6054a06e5832933fd53287a80554fe42bc60ae4101fef9015ceb7'
REVIEWED_NATIVE = '906eca8a5dfede7fa1674fb17cd3c6ce12205e6d'


def _save_original(path, raw):
    if path.is_symlink() or (path.exists() and (not path.is_file() or path.read_bytes() != raw)):
        raise ValueError('Preserve the original scoped reader admission')
    if not path.exists():
        atomic(path, raw)


def reviewed_reader_context(original_raw, current_raw):
    original, current = json.loads(original_raw), json.loads(current_raw)
    expected = dict(LEGACY_CONTROLLER_SOURCES)
    expected['microcap_recovery_handoff.py'] = REVIEWED_PROOF_READER
    expected['microcap_scoped_stage_resume.py'] = sha(Path(__file__).read_bytes().replace(b'\r\n', b'\n'))
    unchanged = lambda value: {k: v for k, v in value.items() if k != 'controller_sources'}
    if (original.get('schema') != 'microcap_scoped_recovery_transaction_v1'
            or original.get('consumer_sha') != REVIEWED_NATIVE
            or original.get('controller_sources') != LEGACY_CONTROLLER_SOURCES
            or current.get('controller_sources') != expected
            or unchanged(original) != unchanged(current)):
        raise ValueError('Scoped recovery transaction changed; preserve it and use a new target directory')
    # Verify the exact installed reader closure as well as the supplied context.
    for name, digest in expected.items():
        if sha((Path(__file__).parent / name).read_bytes().replace(b'\r\n', b'\n')) != digest:
            raise ValueError('Scoped recovery reader source differs from its reviewed relationship')
    current_sha, original_sha = sha(current_raw), sha(original_raw)
    return dict(schema='microcap_scoped_proof_reader_admission_v1',
        original_context_sha256=original_sha, reader_context_sha256=current_sha,
        original_controller_sources=LEGACY_CONTROLLER_SOURCES, reader_controller_sources=expected,
        original_context_and_receipts_rewritten=False, native_outputs_recomputed=False)


def receipt_context(original_raw, receipt_sha, read_closed):
    """Select only the exact closed context that earned a successful receipt."""
    if receipt_sha == sha(original_raw):
        return original_raw
    if not isinstance(receipt_sha, str) or re.fullmatch('[0-9a-f]{64}', receipt_sha) is None:
        raise ValueError('Scoped receipt context is not a closed source identity')
    current_raw = read_closed('context-reader-' + receipt_sha + '.json')
    if sha(current_raw) != receipt_sha:
        raise ValueError('Scoped receipt reader context hash differs')
    admission = reviewed_reader_context(original_raw, current_raw)
    if json.loads(read_closed('reader-admission-' + receipt_sha + '.json')) != admission:
        raise ValueError('Scoped receipt lacks its exact original reader admission')
    return current_raw


def _admit_proof_reader(directory, original_raw, current_raw):
    admission = reviewed_reader_context(original_raw, current_raw)
    current_sha = admission['reader_context_sha256']
    path = directory / ('context-reader-' + current_sha + '.json')
    _save_original(path, current_raw)
    _save_original(directory / ('reader-admission-' + current_sha + '.json'), encode(admission))
    return path


class StageResume:
    def __init__(self, out, consumer, context, run, input_roles=None):
        self.out, self.consumer = Path(out).resolve(), Path(consumer).resolve()
        self.context, self.run = context, run
        self.input_roles={name:str(Path(path).resolve()) for name,path in (input_roles or {}).items()}
        if any(name not in context.get('input_object_bindings',{}) for name in self.input_roles):
            raise ValueError('Relocatable stage input lacks its exact source-object binding')
        self.directory = self.out / 'stage-receipts'
        self.directory.mkdir(parents=True, exist_ok=True)
        self.context_path = self.directory / 'context.json'
        self.write_context_path = self.context_path
        raw = encode(context)
        if self.context_path.exists():
            if self.context_path.read_bytes() != raw:
                self.write_context_path = _admit_proof_reader(self.directory, self.context_path.read_bytes(), raw)
        else:
            atomic(self.context_path, raw)
        self.context_objects = {path: path.read_bytes() for path in (self.context_path, self.write_context_path)}

    def _context_shas(self):
        for path, original in self.context_objects.items():
            if path.is_symlink() or not path.is_file() or path.read_bytes() != original:
                raise ValueError('Scoped recovery context changed after reader admission')
        return {sha(raw) for raw in self.context_objects.values()}

    def _location(self, path):
        path = Path(path).resolve()
        for role, root in (('out', self.out), ('consumer', self.consumer)):
            if path.is_relative_to(root):
                return dict(role=role, path=path.relative_to(root).as_posix())
        raise ValueError('Scoped stage evidence escapes its private roots')

    def _path(self, item):
        root = {'out': self.out, 'consumer': self.consumer}[item['role']]
        path = (root / item['path']).resolve()
        if not path.is_relative_to(root):
            raise ValueError('Scoped stage object escapes its role')
        return path

    def _objects(self, paths):
        result = []
        for path in sorted(set(map(lambda p: Path(p).resolve(), paths)), key=str):
            if not path.is_file() or path.is_symlink():
                raise ValueError('Successful stage object missing or linked: ' + str(path))
            result.append(dict(**self._location(path), sha256=sha(path.read_bytes())))
        if not result:
            raise ValueError('A successful stage needs an actual output closure')
        return result

    def _argv(self, argv):
        # Only declared private-root relocation is ignored; flags and values are
        # exact. Original command/process records retain the real paths.
        result = []
        roots = sorted(((str(self.out), '{out}'), (str(self.consumer), '{consumer}')), key=lambda x: -len(x[0]))
        for value in argv:
            value = str(value)
            role=next((name for name,path in self.input_roles.items() if value==path),None)
            if role is not None:
                result.append('{input:'+role+'}')
                continue
            for root, token in roots:
                if value == root or value.startswith(root + '/') or value.startswith(root + '\\'):
                    value = token + value[len(root):]
                    break
            result.append(value.replace('\\', '/'))
        return result

    def call(self, label, argv, output_paths):
        receipt_path = self.directory / (label + '.json')
        command = self._argv(argv)
        if receipt_path.exists():
            receipt = json.loads(receipt_path.read_bytes())
            if (receipt.get('schema') != 'microcap_scoped_successful_stage_v1'
                    or receipt.get('context_sha256') not in self._context_shas()
                    or receipt.get('command') != command or receipt.get('exit_code') != 0):
                raise ValueError('Successful scoped stage has another command or source context')
            for item in receipt['objects']:
                path = self._path(item)
                if not path.is_file() or path.is_symlink() or sha(path.read_bytes()) != item['sha256']:
                    raise ValueError('Successful scoped stage output changed; restore its original objects: ' + str(path))
            if self.write_context_path != self.context_path and label in ('handoff', 'verify-handoff'):
                checkpoints = [self._path(item) for item in receipt['objects']
                               if item.get('role') == 'out' and item.get('path') == 'handoff/checkpoint.json']
                if len(checkpoints) != 1:
                    raise ValueError('Reused recovery handoff lacks its original checkpoint closure')
                from member_checkpoint_policy import validate_checkpoint_directory
                validate_checkpoint_directory(checkpoints[0], None)
            result = {}
            for key, item in receipt['command_evidence'].items():
                result[key] = self._path(item)
            result['stage_reused'] = True
            return result
        self._context_shas()
        result = self.run(label, argv)
        if not isinstance(result, dict) or not all(key in result for key in ('process', 'stdout', 'stderr')):
            raise ValueError('Scoped recovery commands must retain original process/log receipts')
        process_path = Path(result['process'])
        process = json.loads(process_path.read_bytes())
        if process.get('exit_code') != 0 or process.get('completion_unconfirmed'):
            raise ValueError('Unknown or unsuccessful stage cannot earn a reuse receipt')
        outputs = output_paths() if callable(output_paths) else output_paths
        logs = [Path(result[key]) for key in ('process', 'stdout', 'stderr')]
        receipt = dict(schema='microcap_scoped_successful_stage_v1', exit_code=0,
                       context_sha256=sha(self.write_context_path.read_bytes()), command=command,
                       objects=self._objects([*outputs, *logs]),
                       command_evidence={key: self._location(result[key]) for key in ('process', 'stdout', 'stderr')})
        atomic(receipt_path, encode(receipt))
        return result


def formal_objects(consumer, target):
    root = Path(consumer).resolve()
    manifest = root / 'outputs/top100_delivery_manifest.json'
    value = json.loads(manifest.read_bytes())
    if (value.get('ok') is not True or value.get('status') != 'complete'
            or value.get('scope') != 'whole_workspace_delivery' or value.get('expected_date') != target):
        raise ValueError('Scoped stage has not earned a complete native target manifest')
    files = {**value['inputs'], **value['artifacts']}
    paths = [manifest]
    for name, expected in files.items():
        path = (root / name).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError('Native successful-stage closure changed: ' + name)
        raw=path.read_bytes()
        # Native manifests normalize text line endings; original issuer/ST
        # evidence remains byte-bound. Reuse receipts below still bind raw bytes.
        actual=sha(raw) if name.startswith('data/st_notice_evidence/') else sha(raw.replace(b'\r\n',b'\n'))
        if actual!=expected:
            raise ValueError('Native successful-stage closure changed: ' + name)
        paths.append(path)
    return paths


def checkpoint_objects(path):
    path = Path(path).resolve()
    value = json.loads(path.read_bytes())
    paths = [path]
    for key in ('period', 'history', 'turnover', 'version_state', 'prices'):
        spec = value[key]
        item = (path.parent / spec['path']).resolve()
        if not item.is_relative_to(path.parent) or sha(item.read_bytes()) != spec['sha256']:
            raise ValueError('Native checkpoint stage closure changed: ' + key)
        paths.append(item)
    return paths
