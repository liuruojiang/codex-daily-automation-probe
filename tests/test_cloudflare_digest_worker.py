from __future__ import annotations

import unittest
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "cloudflare-workers" / "microcap-post-close-trigger" / "worker.js"
WRANGLER = ROOT / "cloudflare-workers" / "microcap-post-close-trigger" / "wrangler.jsonc"


class CloudflareDigestWorkerTests(unittest.TestCase):
    def test_scheduled_routes_each_digest_to_its_own_time(self) -> None:
        source = WORKER.read_text(encoding="utf-8").replace("export default {", "const worker = {")
        harness = '''
        const calls = [];
        globalThis.fetch = async url => { calls.push(url.split('/').at(-2)); return {ok:true,status:204}; };
        const outcomes = [];
        for (const cron of ['0 8 * * MON-FRI','0 12 * * MON-FRI','unexpected']) {
          calls.length = 0;
          let pending;
          await worker.scheduled({cron}, {GITHUB_TOKEN:'fixture'}, {waitUntil:p=>pending=p});
          await pending;
          outcomes.push([...calls]);
        }
        console.log(JSON.stringify(outcomes));
        '''
        result = subprocess.run(['node','--input-type=module'], input=source+harness, text=True, capture_output=True, check=True, timeout=10)
        self.assertEqual(json.loads(result.stdout.strip().splitlines()[-1]), [['microcap-realtime-digest.yml'], ['ic-im-v1-3-daily-digest.yml'], []])

    def test_worker_dispatches_both_post_close_workflows(self) -> None:
        text = WORKER.read_text(encoding="utf-8")

        self.assertIn('"microcap-realtime-digest.yml"', text)
        self.assertIn('"ic-im-v1-3-daily-digest.yml"', text)
        self.assertIn('publication_mode: "close_confirmed"', text)
        self.assertIn("external_schedule: true", text)
        self.assertIn("correction: false", text)
        self.assertIn("Promise.allSettled", text)
        self.assertIn("MAX_ATTEMPTS = 3", text)

    def test_wrangler_config_deploys_the_scheduled_only_worker(self) -> None:
        config = json.loads(WRANGLER.read_text(encoding="utf-8"))

        self.assertEqual(config["name"], "china-post-close-digests")
        self.assertEqual(config["main"], "./worker.js")
        self.assertFalse(config["workers_dev"])
        self.assertFalse(config["preview_urls"])
        self.assertEqual(config["triggers"]["crons"], ["0 8 * * MON-FRI", "0 12 * * MON-FRI"])
        self.assertTrue(config["observability"]["enabled"])


if __name__ == "__main__":
    unittest.main()
