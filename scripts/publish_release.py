#!/usr/bin/env python3
"""
Create a GitHub release and upload dist/SOR-Public-Archiver-Windows.zip.

Auth: git credential helper (same as git push). Does not print tokens.
Usage:
    python scripts/publish_release.py [--tag v1.3.0]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

OWNER = "HyperboreanSlug"
REPO = "sor-public-archiver"
ASSET_NAME = "SOR-Public-Archiver-Windows.zip"


def git_cred() -> tuple[str, str]:
    p = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    user, password = "", ""
    for line in p.stdout.splitlines():
        if line.startswith("username="):
            user = line.split("=", 1)[1].strip()
        elif line.startswith("password="):
            password = line.split("=", 1)[1].strip()
    if not password:
        raise SystemExit("No GitHub credentials from git credential helper")
    return user or OWNER, password


def api_request(
    method: str,
    url: str,
    token: str,
    data: bytes | None = None,
    content_type: str = "application/json",
) -> dict | list | None:
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", f"{OWNER}-{REPO}-release-script")
    if data is not None:
        req.add_header("Content-Type", content_type)
        req.add_header("Content-Length", str(len(data)))
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            body = resp.read()
            if not body:
                return None
            return json.loads(body.decode("utf-8"))
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")
        raise SystemExit(f"GitHub API {method} {url} failed: {e.code}\n{err[:800]}") from e


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="v1.3.3")
    ap.add_argument(
        "--zip",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "dist"
        / "SOR-Public-Archiver-Windows.zip",
    )
    args = ap.parse_args()
    zip_path: Path = args.zip
    if not zip_path.is_file():
        raise SystemExit(f"Missing package: {zip_path}\nRun: python build_exe.py")

    tag = args.tag
    _, token = git_cred()
    size_mb = zip_path.stat().st_size / (1024 * 1024)
    print(f"Publishing {tag} with {zip_path.name} ({size_mb:.1f} MB) …")

    # Ensure tag exists on remote
    root = Path(__file__).resolve().parents[1]
    subprocess.run(["git", "fetch", "--tags"], cwd=root, check=False)
    existing = subprocess.run(
        ["git", "rev-parse", "--verify", f"refs/tags/{tag}"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if existing.returncode != 0:
        subprocess.check_call(["git", "tag", "-a", tag, "-m", f"Release {tag}"], cwd=root)
        subprocess.check_call(["git", "push", "origin", tag], cwd=root)
        print(f"Created and pushed tag {tag}")
    else:
        # push in case local-only
        subprocess.run(["git", "push", "origin", tag], cwd=root, check=False)
        print(f"Tag {tag} already exists")

    body = f"""## SOR Public Archiver {tag}

Standalone **Windows** GUI package (no Python install required).

### Download
- **[{ASSET_NAME}](https://github.com/{OWNER}/{REPO}/releases/download/{tag}/{ASSET_NAME})** ({size_mb:.1f} MB)

### Install
1. Download and extract the zip
2. Open the `SOR-Public-Archiver` folder
3. Run `SOR-Public-Archiver.exe` (keep `_internal` beside the exe)
4. If the app fails to start, install [VC++ Redistributable 2015–2022 x64](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist)

### Highlights
- NSOPW **ethnicity match vs other surnames** tabs (both saved; primary view is list-matched)
- Short partial name queries (min first+last = 3 letters) with prefix collapse for speed
- Recent inserts: **column sort** + scroll isolation from the form
- **Max names** caps unique ethnicity-matched offenders (not HTTP report count)
- **AZ iCrimewatch CSV** fetch via `curl_cffi` Chrome TLS (bypasses HTTP 403 bot walls)
- Live NSOPW Recent inserts with **race** from detail sheets
- **Search all surnames**, **resume/skip completed searches**, **new HTML only**
- Automatic **disclaimer click-through** (iCrimeWatch / sheriffalerts)
- Granular **search vs report/HTML** rate limits
- Cloudflare-hardened NSOPW client (`curl_cffi`)

### Notes
- Runtime data (`data/`, downloads) is local-only and is **not** shipped in this package
- Respect registry terms of use and rate limits
"""

    # Create or get release
    release_url = f"https://api.github.com/repos/{OWNER}/{REPO}/releases"
    try:
        release = api_request(
            "POST",
            release_url,
            token,
            data=json.dumps(
                {
                    "tag_name": tag,
                    "name": f"SOR Public Archiver {tag}",
                    "body": body,
                    "draft": False,
                    "prerelease": False,
                    "generate_release_notes": True,
                }
            ).encode("utf-8"),
        )
        print(f"Created release: {release.get('html_url')}")
    except SystemExit as e:
        msg = str(e)
        if "already_exists" in msg or "422" in msg:
            print("Release may already exist; fetching…")
            releases = api_request("GET", release_url, token) or []
            release = next((r for r in releases if r.get("tag_name") == tag), None)
            if not release:
                raise
            print(f"Using existing release: {release.get('html_url')}")
        else:
            raise

    upload_url = (release.get("upload_url") or "").split("{", 1)[0]
    if not upload_url:
        raise SystemExit("No upload_url on release")

    # Delete existing asset with same name if re-publishing
    for asset in release.get("assets") or []:
        if asset.get("name") == ASSET_NAME:
            aid = asset["id"]
            print(f"Deleting existing asset id={aid}…")
            api_request(
                "DELETE",
                f"https://api.github.com/repos/{OWNER}/{REPO}/releases/assets/{aid}",
                token,
            )

    asset_bytes = zip_path.read_bytes()
    upload = (
        f"{upload_url}?name={ASSET_NAME}"
    )
    print(f"Uploading {ASSET_NAME} ({len(asset_bytes)} bytes)…")
    uploaded = api_request(
        "POST",
        upload,
        token,
        data=asset_bytes,
        content_type="application/octet-stream",
    )
    print("Upload complete.")
    print(f"Browser download: {uploaded.get('browser_download_url')}")
    print(f"Release page:     {release.get('html_url')}")


if __name__ == "__main__":
    main()
