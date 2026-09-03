from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "cloudflare-workers" / "microcap-post-close-trigger" / "worker.js"


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


if __name__ == "__main__":
    unittest.main()
