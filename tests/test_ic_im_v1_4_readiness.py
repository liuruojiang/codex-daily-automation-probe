"""The readiness path uses production settings and cannot send or persist state."""
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('mail_transport_probe', ROOT / 'scripts/check_mail_transport.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def test_readiness_never_uploads_formal_ledger_or_delivery_marker():
    text = (ROOT / '.github/workflows/ic-im-v1-4-delivery-readiness.yml').read_text()
    assert 'workflow_dispatch:' in text and 'schedule:' not in text
    assert 'family: icim' in text
    assert 'send_report.py' not in text
    assert 'name: ic-im-v1-4-r1-ledger\n' not in text
    assert 'name: ${{ steps.prepare_marker.outputs.marker_name }}' not in text
    assert 'ref: ${{ steps.pin.outputs.sha }}' in text
    assert '--state-dir readiness-state' in text and '--required' in text
    assert "ICIM_REQUIRE_MIGRATION: '1'" in text
    assert 'ICIM_CHINABOND_SNAPSHOT_FILE: readiness-artifacts/chinabond.json' in text
    assert '--expected-date "$COMPLETED_DAY"' in text
    assert text.index('strategy/ic_im_chinabond.py') < text.index('strategy/run_ic_im_v1_4_github_digest.py')
    assert 'delivery_contract.CALENDAR_YEARS == bot._OFFICIAL_EXCHANGE_CALENDAR_YEARS' in text
    assert 'delivery_contract.EXCHANGE_CLOSURES == bot._EXCHANGE_CLOSURES' in text


@pytest.mark.parametrize('use_ssl', [True, False])
@pytest.mark.parametrize('reject', [False, True])
def test_transport_probe_checks_all_recipients_and_never_submits_data(monkeypatch, use_ssl, reject):
    values = {'MAIL_SERVER': 'smtp.test.invalid', 'MAIL_PORT': '465' if use_ssl else '587',
              'MAIL_USERNAME': 'test', 'MAIL_PASSWORD': 'test-only',
              'MAIL_FROM': 'a@example.invalid', 'MAIL_TO': 'b@example.invalid,c@example.invalid'}
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    smtp = MagicMock()
    smtp.__enter__.return_value = smtp
    smtp.mail.return_value = (250, b'accepted')
    smtp.rcpt.side_effect = [(250, b'accepted'), (550 if reject else 250, b'result')]
    smtp.rset.return_value = (250, b'reset')
    monkeypatch.setattr(probe.smtplib, 'SMTP_SSL', lambda *a, **k: smtp)
    monkeypatch.setattr(probe.smtplib, 'SMTP', lambda *a, **k: smtp)
    if reject:
        with pytest.raises(RuntimeError, match='recipient'):
            probe.probe()
    else:
        result = probe.probe()
        assert result['recipients_accepted'] == 2 and not result['message_sent']
    smtp.login.assert_called_once()
    assert smtp.rcpt.call_count == 2
    smtp.rset.assert_called_once()
    smtp.data.assert_not_called()
    smtp.send_message.assert_not_called()
    smtp.sendmail.assert_not_called()
    assert smtp.starttls.call_count == (0 if use_ssl else 1)
