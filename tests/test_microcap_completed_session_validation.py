"""Run the real workflow resolver under an explicit software midnight clock."""
from contextlib import redirect_stdout
from datetime import date, datetime
import io
from pathlib import Path
import sys
import types
from unittest.mock import patch

import pytest
import yaml

ROOT=Path(__file__).resolve().parents[1]


def resolve(*, validation, persist='false', external='false', completed='2026-11-12'):
    flow=yaml.load((ROOT/'.github/workflows/microcap-member-daily.yml').read_text(encoding='utf-8'),Loader=yaml.BaseLoader)
    step=next(s for s in flow['jobs']['send']['steps'] if s.get('name')=='Resolve independent completed session')
    body=step['run'].split('\n',1)[1].rsplit('\nPY',1)[0]
    # Only wall-clock and calendar transport are software adapters. Execute
    # the actual resolver and its original guard, without faking a run result.
    body=body.replace('from datetime import datetime','')
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):
            instant=datetime.fromisoformat('2026-11-13T00:30:00+08:00')
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)
    calendar=types.ModuleType('scripts.exchange_calendar')
    calendar.sessions_for_day=lambda _: (date(2026,11,12),date(2026,11,13))
    calendar.latest_completed_session=lambda: date.fromisoformat(completed)
    output=io.StringIO()
    with patch.dict('os.environ',dict(MODE='close_confirmed',VALIDATE=validation,
            PERSIST_CHECKPOINT=persist,EXTERNAL_SCHEDULE=external)), \
         patch.dict(sys.modules,{'scripts.exchange_calendar':calendar}),redirect_stdout(output):
        exec(compile(body,'actual-daily-session-resolver','exec'),{'datetime':Clock})
    return output.getvalue()


def test_readonly_midnight_validation_uses_actual_completed_day_without_retagging():
    assert resolve(validation='true')=='trading=true\ndate=2026-11-12\n'


@pytest.mark.parametrize('validation,persist,external',[
    ('false','false','false'),('true','true','false'),('true','false','true')])
def test_sending_persistence_or_external_schedule_keeps_same_day_close_guard(validation,persist,external):
    with pytest.raises(ValueError,match='earlier completed session'):
        resolve(validation=validation,persist=persist,external=external)


def test_same_completed_day_still_follows_original_normal_path():
    assert resolve(validation='false',completed='2026-11-13')=='trading=true\ndate=2026-11-13\n'
