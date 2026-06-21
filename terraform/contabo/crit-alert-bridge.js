/**
 * Cloudflare Worker — Grafana CRIT-alert → GitHub repository_dispatch bridge.
 *
 * Design choice: this Worker is intentionally thin. It validates the webhook,
 * checks it is firing (not resolved), and hands off to GitHub. Log fetching
 * and Claude analysis happen inside the GHA workflow runner, which already
 * has KUBE_CONFIG access and can kubectl port-forward to Loki directly.
 * Doing the Loki query here would require exposing Loki through the CF tunnel
 * with an extra Access bypass — unnecessary complexity.
 */
export default {
  async fetch(request, env) {
    if (request.method !== "POST") {
      return new Response("Method Not Allowed", { status: 405 });
    }

    // Optional shared-secret gate (set BRIDGE_TOKEN in Worker secrets + Grafana contact point).
    const auth = request.headers.get("Authorization") || "";
    if (env.BRIDGE_TOKEN && auth !== `Bearer ${env.BRIDGE_TOKEN}`) {
      return new Response("Unauthorized", { status: 401 });
    }

    let body;
    try {
      body = await request.json();
    } catch {
      return new Response("Bad Request — invalid JSON", { status: 400 });
    }

    // Grafana sends status "firing" or "resolved"; only act on firing.
    if (body.status !== "firing") {
      return new Response("OK — resolved alert, ignoring", { status: 200 });
    }

    const alerts = body.alerts ?? [];
    const firstAlert = alerts[0] ?? {};
    const summary =
      firstAlert.annotations?.summary ??
      body.commonAnnotations?.summary ??
      "CRIT log detected";
    const description =
      firstAlert.annotations?.description ??
      body.commonAnnotations?.description ??
      "";
    const labels = JSON.stringify(firstAlert.labels ?? body.commonLabels ?? {});
    const firedAt = firstAlert.startsAt ?? new Date().toISOString();

    const repo = env.GITHUB_REPO; // "owner/repo"
    const resp = await fetch(`https://api.github.com/repos/${repo}/dispatches`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GITHUB_TOKEN}`,
        Accept: "application/vnd.github.v3+json",
        "Content-Type": "application/json",
        "User-Agent": "FuzeInfra-CritAlert-Bridge/1.0",
      },
      body: JSON.stringify({
        event_type: "grafana-crit-alert",
        client_payload: { summary, description, labels, fired_at: firedAt },
      }),
    });

    if (!resp.ok) {
      const txt = await resp.text();
      return new Response(`GitHub dispatch failed (${resp.status}): ${txt}`, {
        status: 502,
      });
    }

    return new Response("OK — dispatched to GitHub Actions", { status: 200 });
  },
};
