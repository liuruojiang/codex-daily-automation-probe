"""Give one partial-information delivery its own idempotency transaction."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
from datetime import date
from check_microcap_delivery import delivery_marker_name, fetch_artifacts, marker_exists, verify_smtp_receipt, write_outputs


def choose(metadata, normal_marker):
    if metadata.get('schema') != 'member_daily_report_v1' or metadata.get('status') not in {'OK', 'PARTIAL'}:
        raise ValueError('Unsupported member report identity')
    raw_day=metadata.get('signal_date')
    try:
        day=date.fromisoformat(raw_day)
    except (TypeError,ValueError) as exc:
        raise ValueError('Member report requires its canonical signal date') from exc
    if day.isoformat()!=raw_day or metadata.get('publication_mode') not in {'realtime','close_confirmed'}:
        raise ValueError('Member report date/publication mode differs')
    if normal_marker!=delivery_marker_name(day,metadata['publication_mode']):
        raise ValueError('Member report and delivery transaction date/mode differ')
    return normal_marker + ('-partial-information' if metadata['status'] == 'PARTIAL' else '')


def validate_member_delivery_marker(metadata, marker):
    """Check the exact transaction immediately before any SMTP side effect."""
    normal=marker.removesuffix('-partial-information')
    if choose(metadata,normal)!=marker:
        raise ValueError('Member report and complete/partial transaction differ')
    return marker


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--metadata', type=Path, required=True)
    p.add_argument('--normal-marker', required=True)
    p.add_argument('--validation-only', action='store_true')
    args=p.parse_args()
    meta=json.loads(args.metadata.read_bytes())
    name=choose(meta,args.normal_marker)
    result={'marker_name':name,'should_send':'true','recover_marker':'false'}
    if not args.validation_only:
        repository, token=os.environ['GITHUB_REPOSITORY'],os.environ['GITHUB_TOKEN']
        api='https://api.github.com'
        # Normal gate already checked the normal transaction. A partial
        # report must never write that transaction's intent or marker.
        if name != args.normal_marker:
            if marker_exists(fetch_artifacts(repository,token,name),name):
                result['should_send']='false'
            else:
                receipt_name=name+'-smtp-accepted'
                receipts=fetch_artifacts(repository,token,receipt_name)['artifacts']
                candidates=[x for x in receipts if x.get('name')==receipt_name and not x.get('expired')]
                if candidates:
                    verify_smtp_receipt(repository,token,api,candidates[0],date.fromisoformat(meta['signal_date']),meta['publication_mode'])
                    result.update(should_send='false',recover_marker='true')
                elif marker_exists(fetch_artifacts(repository,token,name+'-send-intent'),name+'-send-intent'):
                    raise RuntimeError('Partial-information send intent has unknown terminal state; reconcile original SMTP/INBOX before retry')
    write_outputs(result)


if __name__=='__main__':
    main()
