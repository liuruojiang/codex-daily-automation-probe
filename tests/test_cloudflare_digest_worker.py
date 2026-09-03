from __future__ import annotations

import unittest
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "cloudflare-workers" / "microcap-post-close-trigger" / "worker.js"
WRANGLER = ROOT / "cloudflare-workers" / "microcap-post-close-trigger" / "wrangler.jsonc"


class CloudflareDigestWorkerTests(unittest.TestCase):
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
        self.assertEqual(config["triggers"]["crons"], ["0 10 * * MON-FRI"])
        self.assertTrue(config["observability"]["enabled"])


if __name__ == "__main__":
    unittest.main()
