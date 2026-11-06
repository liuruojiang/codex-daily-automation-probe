"""Portable native-output -> consumer contract; no second repository required.

The fixture was produced by the strategy's offline builder/state/runner path.
It is synthetic software evidence, never a saved live market observation.
"""
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import build_ic_im_v1_4_digest as consumer
import build_local_intraday_report as local


def test_frozen_native_builder_output_reaches_text_html_and_local_report(tmp_path):
    fixture=ROOT/'tests/fixtures/icim_observation_publication_20261106'
    payload=json.loads((fixture/'native_observation.json').read_text(encoding='utf-8'))
    provenance=json.loads((fixture/'provenance.json').read_text(encoding='utf-8'))
    canonical=json.dumps(payload,ensure_ascii=True,sort_keys=True,separators=(',',':')).encode()
    assert provenance['not_live_data'] is True
    assert provenance['kind']=='synthetic_offline_software_fixture'
    assert hashlib.sha256(canonical).hexdigest()==provenance['canonical_json_sha256']
    subject,body,actionable=consumer.build_success(payload,'','')
    assert '0.25' in body and '0.75' in body and '保护未落实' in body
    assert 'N/A' in body and actionable is True
    rendered_html=consumer.build_success_html(payload,'')
    assert '0.25' in rendered_html and '保护未落实' in rendered_html
    source=tmp_path/'result.json'
    source.write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8')
    rendered=local.render(mode='replay',signal_day='2026-11-06',anchor_day='2026-11-05',
                          next_session='2026-11-09',icim_result=source)
    assert '0.25' in rendered['report'] and '0.75' in rendered['report']
    assert '保护未落实' in rendered['report']
    assert rendered['manifest']['actual_publication'] is False
    assert rendered['manifest']['needs_actual_publication'] is False
