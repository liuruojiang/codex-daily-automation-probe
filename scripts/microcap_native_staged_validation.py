"""Run native foreign-root validation with a fresh, correctly bound import.

The native restore keeps its original manifest, source checks, validation and
backed-up promotion. Only validation of its private staged tree moves to a
fresh interpreter; the receiver's own validation remains in the native process.
"""
from __future__ import annotations

from datetime import date
import hashlib
import inspect
import json
from pathlib import Path
import subprocess
import sys


CHILD = r'''
import json,sys
from datetime import date
from pathlib import Path
stage,receiver,request_path,result_path=map(lambda p:Path(p).resolve(),sys.argv[1:])
sys.path[:0]=[str(stage),str(receiver)]
request=json.loads(request_path.read_bytes())
import microcap_top100_mom16_biweekly_live_v2_0 as version
if Path(version.__file__).resolve().parent!=stage:
    raise ValueError('Native staged strategy import escaped its validation root')
freq=version.base_mod.freq_mod
namespaces=[vars(freq),freq.list_backtest_universe_symbols.__globals__,freq.load_security_master.__globals__]
for namespace in namespaces:
    for key,relative in (('PRICE_DIR','prices_raw'),('SHARE_DIR','share_change'),
                         ('SECURITY_MASTER_CACHE','security_master.csv')):
        if Path(namespace[key]).resolve()!=stage/'.microcap_index_cache'/relative:
            raise ValueError('Native staged cache/master path escaped its validation root')
    if Path(namespace['ROOT']).resolve()!=stage:
        raise ValueError('Native staged function globals escaped their validation root')
    for key in ('CACHE_DIR','ACTIVE_UNIVERSE','CURRENT_ST','SECURITY_META_DIR','ADJ_PRICE_DIR','OHLC_DIR'):
        value=namespace.get(key)
        if value is not None and not Path(value).resolve().is_relative_to(stage):
            raise ValueError('Native staged ancillary cache escaped its validation root')
    for key in ('SHARED_PRICE_DIR','SHARED_SHARE_DIR','SHARED_ADJ_PRICE_DIR','SHARED_SECURITY_MASTER_CACHE'):
        value=namespace.get(key)
        if value is not None and not Path(value).resolve().is_relative_to(stage):
            raise ValueError('Native staged validation cannot borrow an external shared cache')
base=version.base_mod
if Path(base.ROOT).resolve()!=stage or Path(base.CACHE_DIR).resolve()!=stage/'.microcap_index_cache':
    raise ValueError('Native staged base cache escaped its validation root')
fetch=base.fetch_mod
for namespace in (vars(fetch),fetch.fetch_share_change.__globals__):
    for key,relative in (('ROOT',''),('CACHE_DIR','.microcap_index_cache'),
            ('PRICE_CACHE_DIR','.microcap_index_cache/prices_raw'),
            ('ADJ_PRICE_CACHE_DIR','.microcap_index_cache/prices_qfq'),
            ('SHARE_CACHE_DIR','.microcap_index_cache/share_change'),
            ('UNIVERSE_CACHE','.microcap_index_cache/active_universe.csv'),
            ('CURRENT_ST_CACHE','.microcap_index_cache/current_st.csv')):
        if Path(namespace[key]).resolve()!=stage/relative:
            raise ValueError('Native staged fetch globals escaped their validation root')
    for key in ('SHARED_CACHE_DIR','SHARED_PRICE_CACHE_DIR','SHARED_ADJ_PRICE_CACHE_DIR','SHARED_SHARE_CACHE_DIR'):
        value=namespace.get(key)
        if value is not None and not Path(value).resolve().is_relative_to(stage):
            raise ValueError('Native staged fetch cannot borrow an external shared cache')
from scripts import realtime_state_bundle as state
state_path=Path(state.__file__).resolve()
if state_path not in (stage/'scripts/realtime_state_bundle.py',receiver/'scripts/realtime_state_bundle.py'):
    raise ValueError('Native state validator import escaped its pinned source roots')
kwargs={key:(date.fromisoformat(value['date']) if isinstance(value,dict) else value)
        for key,value in request['kwargs'].items()}
if request['gate']=='validate_state':
    report=state.validate_state(stage,**kwargs)
elif request['gate']=='inspect_outputs':
    from scripts import top100_delivery as delivery
    if Path(delivery.__file__).resolve() not in (stage/'scripts/top100_delivery.py',receiver/'scripts/top100_delivery.py'):
        raise ValueError('Native output validator import escaped its pinned source roots')
    report=delivery.inspect_outputs(stage,**kwargs)
else:
    raise ValueError('Unknown native staged validation function')
result_path.write_text(json.dumps(report,ensure_ascii=False)+'\n',encoding='utf-8')
'''


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _local_file(root, relative):
    path = root / relative
    if (not relative or Path(relative).is_absolute() or '..' in Path(relative).parts
            or not path.resolve().is_relative_to(root) or not path.is_file()):
        raise ValueError('Native staged validation source path is invalid')
    for current in (path, *path.parents):
        if current == root:
            break
        if current.is_symlink() or getattr(current, 'is_junction', lambda: False)():
            raise ValueError('Native staged validation source cannot be linked')
    return path


def _source_identity(receiver, stage, names):
    if not names or len(names) != len(set(names)):
        raise ValueError('Native staged validation requires its complete original source inventory')
    result = {}
    for name in names:
        expected = _sha(_local_file(receiver, name))
        if _sha(_local_file(stage, name)) != expected:
            raise ValueError('Native staged validation source differs: '+name)
        result[name] = expected
    # Namespace imports may also see verifier modules absent from SOURCE_FILES.
    # Every executable staged file must still be the original receiver source.
    for path in [*stage.glob('*.py'), *(stage/'scripts').rglob('*.py')]:
        name = path.relative_to(stage).as_posix()
        expected = _sha(_local_file(receiver, name))
        if _sha(_local_file(stage, name)) != expected:
            raise ValueError('Native staged verifier source differs: '+name)
        result[name] = expected
    return result


def restore_isolated(receiver, bundle, expected, evidence):
    """Delegate the unchanged native restore, isolating only foreign roots."""
    from scripts import top100_cloud_delivery as native
    receiver = Path(receiver).resolve()
    evidence = Path(evidence).resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    original = native.state.validate_state
    original_inspect = native.delivery.inspect_outputs
    source_files = tuple(native.SOURCE_FILES)
    checks = []

    def failure(directory, state, exit_code=None):
        stderr = directory/'stderr.log'
        if stderr.exists():
            with stderr.open('rb') as stream:
                stream.seek(max(0, stderr.stat().st_size-65536))
                tail = stream.read()
            (evidence.parent/'native-staged-validation-failure.stderr.log').write_bytes(tail)
        (evidence.parent/'native-staged-validation-failure.json').write_text(json.dumps(dict(
            status=state, child_exit_code=exit_code, original_stderr=str(stderr),
            original_stderr_sha256=_sha(stderr) if stderr.exists() else None,
            stderr_tail_is_diagnostic_only=True))+'\n', encoding='utf-8')

    def validate(gate, function, path, *args, **kwargs):
        stage = Path(path).resolve()
        if stage == receiver:
            return function(path, *args, **kwargs)
        if stage.is_relative_to(receiver) or receiver.is_relative_to(stage):
            raise ValueError('Native foreign validation root overlaps its receiver')
        before = _source_identity(receiver, stage, source_files)
        bound = inspect.signature(function).bind(path, *args, **kwargs)
        arguments = {key: ({'date': value.isoformat()} if isinstance(value, date) else value)
                     for key, value in bound.arguments.items() if key != 'root'}
        directory = evidence / ('check-'+str(len(checks)+1))
        directory.mkdir()
        request, result = directory/'request.json', directory/'native-report.json'
        request.write_text(json.dumps({'gate': gate, 'kwargs': arguments})+'\n', encoding='utf-8')
        try:
            with (directory/'stdout.log').open('wb') as stdout, (directory/'stderr.log').open('wb') as stderr:
                process = subprocess.run([sys.executable, '-B', '-c', CHILD,
                    str(stage), str(receiver), str(request), str(result)], cwd=stage,
                    stdout=stdout, stderr=stderr, timeout=3600)
        except subprocess.TimeoutExpired:
            failure(directory, 'native_staged_validation_timeout')
            raise
        if process.returncode:
            failure(directory, 'native_staged_validation_failed', process.returncode)
            raise ValueError('Original native staged validation process failed; see its retained stderr')
        if _source_identity(receiver, stage, source_files) != before:
            raise ValueError('Native staged source changed during validation')
        report = json.loads(result.read_bytes())
        if not isinstance(report, dict) or type(report.get('ok')) is not bool:
            raise ValueError('Original native staged validator returned an invalid report')
        checks.append(dict(native_function=gate, stage=str(stage), source_files=before,
                           native_report_sha256=_sha(result), native_report_ok=report['ok'],
                           original_validation_arguments=arguments, child_exit_code=process.returncode))
        return report

    native.state.validate_state = lambda path,*args,**kwargs: validate('validate_state',original,path,*args,**kwargs)
    native.delivery.inspect_outputs = lambda path,*args,**kwargs: validate('inspect_outputs',original_inspect,path,*args,**kwargs)
    try:
        return native.restore(receiver, Path(bundle), expected)
    finally:
        native.state.validate_state = original
        native.delivery.inspect_outputs = original_inspect
        receipt=json.dumps(dict(
            schema='native_foreign_root_validation_isolation_v1', receiver=str(receiver),
            source_rewritten=False, validation_rules_changed=False,
            original_native_validation_checks=checks), ensure_ascii=False, indent=2)+'\n'
        (evidence/'isolation-receipt.json').write_text(receipt,encoding='utf-8')
        (evidence.parent/'native-staged-validation.json').write_text(receipt,encoding='utf-8')
