"""Reuse only an unchanged successful scoped stage and its original objects.

Receipts are private recovery inputs. They never authorize publication or replace
the independent native delivery check. Failed/unknown commands are never cached.
"""
from pathlib import Path
import json
from prepare_microcap_local import atomic, encode, sha


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
        raw = encode(context)
        if self.context_path.exists():
            if self.context_path.read_bytes() != raw:
                raise ValueError('Scoped recovery transaction changed; preserve it and use a new target directory')
        else:
            atomic(self.context_path, raw)

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
                    or receipt.get('context_sha256') != sha(self.context_path.read_bytes())
                    or receipt.get('command') != command or receipt.get('exit_code') != 0):
                raise ValueError('Successful scoped stage has another command or source context')
            for item in receipt['objects']:
                path = self._path(item)
                if not path.is_file() or path.is_symlink() or sha(path.read_bytes()) != item['sha256']:
                    raise ValueError('Successful scoped stage output changed; restore its original objects: ' + str(path))
            result = {}
            for key, item in receipt['command_evidence'].items():
                result[key] = self._path(item)
            result['stage_reused'] = True
            return result
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
                       context_sha256=sha(self.context_path.read_bytes()), command=command,
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
