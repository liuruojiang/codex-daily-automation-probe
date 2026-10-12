"""Run the deployed Worker module against isolated GitHub transport counterexamples."""
from pathlib import Path
import json
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "cloudflare-workers/microcap-post-close-trigger/worker.js"
NODE = shutil.which("node")

HARNESS = r"""
import fs from 'node:fs';
const scenario = JSON.parse(process.argv[1]);
globalThis.setTimeout = (callback) => { callback(); return 1; };
const calls = [];
globalThis.fetch = async (url, options) => {
  calls.push({url, body: JSON.parse(options.body)});
  if (scenario.networkError) throw new Error('isolated connection failure');
  const status = calls.length < (scenario.succeedOn || 100) ? scenario.status : 204;
  return {ok: status === 204, status,
    text: async () => {
      if (scenario.bodyError) throw new Error('isolated response body failure');
      return 'isolated response';
    }};
};
const worker = (await import('data:text/javascript;base64,' +
  fs.readFileSync(process.argv[2]).toString('base64'))).default;
const work = [];
let error = '';
try {
  await worker.scheduled({cron: scenario.cron},
    scenario.missingToken ? {} : {GITHUB_TOKEN: 'isolated-test-only'},
    {waitUntil: promise => work.push(promise)});
  await Promise.all(work);
} catch (failure) { error = String(failure.message); }
console.log('RESULT ' + JSON.stringify({calls, error}));
"""


def execute(**scenario):
    assert NODE, "Node is required for the actual Worker transport audit"
    scenario.setdefault("cron", "0 8 * * MON-FRI")
    result = subprocess.run([NODE, "--input-type=module", "-e", HARNESS,
                             json.dumps(scenario), str(WORKER)],
                            capture_output=True, text=True, timeout=8, check=True)
    line = next(line for line in result.stdout.splitlines() if line.startswith("RESULT "))
    return json.loads(line[7:])


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
@pytest.mark.parametrize("body_error", [False, True])
def test_permanent_http_failure_is_not_retried(status, body_error):
    result = execute(status=status, bodyError=body_error)
    assert len(result["calls"]) == 1
    assert f"({status})" in result["error"]


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_retryable_http_failure_has_three_attempt_limit(status):
    result = execute(status=status)
    assert len(result["calls"]) == 3
    assert f"({status})" in result["error"]


def test_connection_failure_is_bounded():
    result = execute(networkError=True)
    assert len(result["calls"]) == 3
    assert "connection failure" in result["error"]


def test_streaming_retryable_http_failure_retains_status_and_budget():
    result = execute(status=503, bodyError=True)
    assert len(result["calls"]) == 3
    assert "(503)" in result["error"]


@pytest.mark.parametrize("cron,workflow", [
    ("0 8 * * MON-FRI", "microcap-realtime-digest.yml"),
    ("0 12 * * MON-FRI", "ic-im-v1-4-daily-digest.yml"),
])
def test_each_cron_dispatches_only_its_own_close_confirmed_workflow(cron, workflow):
    result = execute(cron=cron, status=503, succeedOn=2)
    assert not result["error"]
    assert len(result["calls"]) == 2
    assert all(call["url"].endswith(f"/{workflow}/dispatches") for call in result["calls"])
    assert all(call["body"] == {"ref": "main", "inputs": {
        "correction": False, "external_schedule": True, "publication_mode": "close_confirmed"}}
        for call in result["calls"])


def test_missing_credential_never_sends():
    result = execute(missingToken=True)
    assert result["calls"] == []
    assert "Missing GITHUB_TOKEN" in result["error"]


def test_unknown_cron_never_sends():
    result = execute(cron="0 9 * * MON-FRI")
    assert result["calls"] == []
    assert "Unexpected cron" in result["error"]


def test_actual_config_keeps_both_weekday_beijing_clocks():
    config = json.loads((WORKER.parent / "wrangler.jsonc").read_text(encoding="utf-8"))
    assert config["name"] == "china-post-close-digests"
    assert set(config["triggers"]["crons"]) == {"0 8 * * MON-FRI", "0 12 * * MON-FRI"}
    assert config["workers_dev"] is False and config["preview_urls"] is False
