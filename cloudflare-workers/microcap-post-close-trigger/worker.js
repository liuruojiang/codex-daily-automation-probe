const REPOSITORY = "liuruojiang/codex-daily-automation-probe";
const WORKFLOWS = [
  "microcap-realtime-digest.yml",
  "ic-im-v1-3-daily-digest.yml",
];
const MAX_ATTEMPTS = 3;
const RETRYABLE_STATUS = new Set([408, 429, 500, 502, 503, 504]);

const sleep = (milliseconds) =>
  new Promise((resolve) => setTimeout(resolve, milliseconds));

async function dispatchWorkflow(env, workflow) {
  if (!env.GITHUB_TOKEN) {
    throw new Error("Missing GITHUB_TOKEN secret");
  }

  const dispatchUrl =
    `https://api.github.com/repos/${REPOSITORY}/actions/workflows/` +
    `${workflow}/dispatches`;
  let lastError;

  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt += 1) {
    let response;
    try {
      response = await fetch(dispatchUrl, {
        method: "POST",
        signal: AbortSignal.timeout(30000),
        headers: {
          Accept: "application/vnd.github+json",
          Authorization: `Bearer ${env.GITHUB_TOKEN}`,
          "Content-Type": "application/json",
          "User-Agent": "cloudflare-worker-china-digest-trigger",
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
    } catch (error) {
      lastError = error;
      if (attempt < MAX_ATTEMPTS) {
        await sleep(1000 * attempt);
        continue;
      }
      break;
    }

    if (response.ok) {
      console.log(`${workflow}: GitHub dispatch accepted (${response.status})`);
      return;
    }

    const detail = (await response.text()).slice(0, 500);
    lastError = new Error(
      `${workflow}: GitHub dispatch failed (${response.status}): ${detail}`,
    );
    if (!RETRYABLE_STATUS.has(response.status)) {
      throw lastError;
    }
    if (attempt < MAX_ATTEMPTS) {
      await sleep(1000 * attempt);
    }
  }

  throw lastError ?? new Error(`${workflow}: GitHub dispatch failed`);
}

async function dispatchAllDigests(env) {
  const results = await Promise.allSettled(
    WORKFLOWS.map((workflow) => dispatchWorkflow(env, workflow)),
  );
  const failures = results
    .map((result, index) => ({ result, workflow: WORKFLOWS[index] }))
    .filter(({ result }) => result.status === "rejected");

  if (failures.length > 0) {
    throw new Error(
      failures
        .map(({ result, workflow }) => `${workflow}: ${result.reason}`)
        .join("; "),
    );
  }
}

export default {
  async scheduled(_controller, env, ctx) {
    ctx.waitUntil(dispatchAllDigests(env));
  },
};
