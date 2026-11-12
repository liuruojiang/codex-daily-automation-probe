"""Actual D12 alias rows; no source collection, signal calculation or SMTP."""
import base64
import copy
import csv
import gzip
import hashlib
import io
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import build_microcap_member_digest as digest

# Exact two membership CSVs from actual no-send run43274045332/artifact13969018632.
# Original full report ZIP SHA8e6089a07b99c30758da3d9e60333340b25e72defc9d897140e2fe65d75455bd.
# The 100 rows and 5 historical *ST labels in each are original observations.
FROZEN_ROWS_B64 = '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'


@pytest.mark.parametrize('name',['effective_members.csv','target_members.csv'])
def test_actual_100_member_alias_rows_keep_all_original_fields_and_label_basis(name):
    payload=json.loads(gzip.decompress(base64.b64decode(FROZEN_ROWS_B64)))
    original=base64.b64decode(payload[name])
    reader=csv.DictReader(io.StringIO(original.decode('utf-8-sig'),newline=''))
    rows=list(reader);header=reader.fieldnames
    before=copy.deepcopy(rows)
    assert len(rows)==100 and sum('ST' in row['name'] for row in rows)==5
    rendered=digest.member_attachment_csv(rows)
    output=csv.DictReader(io.StringIO(rendered.decode('utf-8-sig'),newline=''))
    actual=list(output)
    assert len(actual)==100 and rows==before
    assert set(output.fieldnames)==set(header)|{'name_basis'}
    assert all(row['name_basis']=='historical_member_alias' for row in actual)
    assert [{k:row[k] for k in header} for row in actual]==before
    # Removing only the added explanation column recreates exact original bytes.
    buffer=io.StringIO(newline='');writer=csv.DictWriter(buffer,fieldnames=header)
    writer.writeheader();writer.writerows({k:row[k] for k in header} for row in actual)
    assert buffer.getvalue().encode('utf-8-sig')==original
    assert {row['symbol']:row['name'] for row in actual if 'ST' in row['name']}=={
        row['symbol']:row['name'] for row in before if 'ST' in row['name']}


def test_alias_note_does_not_claim_current_ST_or_invent_current_name_date():
    note=digest.MEMBER_NAME_NOTE
    assert '历史别名' in note and '不代表当前简称或当前 ST 资格' in note
    assert '股票代码' in note
    html=digest.html_document(note)
    assert note in html
    assert 'current_name' not in note and 'asof' not in note


def test_an_existing_conflicting_name_basis_cannot_be_silently_overwritten():
    rows=[dict(symbol='000929',name='*ST historical original',rank=1,name_basis='current_name')]
    before=copy.deepcopy(rows)
    with pytest.raises(ValueError,match='name basis'):
        digest.member_attachment_csv(rows)
    assert rows==before
