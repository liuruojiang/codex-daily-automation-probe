"""Activate one exact reviewed scoped runtime without changing restored data.

Every changed Git path, including test/document evidence, must be reviewed.
This switches source only; it grants no historical rewrite or formal acceptance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import zipfile

DATA_PREFIXES = ("outputs/", ".microcap_index_cache/")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def checked_path(root, name):
    if (not isinstance(name, str) or not name or any(c in name for c in "\\:\r\n\0")
            or PurePosixPath(name).is_absolute() or PurePosixPath(name).as_posix() != name
            or any(p.casefold() in ("", ".", "..", ".git") or p.endswith((".", " "))
                   for p in name.split("/"))):
        raise ValueError("Invalid reviewed source path")
    path = root.joinpath(*PurePosixPath(name).parts)
    for candidate in (path, *path.parents):
        if candidate == root:
            break
        if candidate.is_symlink() or getattr(candidate, "is_junction", lambda: False)():
            raise ValueError("Linked source/data path is not supported")
    if not path.resolve().is_relative_to(root):
        raise ValueError("Reviewed source path escapes checkout")
    if os.name == "nt" and len(str(path)) >= 248 and not str(path).startswith("\\\\?\\"):
        return Path("\\\\?\\" + str(path))
    return path


def activate(root: Path, config_path: Path, restore_receipt: Path, receipt: Path, *, bundle: Path | None = None) -> dict:
    root, config_path, restore_receipt, receipt = [Path(p).resolve() for p in
                                                 (root, config_path, restore_receipt, receipt)]
    config_raw, restored_raw = config_path.read_bytes(), restore_receipt.read_bytes()
    config, restored = json.loads(config_raw), json.loads(restored_raw)
    if (not isinstance(config, dict)
            or set(config) != {"schema_version", "baseline_sha", "runtime_sha", "changes"}
            or type(config["schema_version"]) is not int or config["schema_version"] != 1):
        raise ValueError("Invalid scoped runtime review schema")
    old, new = config["baseline_sha"], config["runtime_sha"]
    if old == new or any(not isinstance(v, str) or re.fullmatch(r"[0-9a-f]{40}", v) is None for v in (old, new)):
        raise ValueError("Scoped review requires distinct exact commits")
    changes = config["changes"]
    if not isinstance(changes, dict) or not changes or len({n.casefold() for n in changes}) != len(changes):
        raise ValueError("Scoped review requires all changed paths")
    for name, pair in changes.items():
        checked_path(root, name)
        if name.casefold().startswith(DATA_PREFIXES) or name.casefold() in ("outputs", ".microcap_index_cache"):
            raise ValueError("Scoped source activation cannot change restored data")
        if (not isinstance(pair, list) or len(pair) != 2 or pair[0] == pair[1]
                or any(not isinstance(v, str) or re.fullmatch(r"[0-9a-f]{64}", v) is None
                       for v in ([pair[1]] if pair[0] is None else pair))):
            raise ValueError("Scoped review requires exact old/new source hashes")
    if not isinstance(restored, dict) or restored.get("restored") is not True:
        raise ValueError("Scoped activation requires the actual baseline restore receipt")
    if restored.get('status') == 'scoped_whole_restore_verified':
        manifest_path=root/'outputs/top100_delivery_manifest.json'
        raw=manifest_path.read_bytes();manifest=json.loads(raw)
        if (restored.get('runtime_sha')!=old
                or restored.get('historical_release_files_equivalent') is not True
                or restored.get('repository')!='liuruojiang/codex-daily-automation-probe'
                or restored.get('native_restore',{}).get('ok') is not True
                or restored.get('seed_date')!=manifest.get('expected_date')
                or restored.get('native_restore',{}).get('expected_date')!=manifest.get('expected_date')
                or restored.get('source_manifest_release_sha')!=manifest.get('release_sha')
                or restored.get('restored_manifest_release_sha')!=manifest.get('release_sha')
                or restored.get('source_manifest_sha256')!=digest(raw)
                or manifest.get('ok') is not True or manifest.get('status')!='complete'):
            raise ValueError('Scoped whole restoration identity differs from actual archive')
        if bundle is None:
            raise ValueError('Scoped whole activation requires its original verified bundle')
        bundle=Path(bundle).resolve()
        with bundle.open('rb') as stream:
            actual_bundle=hashlib.file_digest(stream,'sha256').hexdigest()
        if actual_bundle!=restored.get('bundle_sha256') or bundle.stat().st_size!=restored.get('bundle_bytes'):
            raise ValueError('Scoped whole activation bundle identity differs')
        with zipfile.ZipFile(bundle) as archive:
            if archive.namelist().count('outputs/top100_delivery_manifest.json')!=1 or archive.read('outputs/top100_delivery_manifest.json')!=raw:
                raise ValueError('Original whole manifest differs from restored archive')
    elif restored.get("strategy_sha") != old:
        raise ValueError("Scoped activation requires the actual baseline restore receipt")
    if receipt in (restore_receipt, config_path) or receipt.exists():
        raise ValueError("Scoped activation receipt must be new and preserve original evidence")
    if receipt.is_relative_to(root) and receipt.relative_to(root).as_posix() in changes:
        raise ValueError("Activation receipt cannot overwrite reviewed code")

    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), *args])

    lock = Path(git("rev-parse", "--git-path", "codex-scoped-activation.lock").decode().strip())
    if not lock.is_absolute():
        lock = root / lock
    try:
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError as exc:
        raise ValueError("Another scoped source activation is active") from exc
    os.close(fd)
    try:
        def data_state(expected_head=old):
            if git("rev-parse", "HEAD").decode().strip() != expected_head:
                raise ValueError("Scoped baseline checkout differs")
            # -z handles spaces/non-ASCII without Git's quoting convention.
            records = git("status", "--porcelain=v1", "-z", "--untracked-files=all").split(b"\0")
            files = {}
            for record in records:
                if not record:
                    continue
                status, name = record[:2].decode(), record[3:].decode("utf-8")
                if status == "??" and checked_path(root, name) in (config_path, restore_receipt):
                    continue  # These exact immutable input bytes are bound separately.
                if "R" in status or "C" in status or not name.startswith(DATA_PREFIXES):
                    raise ValueError("Scoped source checkout has local edits")
                path = checked_path(root, name)
                if path.exists() and not path.is_file():
                    raise ValueError("Unsupported restored data object")
                files[name] = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
            return files

        before_data = data_state()
        subprocess.run(["git", "-C", str(root), "fetch", "--no-tags", "origin", new], check=True)
        changed = {p.decode("utf-8") for p in git("diff", "--no-renames", "--name-only", "-z", old, new).split(b"\0") if p}
        if changed != set(changes):
            raise ValueError("Scoped runtime differs from the complete reviewed path set")

        def blob(revision, name):
            entry = git("ls-tree", "-z", revision, "--", name)
            if not entry:
                return None
            items = entry.rstrip(b"\0").split(b"\0")
            if len(items) != 1:
                raise ValueError("Reviewed path is not a single Git blob")
            metadata, actual = items[0].split(b"\t", 1)
            mode, kind, oid = metadata.split()
            if actual.decode("utf-8") != name or mode not in (b"100644", b"100755") or kind != b"blob":
                raise ValueError("Reviewed runtime only permits regular source blobs")
            return git("cat-file", "blob", oid.decode()), oid.decode()

        objects = {}
        for name, pair in changes.items():
            for revision, expected in zip((old, new), pair):
                value = blob(revision, name)
                raw = value[0] if value is not None else None
                if (raw is None) != (expected is None) or (raw is not None and digest(raw) != expected):
                    raise ValueError("Scoped Git source hash differs: " + name)
                objects[revision, name] = value[1] if value is not None else None
            path = checked_path(root, name)
            def working_blob():
                # Git's own clean filter handles the checkout's declared text
                # EOL convention, while binary evidence retains byte identity.
                return subprocess.check_output(["git", "-C", str(root), "hash-object", "--path=" + name,
                                                "--stdin"], input=path.read_bytes()).decode().strip()
            if (pair[0] is None and path.exists()) or (pair[0] is not None and working_blob() != objects[old, name]):
                raise ValueError("Scoped baseline source readback differs: " + name)
        if (config_path.read_bytes() != config_raw or restore_receipt.read_bytes() != restored_raw
                or data_state() != before_data):
            raise ValueError("Scoped review, restore evidence or state changed concurrently")
        # No commit-tree data differences are permitted; Git only changes the
        # exact reviewed source blobs and leaves all restored cache/output paths.
        subprocess.run(["git", "-C", str(root), "checkout", "--detach", new], check=True)
        for name, pair in changes.items():
            raw = checked_path(root, name).read_bytes()
            observed = subprocess.check_output(["git", "-C", str(root), "hash-object", "--path=" + name,
                                                "--stdin"], input=raw).decode().strip()
            if observed != objects[new, name]:
                raise ValueError("Scoped runtime source readback differs: " + name)
        if data_state(new) != before_data:
            raise ValueError("Restored data or source status changed during source activation")
        for name, expected in before_data.items():
            path = checked_path(root, name)
            actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
            if actual != expected:
                raise ValueError("Restored data changed during source activation: " + name)
        if config_path.read_bytes() != config_raw or restore_receipt.read_bytes() != restored_raw:
            raise ValueError("Original activation evidence changed during checkout")
        result = dict(schema="reviewed_scoped_runtime_v1", baseline_sha=old, runtime_sha=new,
                      changes=changes, review_sha256=hashlib.sha256(config_raw).hexdigest(),
                      restore_receipt_sha256=hashlib.sha256(restored_raw).hexdigest(),
                      preserved_changed_data_files=before_data, activated=True,
                      current_data_verified=False, formal_release_verified=False,
                      historical_rewrite_authorized=False)
        receipt.parent.mkdir(parents=True, exist_ok=True)
        with receipt.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(result, indent=2) + "\n")
        return result
    finally:
        lock.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("root", "config", "restore-receipt", "receipt"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument('--bundle',type=Path)
    args = parser.parse_args()
    print(json.dumps(activate(args.root, args.config, args.restore_receipt, args.receipt,bundle=args.bundle)))


if __name__ == "__main__":
    main()
