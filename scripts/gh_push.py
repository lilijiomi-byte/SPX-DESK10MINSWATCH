#!/usr/bin/env python3
"""Push ~/workspace/spx-desk to GitHub via the GitHub git-database API.

Why not `git push` over HTTPS: the Secure Vault surrogate (hsurr:...) is only
swapped by authd on Bearer-authenticated API calls to api.github.com; the git
HTTPS transport does not accept it (401 + credential prompt). The REST API
accepts the Bearer surrogate, so we push by creating blobs -> tree -> commit ->
updating refs/heads/main. The credential value never appears in files, args,
or logs.
"""
import base64
import json
import os
import subprocess
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
    os.chdir(REPO)
    token = dynamic_credential_entry("custom.github", "access_token")["surrogate"]

    # Mechanically resolve owner/repo: never type the username by hand
    # (lilijiomi-byte vs liljiomi-byte differ by one 'i').
    _, repos = api("GET", "/user/repos?per_page=100", token=token)
    full = next(r["full_name"] for r in repos if "DESK" in r["name"])
    print("target:", full)

    def ls_files():
        return subprocess.run(["git", "ls-files"], capture_output=True,
                              text=True, check=True).stdout.split()

    blobs = {}   # path -> blob sha
    mtimes = {}  # path -> mtime at upload time

    def upload(path):
        with open(path, "rb") as fh:
            content = base64.b64encode(fh.read()).decode()
        st, blob = api("POST", f"/repos/{full}/git/blobs",
                       {"content": content, "encoding": "base64"}, token)
        assert st == 201, (st, blob)
        blobs[path] = blob["sha"]
        mtimes[path] = os.path.getmtime(path)

    for f in ls_files():
        try:
            upload(f)
        except FileNotFoundError:
            pass  # deleted mid-run; dropped from the final list below
    print("files:", len(blobs))

    # 2026-10-19 stale-list fix: re-snapshot immediately before tree creation.
    # Uploading thousands of blobs takes minutes; files added or modified in
    # the meantime were silently dropped from the tree, while the parent was
    # read fresh just before PATCH -- so the push looked like a clean
    # fast-forward that deleted them. Re-snapshotting shrinks that race
    # window from minutes to seconds.
    fresh = ls_files()
    rescued = 0
    for f in fresh:
        try:
            if f not in blobs or os.path.getmtime(f) != mtimes.get(f):
                upload(f)
                rescued += 1
        except FileNotFoundError:
            pass
    if rescued:
        print(f"re-snapshot rescued {rescued} new/modified file(s)")
    entries = [{"path": f, "mode": "100644", "type": "blob", "sha": blobs[f]}
               for f in fresh if f in blobs]
    st, tree = api("POST", f"/repos/{full}/git/trees", {"tree": entries}, token)
    assert st == 201, (st, tree)

    st, ref = api("GET", f"/repos/{full}/git/ref/heads/main", token=token)
    parents = []
    if st == 200:
        remote_sha = ref["object"]["sha"]
        _, rcommit = api("GET", f"/repos/{full}/git/commits/{remote_sha}", token=token)
        if rcommit["tree"]["sha"] == tree["sha"]:
            print("nothing to push (tree unchanged)")
            return 0
        parents = [remote_sha]
    elif st != 404:
        print("ref lookup failed:", st, ref)
        return 1

    msg = subprocess.run(["git", "log", "-1", "--format=%s"], capture_output=True,
                         text=True).stdout.strip() or "update"
    st, commit = api("POST", f"/repos/{full}/git/commits",
                     {"message": msg, "tree": tree["sha"], "parents": parents},
                     token)
    assert st == 201, (st, commit)
    if parents:
        st, _ = api("PATCH", f"/repos/{full}/git/refs/heads/main",
                    {"sha": commit["sha"]}, token)
        assert st == 200, st
    else:
        st, _ = api("POST", f"/repos/{full}/git/refs",
                    {"ref": "refs/heads/main", "sha": commit["sha"]}, token)
        assert st == 201, st
    print("pushed:", commit["sha"][:8])
    return 0


if __name__ == "__main__":
    import urllib.error
    sys.exit(main())
