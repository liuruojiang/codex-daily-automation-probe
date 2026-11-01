"""Terminal writeoff label authority and mixed-generation rejection, no sending."""
import pytest
import test_microcap_csv_authority_adversarial as fixtures

@pytest.mark.parametrize('version', ['v2.0','v2.3','v2.5'])
@pytest.mark.parametrize('key', ['terminal_loss_policy','terminal_loss_policy_note'])
@pytest.mark.parametrize('value', ['', 'old_generation'])
def test_stdout_cannot_repair_terminal_policy(tmp_path, version, key, value):
    row = fixtures.complete_row(version)
    stdout = f'{key}: {row[key]}\n'
    row[key] = value
    result = fixtures.run(tmp_path, version, row, stdout)
    assert result['status'] == 'FAILED'
    assert '历史计价口径：' not in result['body']


def test_three_csvs_render_policy_once(tmp_path):
    versions = ['v2.0','v2.3','v2.5']
    result = fixtures.fixtures.MicrocapDigestEmailBodyTests().run_digest(
        tmp_path, {v:'signal\n' for v in versions},
        {v:fixtures.complete_row(v) for v in versions},
        publication_mode='close_confirmed', expected_signal_date='2026-09-03')
    assert result['status'] == 'OK'
    assert result['body'].count('历史计价口径：') == 1
    assert '保守模型假设，非真实成交' in result['body']
    assert '不构成当日卖出指令' in result['body']


def test_mixed_generation_is_failed(tmp_path):
    versions = ['v2.0','v2.3','v2.5']
    rows = {v:fixtures.complete_row(v) for v in versions}
    rows['v2.3'].pop('terminal_loss_policy')
    result = fixtures.fixtures.MicrocapDigestEmailBodyTests().run_digest(
        tmp_path, {v:'signal\n' for v in versions}, rows,
        publication_mode='close_confirmed', expected_signal_date='2026-09-03')
    assert result['status'] == 'FAILED'
