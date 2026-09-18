const REPOSITORY = "liuruojiang/codex-daily-automation-probe";
const WORKFLOWS_BY_CRON = {
  "0 8 * * MON-FRI": ["microcap-realtime-digest.yml"],
  "0 12 * * MON-FRI": ["ic-im-v1-4-daily-digest.yml"],
};
const MAX_ATTEMPTS = 3;
const RETRYABLE_STATUS = new Set([408, 429, 500, 502, 503, 504]);

const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

async function dispatchWorkflow(env, workflow) {
  if (!env.GITHUB_TOKEN) throw new Error("Missing GITHUB_TOKEN secret");
  const url = `https://api.github.com/repos/${REPOSITORY}/actions/workflows/${workflow}/dispatches`;
  let lastError;
  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt += 1) {
    try {
      const response = await fetch(url, {
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
          inputs: { correction: false, external_schedule: true, publication_mode: "close_confirmed" },
        }),
      });
      if (response.ok) {
        console.log(`${workflow}: GitHub dispatch accepted (${response.status})`);
        return;
      }
      const detail = (await response.text()).slice(0, 500);
      lastError = new Error(`${workflow}: GitHub dispatch failed (${response.status}): ${detail}`);
      if (!RETRYABLE_STATUS.has(response.status)) throw lastError;
    } catch (error) {
      lastError = error;
      if (attempt === MAX_ATTEMPTS) break;
    }
    await sleep(1000 * attempt);
  }
  throw lastError ?? new Error(`${workflow}: GitHub dispatch failed`);
}

export default {
  async scheduled(controller, env, ctx) {
    const workflows = WORKFLOWS_BY_CRON[controller.cron] ?? [];
    if (workflows.length === 0) throw new Error(`Unexpected cron trigger: ${controller.cron}`);
    ctx.waitUntil(Promise.all(workflows.map((workflow) => dispatchWorkflow(env, workflow))));
  },
};
