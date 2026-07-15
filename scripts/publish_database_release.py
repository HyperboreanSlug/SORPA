#!/usr/bin/env python3
"""
Prepare scrubbed public DB (base + deltas + mugshots) and publish to GitHub Releases.

Upload is gated to THIS local publisher instance only (data/db_publish.allow).
Other machines / app installs can only download.

Usage (from repo root)::

    python scripts/enable_db_publish.py   # once on publisher machine
    gh auth login
    python scripts/publish_database_release.py --use-gh
    python scripts/publish_database_release.py --use-gh --full-base
    python scripts/publish_database_release.py --use-gh --skip-photos

Default publishes a small delta when possible; use --full-base after large rebuilds.
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

REPO = "HyperboreanSlug/SORPA"
TAG = "database-latest"
PHOTO_GLOB = "offenders.photos.*.zip"
DELTA_GLOB = "offenders.delta.*.zip"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--use-gh", action="store_true", help="Use gh CLI to upload")
    ap.add_argument("--skip-scrub", action="store_true", help="Reuse existing release files")
    ap.add_argument("--full-base", action="store_true", help="Force full base zip")
    ap.add_argument(
        "--skip-photos",
        action="store_true",
        help="Do not repack mugshots (reuse prior photo assets)",
    )
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--tag", default=TAG)
    args = ap.parse_args()

    os.chdir(ROOT)

    from scraper.db_publish_gate import require_publish_allowed

    require_publish_allowed(ROOT)

    zip_path = ROOT / "releases" / "offenders.db.zip"
    man_path = ROOT / "releases" / "MANIFEST.json"

    if not args.skip_scrub:
        scrub = ROOT / "scripts" / "scrub_db_for_release.py"
        cmd = [sys.executable, str(scrub)]
        if args.full_base:
            cmd.append("--full-base")
        if args.skip_photos:
            cmd.append("--skip-photos")
        print("Scrubbing + packaging DB (base or delta) + photos…")
        rc = subprocess.call(cmd)
        if rc != 0:
            return rc

    if not man_path.is_file():
        print("Missing releases/MANIFEST.json")
        return 1

    man = json.loads(man_path.read_text(encoding="utf-8"))
    photo_paths = sorted((ROOT / "releases").glob(PHOTO_GLOB))
    delta_paths = sorted((ROOT / "releases").glob(DELTA_GLOB))

    print(
        f"MANIFEST records={man.get('record_count')} "
        f"format={man.get('format')} base={str(man.get('sha256') or '')[:16]}… "
        f"deltas={len(man.get('deltas') or [])}"
    )
    if zip_path.is_file():
        print(f"DB zip: {zip_path.stat().st_size:,} bytes")
    for p in delta_paths:
        print(f"  delta {p.name}: {p.stat().st_size:,} bytes")
    for p in photo_paths:
        print(f"  photo {p.name}: {p.stat().st_size:,} bytes")

    assets = [man_path]
    if zip_path.is_file():
        assets.insert(0, zip_path)
    assets.extend(delta_paths)
    if not args.skip_photos:
        assets.extend(photo_paths)
    else:
        # Still upload photo assets that exist if first publish
        assets.extend(photo_paths)

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    if not token:
        token = _git_cred_token()
    if args.use_gh:
        gh = _which("gh")
        if gh:
            return _publish_gh(args.repo, args.tag, assets, man)
        print("gh CLI not found — falling back to API upload with git credentials.")
    if not token:
        names = " ".join(str(a) for a in assets)
        print(
            "No GITHUB_TOKEN/GH_TOKEN, git credential, or gh CLI.\n"
            "Assets ready under releases/ — upload manually:\n"
            f"  gh release create {args.tag} {names} --repo {args.repo} "
            f'--title "Public database" --notes "Public registry archive"\n'
        )
        return 2

    return _publish_api(args.repo, args.tag, assets, token, man)


def _which(cmd: str) -> str:
    from shutil import which

    return which(cmd) or ""


def _git_cred_token() -> str:
    """Password/token from git credential helper (same as git push)."""
    try:
        p = subprocess.run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost=github.com\n\n",
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except Exception:
        return ""
    if p.returncode != 0:
        return ""
    for line in (p.stdout or "").splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1].strip()
    return ""


def _notes(man: dict) -> str:
    photos = man.get("photo_file_count") or 0
    parts = man.get("photo_part_count") or 0
    deltas = man.get("deltas") or []
    return (
        "Public U.S. sex offender registry SQLite archive for SOR Public Archiver.\n\n"
        f"- Records: {man.get('record_count')}\n"
        f"- Format: {man.get('format', 1)} (base + {len(deltas)} delta pack(s))\n"
        f"- Mugshots: {photos} files in {parts} zip part(s)\n"
        "- Clients apply `offenders.delta.NNNN.zip` after the base for small updates\n"
        "- Paths are project-relative under `data/report_pages/*/photos/`\n"
    )


def _publish_gh(repo: str, tag: str, assets: list[Path], man: dict) -> int:
    """Create/update release; upload assets with --clobber (no full delete)."""
    notes = _notes(man)
    # Ensure release exists
    check = subprocess.call(
        ["gh", "release", "view", tag, "--repo", repo],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if check != 0:
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
    else:
        # Refresh notes
        subprocess.call(
            ["gh", "release", "edit", tag, "--repo", repo, "--notes", notes],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    for path in assets:
        if not path.is_file():
            continue
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
    asset_names = {a.name for a in assets if a.is_file()}

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
            return resp.status, resp.read()

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
    # Delete only assets we are replacing (same name)
    for asset in rel.get("assets") or []:
        name = asset.get("name") or ""
        if name in asset_names:
            aid = asset.get("id")
            try:
                req("DELETE", f"{api}/repos/{repo}/releases/assets/{aid}")
                print("Deleted old asset", name)
            except Exception as e:
                print("Could not delete asset", e)

    for path in assets:
        if not path.is_file():
            continue
        url = f"{upload_url}?name={path.name}"
        size = path.stat().st_size
        print(f"Uploading {path.name} ({size:,} bytes)…")
        try:
            # Stream via curl when available (multi-GB photo parts)
            curl = _which("curl")
            if curl and size > 50 * 1024 * 1024:
                cmd = [
                    curl,
                    "-sS",
                    "-X",
                    "POST",
                    "-H",
                    f"Authorization: Bearer {token}",
                    "-H",
                    "Accept: application/vnd.github+json",
                    "-H",
                    "Content-Type: application/octet-stream",
                    "-H",
                    "User-Agent: SOR-Public-Archiver-Publish",
                    "--data-binary",
                    f"@{path}",
                    url,
                ]
                cp = subprocess.run(cmd, capture_output=True, timeout=7200)
                if cp.returncode != 0:
                    err = (cp.stderr or b"").decode("utf-8", errors="replace")[:400]
                    print(f"  FAIL curl rc={cp.returncode} {err}")
                    return 1
                print(f"  OK curl ({path.name})")
                continue
            # Smaller files: urllib (still avoid full RAM for mid-size if possible)
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
                f"  FAIL: {path.name} too large for in-memory upload and curl missing."
            )
            return 1
        except Exception as e:
            print(f"  FAIL {e}")
            return 1
    print("Done.")
    print(
        f"Download: https://github.com/{repo}/releases/download/{tag}/"
        f"{(assets[0].name if assets else 'MANIFEST.json')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
