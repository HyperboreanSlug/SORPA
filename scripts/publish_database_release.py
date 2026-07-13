#!/usr/bin/env python3
"""
Prepare a scrubbed public offenders.db.zip (+ mugshot parts) and publish
to GitHub Releases.

Usage (from repo root)::

    gh auth login
    python scripts/publish_database_release.py --use-gh

    set GITHUB_TOKEN=ghp_...
    python scripts/publish_database_release.py

The script:
  1. Scrubs data/offenders.db (project-relative paths only)
  2. Writes releases/offenders.db.zip
  3. Packs referenced mugshots into releases/offenders.photos.NNN.zip
  4. Writes releases/MANIFEST.json
  5. Creates/updates release tag database-latest with those assets
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REPO = "HyperboreanSlug/sor-public-archiver"
TAG = "database-latest"
PHOTO_GLOB = "offenders.photos.*.zip"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--use-gh", action="store_true", help="Use gh CLI to upload")
    ap.add_argument("--skip-scrub", action="store_true", help="Reuse existing zip/parts")
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--tag", default=TAG)
    args = ap.parse_args()

    os.chdir(ROOT)
    zip_path = ROOT / "releases" / "offenders.db.zip"
    man_path = ROOT / "releases" / "MANIFEST.json"
    photo_paths = sorted((ROOT / "releases").glob(PHOTO_GLOB))

    if not args.skip_scrub:
        scrub = ROOT / "scripts" / "scrub_db_for_release.py"
        print("Scrubbing + zipping DB + photos…")
        rc = subprocess.call([sys.executable, str(scrub)])
        if rc != 0:
            return rc
        photo_paths = sorted((ROOT / "releases").glob(PHOTO_GLOB))

    if not zip_path.is_file() or not man_path.is_file():
        print("Missing releases/offenders.db.zip or MANIFEST.json")
        return 1

    man = json.loads(man_path.read_text(encoding="utf-8"))
    print(
        f"DB zip: {zip_path.stat().st_size:,} bytes, "
        f"records={man.get('record_count')}, sha={str(man.get('sha256'))[:16]}…"
    )
    print(
        f"Photo parts: {len(photo_paths)} "
        f"(manifest files={man.get('photo_file_count')}, "
        f"bytes={man.get('photo_size_bytes')})"
    )
    for p in photo_paths:
        print(f"  {p.name}: {p.stat().st_size:,} bytes")

    assets = [zip_path, man_path, *photo_paths]
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    if args.use_gh or not token:
        gh = shutil_which("gh")
        if gh:
            return _publish_gh(args.repo, args.tag, assets, man)
        if not token:
            names = " ".join(str(a) for a in assets)
            print(
                "No GITHUB_TOKEN/GH_TOKEN and gh CLI not found.\n"
                "Assets ready under releases/ — upload manually:\n"
                f"  gh release create {args.tag} {names} --repo {args.repo} "
                f'--title "Public database" --notes "Public registry archive"\n'
                "Or re-run with GITHUB_TOKEN set / --use-gh."
            )
            return 2

    return _publish_api(args.repo, args.tag, assets, token, man)


def shutil_which(cmd: str) -> str:
    from shutil import which

    return which(cmd) or ""


def _notes(man: dict) -> str:
    photos = man.get("photo_file_count") or 0
    parts = man.get("photo_part_count") or 0
    return (
        "Public U.S. sex offender registry SQLite archive for SOR Public Archiver.\n\n"
        f"- Records: {man.get('record_count')}\n"
        f"- Mugshots: {photos} files in {parts} zip part(s) "
        f"(``offenders.photos.NNN.zip``)\n"
        "- Paths are project-relative under ``data/report_pages/*/photos/``\n"
        "- No local user-profile paths\n"
    )


def _publish_gh(repo: str, tag: str, assets: list[Path], man: dict) -> int:
    """Create release then upload assets one-by-one (large photo zips hang on create)."""
    notes = _notes(man)
    # Clean previous release/tag (best-effort)
    subprocess.call(
        ["gh", "release", "delete", tag, "--repo", repo, "--yes"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.call(
        ["git", "push", "origin", f":refs/tags/{tag}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Create empty release first (no huge files on the create argv)
    create = [
        "gh",
        "release",
        "create",
        tag,
        "--repo",
        repo,
        "--title",
        "Public database archive",
        "--notes",
        notes,
    ]
    print("Creating release…")
    rc = subprocess.call(create)
    if rc != 0:
        print("gh release create failed", rc)
        return rc

    for path in assets:
        cmd = [
            "gh",
            "release",
            "upload",
            tag,
            str(path),
            "--repo",
            repo,
            "--clobber",
        ]
        print(f"Uploading {path.name} ({path.stat().st_size:,} bytes)…")
        # Unbuffered progress from gh when possible
        env = os.environ.copy()
        env.setdefault("GH_PROMPT_DISABLED", "1")
        rc = subprocess.call(cmd, env=env)
        if rc != 0:
            print(f"Upload failed for {path.name} rc={rc}")
            return rc
        print(f"  OK {path.name}")
    print("Done.")
    print(f"https://github.com/{repo}/releases/tag/{tag}")
    return 0


def _publish_api(
    repo: str, tag: str, assets: list[Path], token: str, man: dict
) -> int:
    import json as _json
    import urllib.error
    import urllib.request

    api = "https://api.github.com"
    asset_names = {a.name for a in assets}

    def req(
        method: str,
        url: str,
        data: bytes | None = None,
        content_type: str = "",
        timeout: int = 600,
    ):
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "SOR-Public-Archiver-Publish",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if content_type:
            headers["Content-Type"] = content_type
        r = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            body = resp.read()
            return resp.status, body

    try:
        status, body = req("GET", f"{api}/repos/{repo}/releases/tags/{tag}")
        rel = _json.loads(body.decode())
        print(f"Updating existing release id={rel.get('id')}")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            print("GET release failed", e)
            return 1
        payload = _json.dumps(
            {
                "tag_name": tag,
                "name": "Public database archive",
                "body": _notes(man),
                "draft": False,
                "prerelease": False,
            }
        ).encode()
        status, body = req(
            "POST",
            f"{api}/repos/{repo}/releases",
            data=payload,
            content_type="application/json",
        )
        rel = _json.loads(body.decode())
        print(f"Created release id={rel.get('id')}")

    upload_url = (rel.get("upload_url") or "").split("{")[0]
    for asset in rel.get("assets") or []:
        name = asset.get("name") or ""
        if name in asset_names or name.startswith("offenders.photos."):
            aid = asset.get("id")
            try:
                req("DELETE", f"{api}/repos/{repo}/releases/assets/{aid}")
                print("Deleted old asset", name)
            except Exception as e:
                print("Could not delete asset", e)

    for path in assets:
        url = f"{upload_url}?name={path.name}"
        print(f"Uploading {path.name} ({path.stat().st_size:,} bytes)…")
        # Stream file to avoid loading multi-GB into RAM
        try:
            with open(path, "rb") as f:
                data = f.read()
            status, body = req(
                "POST",
                url,
                data=data,
                content_type="application/octet-stream",
                timeout=3600,
            )
            print(f"  OK HTTP {status}")
        except urllib.error.HTTPError as e:
            print(f"  FAIL {e.code} {e.read()[:500]}")
            return 1
        except MemoryError:
            print(
                f"  FAIL: {path.name} too large for API upload in-memory. "
                "Re-run with --use-gh."
            )
            return 1
    print("Done.")
    print(
        f"Download: https://github.com/{repo}/releases/download/{tag}/{assets[0].name}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
