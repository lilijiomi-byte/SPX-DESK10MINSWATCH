#!/usr/bin/env python3
"""Scoped push of selected paths to GitHub via the git-database API.

Usage: python3 gh_push_scoped.py "<commit message>" <path1> <path2> ...

Paths are relative to ~/workspace/spx-desk. Only the given files are
updated: the new tree is built on base_tree = the remote main tree, so every
other file stays exactly as it is on the remote. This makes single-page
publishes race-safe against concurrent full-tree gh_push.py runs (and vice
versa): neither push can silently drop the other's files.

Same auth as gh_push.py: Secure Vault surrogate for custom.github, Bearer
against api.github.com only. Never `git push` (surrogate 401s on git HTTPS).
The credential value never appears in files, args, or logs.
"""
import base64
import json
import os
import sys
import urllib.request

sys.path.insert(0, "/opt/muse/skills/custom-connector-setup/bin")
from dynamic_credentials import dynamic_credential_entry  # noqa: E402

REPO = os.path.expanduser("~/workspace/spx-desk")
API = "https://api.github.com"


def api(method, path, body=None, token=None):
    req = urllib.request.Request(
        API + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:500]


def main():
    if len(sys.argv) < 3:
        print('usage: python3 gh_push_scoped.py "<commit message>" <path1> [path2 ...]',
              file=sys.stderr)
        return 2
    msg, paths = sys.argv[1], sys.argv[2:]
    os.chdir(REPO)
    for p in paths:
        if not os.path.isfile(p):
            print(f"missing file (not pushed, aborting): {p}", file=sys.stderr)
            return 2
    token = dynamic_credential_entry("custom.github", "access_token")["surrogate"]

    # Mechanically resolve owner/repo: never type the username by hand
    # (lilijiomi-byte vs liljiomi-byte differ by one 'i').
    st, repos = api("GET", "/user/repos?per_page=100", token=token)
    assert st == 200, (st, repos)
    full = next(r["full_name"] for r in repos if "DESK" in r["name"])
    print("target:", full)

    st, ref = api("GET", f"/repos/{full}/git/ref/heads/main", token=token)
    assert st == 200, (st, ref)
    remote_sha = ref["object"]["sha"]
    print("remote:", remote_sha[:8])
    st, rcommit = api("GET", f"/repos/{full}/git/commits/{remote_sha}", token=token)
    assert st == 200, (st, rcommit)
    base_tree = rcommit["tree"]["sha"]

    entries = []
    for f in paths:
        with open(f, "rb") as fh:
            content = base64.b64encode(fh.read()).decode()
        st, blob = api("POST", f"/repos/{full}/git/blobs",
                       {"content": content, "encoding": "base64"}, token)
        assert st == 201, (st, blob)
        print(f"blob: {f} {blob['sha'][:8]}")
        entries.append({"path": f, "mode": "100644", "type": "blob",
                        "sha": blob["sha"]})
    # base_tree = remote tree: only the given files change, everything else
    # stays exactly as on the remote. No unrelated file can be dropped.
    st, tree = api("POST", f"/repos/{full}/git/trees",
                   {"base_tree": base_tree, "tree": entries}, token)
    assert st == 201, (st, tree)
    print("tree:", tree["sha"][:8])

    st, commit = api("POST", f"/repos/{full}/git/commits",
                     {"message": msg, "tree": tree["sha"],
                      "parents": [remote_sha]}, token)
    assert st == 201, (st, commit)
    st, _ = api("PATCH", f"/repos/{full}/git/refs/heads/main",
                {"sha": commit["sha"]}, token)
    assert st == 200, st
    print("pushed:", commit["sha"][:8])
    return 0


if __name__ == "__main__":
    import urllib.error
    sys.exit(main())
