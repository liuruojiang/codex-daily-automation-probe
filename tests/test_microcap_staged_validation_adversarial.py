"""Independent transport attacks; the small native protocol is software only.

These cases execute fresh Python children against explicit local fixtures. They
prove import/argument/error handling, not market data or formal Native gates.
"""
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import microcap_native_staged_validation as isolation
from test_microcap_native_staged_validation import VERSION, STATE, native_fixture


def write_originals(case, relative, content):
    receiver, stage, *_ = case
    for root in (receiver, stage):
        (root / relative).write_text(content, encoding='utf-8')


def run(case, tmp_path):
    return isolation.restore_isolated(case[0], tmp_path / 'original.zip',
                                     '2026-11-12', tmp_path / 'evidence')


@pytest.mark.parametrize('escape', [
    "freq.PRICE_DIR=ROOT.parent/'external-prices'",
    "PRICE_DIR=ROOT.parent/'external-prices'",  # functions differ from namespace
    "SHARED_SECURITY_MASTER_CACHE=ROOT.parent/'external-master.csv'",
    "freq.SHARED_PRICE_DIR=ROOT.parent/'external-prices'",
    "freq.OHLC_DIR=ROOT.parent/'external-ohlc'",
    "freq.SECURITY_META_DIR=ROOT.parent/'external-metadata'",
    "base_mod.CACHE_DIR=ROOT.parent/'external-cache'",
    "freq.PRICE_CACHE_DIR=ROOT.parent/'external-prices'",
    "SHARE_CACHE_DIR=ROOT.parent/'external-shares'",  # fetch function globals
    "SHARED_ADJ_PRICE_CACHE_DIR=ROOT.parent/'external-adjusted'",
])
def test_exact_source_bytes_do_not_authorize_external_cache(native_fixture,
        tmp_path, escape):
    case = native_fixture
    write_originals(case, case[2].SOURCE_FILES[0], VERSION + '\n' + escape + '\n')
    with pytest.raises(ValueError, match='process failed'):
        run(case, tmp_path)
    receipt = json.loads((tmp_path / 'evidence/isolation-receipt.json').read_bytes())
    stderr = (tmp_path / 'native-staged-validation-failure.stderr.log').read_text()
    assert 'validation root' in stderr or 'external shared cache' in stderr
    assert receipt['original_native_validation_checks'] == []
    assert case[2].state.validate_state is case[3] and case[4] == []


@pytest.mark.parametrize('bad_inventory', ['duplicate', 'traversal', 'missing'])
def test_invalid_source_inventory_rejects_before_child(native_fixture, tmp_path,
                                                       bad_inventory):
    receiver, stage, native, original, calls = native_fixture
    if bad_inventory == 'duplicate':
        native.SOURCE_FILES += native.SOURCE_FILES[:1]
    elif bad_inventory == 'traversal':
        native.SOURCE_FILES.append('../outside.py')
    else:
        (stage / native.SOURCE_FILES[0]).unlink()
    with pytest.raises(ValueError, match='source|inventory'):
        run(native_fixture, tmp_path)
    assert not (tmp_path / 'evidence/check-1').exists()
    assert native.state.validate_state is original and calls == []


@pytest.mark.parametrize('receiver_copy', [False, True])
def test_extra_executable_requires_original_receiver_bytes(native_fixture,
        tmp_path, receiver_copy):
    receiver, stage, native, original, calls = native_fixture
    (stage / 'scripts/extra_verifier.py').write_text('VALUE="changed"\n')
    if receiver_copy:
        (receiver / 'scripts/extra_verifier.py').write_text('VALUE="original"\n')
    with pytest.raises(ValueError, match='source|verifier'):
        run(native_fixture, tmp_path)
    assert not (tmp_path / 'evidence/check-1').exists()
    assert native.state.validate_state is original and calls == []


def test_original_receiver_namespace_fallback_keeps_staged_strategy(
        native_fixture, tmp_path):
    receiver, stage, native, original, calls = native_fixture
    # Native's namespace package may supply a validator absent from SOURCE_FILES.
    native.SOURCE_FILES = native.SOURCE_FILES[:1]
    (stage / 'scripts/realtime_state_bundle.py').unlink()
    result = run(native_fixture, tmp_path)
    assert result['native_report']['strategy_root'] == str(stage)
    assert result['native_report']['symbols'] == ['000001']
    assert calls == [receiver] and native.state.validate_state is original


def test_native_argument_types_and_opaque_report_are_preserved(native_fixture,
                                                              tmp_path):
    source = STATE.replace('from pathlib import Path',
                           'from pathlib import Path\nfrom datetime import date')
    source = source.replace('    root=Path(root).resolve()',
        '    assert today is None or type(today) is date\n'
        '    assert close_confirmed_session is None or type(close_confirmed_session) is date\n'
        '    assert max_anchor_age_days is None\n'
        '    assert require_current_refresh_proof is False\n'
        '    root=Path(root).resolve()')
    source = source.replace('return dict(ok=not bad,',
        "return dict(opaque={'nested':[False,None,{'original':'unchanged'}]},ok=not bad,")
    write_originals(native_fixture, 'scripts/realtime_state_bundle.py', source)
    report = run(native_fixture, tmp_path)['native_report']
    assert report['opaque'] == {'nested': [False, None, {'original': 'unchanged'}]}
    assert report['arguments']['today'] == '2026-11-13'
    assert report['arguments']['close_confirmed_session'] == '2026-11-12'
    assert native_fixture[2].state.validate_state is native_fixture[3]


def test_foreign_inspection_preserves_expected_and_original_receiver_function(
        native_fixture, tmp_path):
    receiver, stage, native, original, calls = native_fixture
    original_inspect = native.delivery.inspect_outputs
    source = '''from pathlib import Path
import microcap_top100_mom16_biweekly_live_v2_0 as v
def inspect_outputs(root,expected,*,new_generation=False):
    assert type(expected) is str and type(new_generation) is bool
    return dict(ok=True,errors=[],expected_date=expected,new_generation=new_generation,
        symbols=v.base_mod.freq_mod.list_backtest_universe_symbols(),
        opaque={'original':[None,False,{'nested':'retained'}]},
        root=str(Path(root).resolve()))
'''
    write_originals(native_fixture, 'scripts/top100_delivery.py', source)
    result = run(native_fixture, tmp_path)
    assert result['native_inspection'] == dict(ok=True, errors=[],
        expected_date='2026-11-12', new_generation=False, symbols=['000001'],
        opaque={'original': [None, False, {'nested': 'retained'}]}, root=str(stage))
    assert result['receiver_inspection']['receiver_inspection'] == 'original unchanged native function'
    assert native.delivery.inspect_outputs is original_inspect
    assert native.state.validate_state is original and calls == [receiver]
    checks = json.loads((tmp_path / 'evidence/isolation-receipt.json').read_bytes())[
        'original_native_validation_checks']
    assert [item['native_function'] for item in checks] == ['inspect_outputs', 'validate_state']
    assert checks[0]['original_validation_arguments'] == {
        'expected': '2026-11-12', 'new_generation': False}


def test_original_inspection_rejection_never_advances_to_state_gate(
        native_fixture, tmp_path):
    receiver, stage, native, original, calls = native_fixture
    original_inspect = native.delivery.inspect_outputs
    source = '''def inspect_outputs(root,expected,*,new_generation=False):
    return dict(ok=False,errors=['original-output-inspection-rejected'],
                expected_date=expected,new_generation=new_generation)
'''
    write_originals(native_fixture, 'scripts/top100_delivery.py', source)
    with pytest.raises(ValueError, match='original-output-inspection-rejected'):
        run(native_fixture, tmp_path)
    checks = json.loads((tmp_path / 'evidence/isolation-receipt.json').read_bytes())[
        'original_native_validation_checks']
    assert len(checks) == 1 and checks[0]['native_function'] == 'inspect_outputs'
    assert checks[0]['native_report_ok'] is False
    assert not (tmp_path / 'evidence/check-2').exists()
    assert native.delivery.inspect_outputs is original_inspect
    assert native.state.validate_state is original and calls == []


def test_truthy_non_boolean_native_ok_cannot_be_promoted(native_fixture,
                                                        tmp_path):
    write_originals(native_fixture, 'scripts/realtime_state_bundle.py',
                    STATE.replace('ok=not bad', 'ok=1'))
    with pytest.raises(ValueError, match='invalid report'):
        run(native_fixture, tmp_path)
    assert native_fixture[2].state.validate_state is native_fixture[3]
    assert native_fixture[4] == []


def test_source_change_during_real_child_cannot_earn_check_receipt(
        native_fixture, tmp_path):
    source = STATE.replace('    bad=(root/',
        "    with (root/'microcap_top100_mom16_biweekly_live_v2_0.py').open('a') as f:\n"
        "        f.write('\\n# changed by software validator\\n')\n"
        '    bad=(root/')
    write_originals(native_fixture, 'scripts/realtime_state_bundle.py', source)
    with pytest.raises(ValueError, match='source differs|changed during'):
        run(native_fixture, tmp_path)
    receipt = json.loads((tmp_path / 'evidence/isolation-receipt.json').read_bytes())
    assert receipt['original_native_validation_checks'] == []
    assert native_fixture[2].state.validate_state is native_fixture[3]
    assert native_fixture[4] == []


def test_child_error_retains_bounded_tail_and_full_original_digest(
        native_fixture, tmp_path):
    source = STATE.replace('    bad=(root/',
        "    raise RuntimeError('original-native-sentinel-' + 'x'*70000)\n"
        '    bad=(root/')
    write_originals(native_fixture, 'scripts/realtime_state_bundle.py', source)
    with pytest.raises(ValueError, match='retained stderr'):
        run(native_fixture, tmp_path)
    raw = (tmp_path / 'evidence/check-1/stderr.log').read_bytes()
    tail = (tmp_path / 'native-staged-validation-failure.stderr.log').read_bytes()
    metadata = json.loads((tmp_path / 'native-staged-validation-failure.json').read_bytes())
    assert len(raw) > 65536 and tail == raw[-65536:]
    assert metadata['original_stderr_sha256'] == hashlib.sha256(raw).hexdigest()
    assert metadata['stderr_tail_is_diagnostic_only'] is True
    assert metadata['status'] == 'native_staged_validation_failed'
    assert metadata['child_exit_code'] != 0
    assert not (tmp_path / 'evidence/check-1/native-report.json').exists()
    assert native_fixture[2].state.validate_state is native_fixture[3]
