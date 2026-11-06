"""Run native refresh with bounded retries confined to failed source requests.

This launcher does not alter strategy files, stored data, or validation results.
Every notice attempt uses the native transactional archive and retains its own
raw pages. All other stages execute once through the unchanged native CLI.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys


def retry_notice_refresh(refresh, error_type, log_phase, **kwargs):
    for attempt in range(1, 4):
        try:
            return refresh(**kwargs)
        except error_type as exc:
            if str(exc) != "Daily notice pagination total changed" or attempt == 3:
                raise
            log_phase(f"retry_notice_pagination_{attempt}_of_2")


def retry_universe_timeout(fetch, log_phase, provider, **kwargs):
    # Actual 2026-11-06 cloud trace: 55/59 pages after 240 seconds.
    # Keep the caller's explicit deadline; only the native default needs room
    # for this paginated all-market source to finish, without accepting a tail.
    if provider == 'stock_zh_a_spot_em' and 'timeout' not in kwargs:
        kwargs = {**kwargs, 'timeout': 360}
    try:
        return fetch(provider, **kwargs)
    except TimeoutError:
        if provider not in {'stock_zh_a_spot', 'stock_zh_a_spot_em'}:
            raise
        log_phase(f"retry_universe_timeout_{provider}_1_of_1")
        return fetch(provider, **kwargs)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--root', type=Path, required=True)
    args, _ = parser.parse_known_args(argv)
    sys.path.insert(0, str(args.root.resolve()))
    from scripts import realtime_state_bundle as state
    from scripts import st_notice_refresh as notice
    from scripts import bounded_market_data as market
    from scripts.st_notice_evidence import StNoticeEvidenceError

    original = notice.refresh_notice_archive
    original_market = market.fetch_akshare_frame
    def retry(**kwargs):
        return retry_notice_refresh(original, StNoticeEvidenceError, state._log_refresh_phase, **kwargs)
    notice.refresh_notice_archive = retry
    def retry_market(provider, **kwargs):
        return retry_universe_timeout(original_market, state._log_refresh_phase, provider, **kwargs)
    market.fetch_akshare_frame = retry_market
    try:
        return state.main(argv)
    finally:
        notice.refresh_notice_archive = original
        market.fetch_akshare_frame = original_market


if __name__ == '__main__':
    raise SystemExit(main())
