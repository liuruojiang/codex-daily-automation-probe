const DISPATCH_URL =
  "https://api.github.com/repos/liuruojiang/codex-daily-automation-probe/actions/workflows/microcap-realtime-digest.yml/dispatches";
const MAX_ATTEMPTS = 3;
const RETRYABLE_STATUS = new Set([408, 429, 500, 502, 503, 504]);

const sleep = (milliseconds) =>
  new Promise((resolve) => setTimeout(resolve, milliseconds));

async function dispatchMicrocapDigest(env) {
  if (!env.GITHUB_TOKEN) {
    throw new Error("Missing GITHUB_TOKEN secret");
  }

  let lastError;
  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt += 1) {
    try {
      const response = await fetch(DISPATCH_URL, {
        method: "POST",
        headers: {
          Accept: "application/vnd.github+json",
          Authorization: `Bearer ${env.GITHUB_TOKEN}`,
          "Content-Type": "application/json",
          "User-Agent": "cloudflare-worker-microcap-trigger",
          "X-GitHub-Api-Version": "2026-03-10",
        },
        body: JSON.stringify({
          ref: "main",
          inputs: {
            correction: false,
            external_schedule: true,
            publication_mode: "close_confirmed",
          },
        }),
      });

      if (response.ok) {
        console.log(`GitHub dispatch accepted with status ${response.status}`);
        return;
      }

      const detail = (await response.text()).slice(0, 500);
      lastError = new Error(
        `GitHub dispatch failed with status ${response.status}: ${detail}`,
      );
      if (!RETRYABLE_STATUS.has(response.status)) {
        throw lastError;
      }
    } catch (error) {
      lastError = error;
      if (attempt === MAX_ATTEMPTS) {
        break;
      }
    }

    await sleep(1000 * attempt);
  }

  throw lastError ?? new Error("GitHub dispatch failed without a response");
}

export default {
  async scheduled(_controller, env, ctx) {
    ctx.waitUntil(dispatchMicrocapDigest(env));
  },
};
