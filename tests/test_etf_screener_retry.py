from __future__ import annotations

import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import etf_movers as movers


class RateLimitError(Exception):
    pass


class HttpError(OSError):
    def __init__(self, status: int):
        super().__init__(f"HTTP {status}")
        self.response = SimpleNamespace(status_code=status)


def response(rows: list[dict] | None = None, *, payload=None) -> Mock:
    result = Mock()
    result.json.return_value = (
        {"finance": {"error": None, "result": [{"quotes": rows or []}]}}
        if payload is None else payload
    )
    return result


@contextmanager
def screener(effects):
    client = Mock()
    client.post.side_effect = effects
    modules = {
        "yfinance.data": SimpleNamespace(YfData=lambda: client),
        "yfinance.exceptions": SimpleNamespace(YFRateLimitError=RateLimitError),
    }
    with patch.dict(sys.modules, modules), patch.object(movers.time, "sleep") as sleep:
        yield client.post, sleep


class EtfScreenerRetryTests(unittest.TestCase):
    def test_timeout_recovers_on_same_page_without_changing_request(self):
        rows = [{"symbol": "SPY", "regularMarketTime": 1791835200}]
        with screener([TimeoutError("injected transport failure"), response(rows)]) as (post, sleep):
            self.assertEqual(movers._fetch_page(250, 250, 5.0), rows)
        self.assertEqual(post.call_count, 2)
        self.assertEqual(post.call_args_list[0], post.call_args_list[1])
        self.assertEqual(post.call_args.kwargs["body"]["offset"], 250)
        self.assertEqual(post.call_args.kwargs["timeout"], 30)
        sleep.assert_called_once_with(2.0)

    def test_transient_http_errors_and_rate_limit_recover(self):
        partial_body_timeout = TimeoutError("timed out after response headers")
        partial_body_timeout.response = SimpleNamespace(status_code=200)
        for error in (HttpError(408), HttpError(429), HttpError(503), RateLimitError(), partial_body_timeout):
            with self.subTest(error=repr(error)), screener([error, response()]) as (post, sleep):
                self.assertEqual(movers._fetch_page(0, 250, 5.0), [])
                self.assertEqual(post.call_count, 2)
                sleep.assert_called_once()

    def test_http_error_from_raise_for_status_is_retried(self):
        failed = response()
        failed.raise_for_status.side_effect = HttpError(502)
        with screener([failed, response([{"symbol": "SPY"}])]) as (post, _sleep):
            self.assertEqual(movers._fetch_page(0, 250, 5.0), [{"symbol": "SPY"}])
            self.assertEqual(post.call_count, 2)

    def test_retry_exhaustion_is_bounded_and_keeps_original_cause(self):
        error = TimeoutError("injected persistent timeout")
        with screener([error] * 3) as (post, sleep):
            with self.assertRaisesRegex(RuntimeError, "offset 500 unavailable after 3 attempts") as caught:
                movers._fetch_page(500, 250, 5.0)
        self.assertIs(caught.exception.__cause__, error)
        self.assertEqual(post.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [2.0, 5.0])

    def test_permanent_http_errors_and_programming_errors_are_not_retried(self):
        for error in (HttpError(400), HttpError(401), HttpError(403), HttpError(404), TypeError("bug")):
            with self.subTest(error=repr(error)), screener([error]) as (post, sleep):
                with self.assertRaises(type(error)):
                    movers._fetch_page(0, 250, 5.0)
                post.assert_called_once()
                sleep.assert_not_called()

    def test_mid_scan_failure_never_returns_partial_universe(self):
        first_page = [{"symbol": f"TEST{i}"} for i in range(250)]
        effects = [response(first_page)] + [TimeoutError("page two unavailable")] * 3
        with screener(effects) as (post, _sleep):
            with self.assertRaisesRegex(RuntimeError, "offset 250 unavailable"):
                movers.fetch_universe()
        self.assertEqual([call.kwargs["body"]["offset"] for call in post.call_args_list], [0, 250, 250, 250])

    def test_recovered_later_page_preserves_all_rows_and_stops_on_valid_empty_page(self):
        effects = [response([{"symbol": "SPY"}]), TimeoutError(), response([{"symbol": "QQQM"}]), response()]
        with screener(effects):
            self.assertEqual(movers.fetch_universe(), [{"symbol": "SPY"}, {"symbol": "QQQM"}])

    def test_error_or_malformed_response_is_not_pagination_exhaustion(self):
        payloads = [
            {},
            {"finance": {"error": {"code": "Unavailable"}, "result": []}},
            {"finance": {"error": None, "result": []}},
            {"finance": {"result": [{}]}},
            {"finance": {"result": [{"quotes": [None]}]}},
            {"finance": {"result": [{"quotes": [{}]}]}},
        ]
        for payload in payloads:
            with self.subTest(payload=payload), screener([response(payload=payload)]) as (post, sleep):
                with self.assertRaisesRegex(RuntimeError, "Invalid ETF screener response at offset 250"):
                    movers._fetch_page(250, 250, 5.0)
                post.assert_called_once()
                sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
