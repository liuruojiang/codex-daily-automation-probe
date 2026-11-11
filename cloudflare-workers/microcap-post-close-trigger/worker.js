const REPOSITORY = "liuruojiang/codex-daily-automation-probe";
const WORKFLOWS_BY_CRON = {
  "0 8 * * MON-FRI": ["microcap-realtime-digest.yml"],
  "0 12 * * MON-FRI": ["ic-im-v1-4-daily-digest.yml"],
  "30 21 * * *": ["etf-allocation-digest.yml"],
  "0 1 * * *": ["ai-hot-digest.yml"],
};
const INPUTS_BY_WORKFLOW = {
  "microcap-realtime-digest.yml": { correction: false, external_schedule: true, publication_mode: "close_confirmed" },
  "ic-im-v1-4-daily-digest.yml": { correction: false, external_schedule: true, publication_mode: "close_confirmed" },
  "etf-allocation-digest.yml": { send_email: true },
  "ai-hot-digest.yml": { send_email: true },
  "microcap-member-daily.yml": { validation_only: false, external_schedule: true, publication_mode: "close_confirmed" },
};
const MAX_ATTEMPTS = 3;
const RETRYABLE_STATUS = new Set([408, 429, 500, 502, 503, 504]);

const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

function dispatchKey(workflow, inputs, scheduledTime) {
  if (!["microcap-member-daily.yml", "microcap-member-maintenance.yml"].includes(workflow)) return null;
  const stamp = new Date(scheduledTime).toISOString().replace(/[-:.]/g, "");
  return `microcap-${inputs.task ?? "daily"}-${stamp}`;
}

async function reconcileDispatch(env, workflow, key, scheduledTime) {
  if (!key) return false;
  // A POST can be accepted before its response is lost. Reads are safe to
  // retry; another dispatch is not. Match the exact title and workflow slot.
  const since = new Date(scheduledTime - 60000).toISOString();
  const url = `https://api.github.com/repos/${REPOSITORY}/actions/workflows/${workflow}/runs?event=workflow_dispatch&branch=main&per_page=100&created=${encodeURIComponent(">=" + since)}`;
  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt += 1) {
    try {
      const response = await fetch(url, {
        signal: AbortSignal.timeout(15000),
        headers: {
          Accept: "application/vnd.github+json",
          Authorization: `Bearer ${env.GITHUB_TOKEN}`,
          "User-Agent": "cloudflare-worker-china-digest-trigger",
          "X-GitHub-Api-Version": "2026-03-10",
        },
      });
      if (response.ok) {
        const payload = await response.json();
        if (Array.isArray(payload.workflow_runs) && payload.workflow_runs.some((run) =>
          run.display_title === `microcap-dispatch:${key}` && run.head_branch === "main" &&
          run.event === "workflow_dispatch" &&
          run.path === `.github/workflows/${workflow}` &&
          Number.isFinite(Date.parse(run.created_at)) && Date.parse(run.created_at) >= scheduledTime - 60000 &&
          run.repository?.full_name === REPOSITORY)) {
          console.log(`${workflow}: accepted dispatch recovered by exact scheduled slot`);
          return true;
        }
      } else if ([401, 403, 404, 422].includes(response.status)) {
        break;
      }
    } catch {
      // Only a read is retried. Retain an unknown POST outcome if lookup fails.
    }
    if (attempt < MAX_ATTEMPTS) await sleep(1000 * attempt);
  }
  return false;
}

async function dispatchWorkflow(env, workflow, inputs = INPUTS_BY_WORKFLOW[workflow], scheduledTime) {
  if (!env.GITHUB_TOKEN) throw new Error("Missing GITHUB_TOKEN secret");
  const key = dispatchKey(workflow, inputs, scheduledTime);
  if (key) inputs = { ...inputs, dispatch_key: key };
  const url = `https://api.github.com/repos/${REPOSITORY}/actions/workflows/${workflow}/dispatches`;
  let lastError;
  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt += 1) {
    let response;
    try {
      response = await fetch(url, {
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
          inputs,
        }),
      });
    } catch (error) {
      if (await reconcileDispatch(env, workflow, key, scheduledTime)) return;
      throw new Error(`${workflow}: dispatch acceptance unknown after connection failure; no repeat POST; reconcile the scheduled slot`);
    }
    if (response.ok) {
      console.log(`${workflow}: GitHub dispatch accepted (${response.status})`);
      return;
    }
    let detail;
    try {
      detail = (await response.text()).slice(0, 500);
    } catch {
      detail = "response body unavailable";
    }
    lastError = new Error(`${workflow}: GitHub dispatch failed (${response.status}): ${detail}`);
    if (key && (response.status === 408 || response.status >= 500)) {
      if (await reconcileDispatch(env, workflow, key, scheduledTime)) return;
      throw new Error(`${workflow}: dispatch acceptance unknown (${response.status}); no repeat POST; reconcile the scheduled slot`);
    }
    // Classify outside the connection catch: a permanent HTTP error must
    // not be converted into another dispatch, even if reading its body fails.
    if (!RETRYABLE_STATUS.has(response.status)) throw lastError;
    if (attempt < MAX_ATTEMPTS) await sleep(1000 * attempt);
  }
  throw lastError ?? new Error(`${workflow}: GitHub dispatch failed`);
}

export default {
  async scheduled(controller, env, ctx) {
    // Activation follows a verified seed and no-send acceptance. The existing
    // 16:00 route remains the sole route until this explicit deployment flag.
    if (controller.cron === "10 7 * * MON-FRI") {
      if (env.MEMBER_DAILY_ENABLED !== "true") return;
      ctx.waitUntil(dispatchWorkflow(env, "microcap-member-maintenance.yml", { task: "rebalance" }, controller.scheduledTime));
      return;
    }
    if (controller.cron === "0 1 * * *" && env.MEMBER_DAILY_ENABLED === "true") {
      const weekday = new Date(controller.scheduledTime).getUTCDay();
      if (weekday >= 1 && weekday <= 5) {
        ctx.waitUntil(dispatchWorkflow(env, "microcap-member-maintenance.yml", { task: "maintenance" }, controller.scheduledTime));
      }
    }
    const workflows = WORKFLOWS_BY_CRON[controller.cron] ?? [];
    if (workflows.length === 0) throw new Error(`Unexpected cron trigger: ${controller.cron}`);
    ctx.waitUntil(Promise.all(workflows.map((workflow) => {
      const selected = workflow === "microcap-realtime-digest.yml" && env.MEMBER_DAILY_ENABLED === "true"
        ? "microcap-member-daily.yml" : workflow;
      return dispatchWorkflow(env, selected, INPUTS_BY_WORKFLOW[selected], controller.scheduledTime);
    })));
  },
};
