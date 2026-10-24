"""GitHub metadata transport: credentials never follow a redirect."""
from __future__ import annotations

import json
import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("GitHub metadata redirect refused")


def github_request(url, token, *, method="GET", data=None, opener=None):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != "api.github.com" or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("GitHub metadata requires the fixed API origin")
    request = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None,
        headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "codex-etf-delivery",
                 **({"Content-Type": "application/json"} if data is not None else {})}, method=method)
    transport = opener or urllib.request.build_opener(NoRedirect()).open
    with transport(request, timeout=30) as response:
        return response.status, json.load(response)


def get_json(url, token, *, opener=None):
    status, body = github_request(url, token, opener=opener)
    if status != 200:
        raise ValueError("Unexpected GitHub metadata response")
    return body
