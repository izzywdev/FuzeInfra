// Strips CF_Authorization / CF_AppSession cookies for Grafana /public/build/* requests.
//
// Without this: the CF_Authorization cookie (set domain-wide by CF Access) is included
// in CF's cache key, so every authenticated user gets CF-Cache-Status: BYPASS and hits
// the origin tunnel cold — which 503s under the ~8-request burst on each page load.
//
// With this: cookie is stripped before fetch(), CF computes a cookie-free cache key,
// finds the shared HIT already warm for unauthenticated requests, and returns 200
// from the edge without touching the tunnel.
export default {
  async fetch(request, env, ctx) {
    const headers = new Headers(request.headers);
    const cookie = headers.get("Cookie") || "";
    const stripped = cookie
      .split(";")
      .map(c => c.trim())
      .filter(c => c !== "" && !c.startsWith("CF_Authorization=") && !c.startsWith("CF_AppSession="))
      .join("; ");
    if (stripped) {
      headers.set("Cookie", stripped);
    } else {
      headers.delete("Cookie");
    }
    return fetch(new Request(request, { headers }));
  }
};
