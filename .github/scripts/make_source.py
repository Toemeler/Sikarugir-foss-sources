#!/usr/bin/env python3
"""Emit the SideStore/AltStore source feed and a small landing page.

Build facts come from the environment, so the workflow stays the single source
of truth for version, size, bundle identifier and download URL.
"""
import datetime as dt
import hashlib
import json
import os
import plistlib
import re
import sys
import zipfile

NAME = "Madeira"
SUBTITLE = "Windows PC games on iPhone and iPad"
TINT = "2E7D6B"
DESCRIPTION = (
    "Runs Windows PC games on iPhone and iPad without a jailbreak: FEX-Emu "
    "translates x86 code, Wine provides Windows, DXMT and Madeira's own "
    "Direct3D 12 layer draw with Metal. Sign in to Steam to install, update "
    "and start your games through Valve's own Windows client, with Steam "
    "Cloud saves, controllers, keyboard, mouse and trackpad.\n\n"
    "Needs iOS 26 or later and JIT (StikDebug, or the built-in helper; on "
    "iOS 27 it can pair the device itself). Automatic build of "
    "github.com/willfaust/Madeira."
)


def main(out_dir):
    repo = os.environ["GITHUB_REPOSITORY"]
    owner, name = repo.split("/")
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    version = os.environ["VERSION"]
    tag = os.environ["TAG"]
    size = int(os.environ["SIZE"])
    sha256 = os.environ["SHA256"]
    bundle_id = os.environ["BUNDLE_ID"]
    min_os = os.environ.get("MIN_OS", "26.0")

    # Pages serves the default branch, and that is where the feed is committed,
    # so every asset URL hangs off it.
    branch = os.environ.get("DEFAULT_BRANCH") or "main"
    pages_site = os.environ.get("PAGES_URL", "").strip().rstrip("/")
    pages_base = pages_site or f"https://{owner.lower()}.github.io/{name}"
    raw_base = f"https://raw.githubusercontent.com/{repo}/{branch}"
    # Assets go over raw.githubusercontent.com so the feed works before GitHub
    # Pages is switched on for the repository.
    icon_url = f"{raw_base}/icon.png"
    download_url = f"{server}/{repo}/releases/download/{tag}/{NAME}.ipa"
    date = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    version_entry = {
        "version": version,
        "buildVersion": version.rsplit(".", 1)[-1],
        "date": date,
        "localizedDescription": f"Automatic build {tag}.",
        "downloadURL": download_url,
        "size": size,
        "sha256": sha256,
        "minOSVersion": min_os,
    }

    app = {
        "name": NAME,
        "bundleIdentifier": bundle_id,
        "developerName": owner,
        "subtitle": SUBTITLE,
        "localizedDescription": DESCRIPTION,
        "iconURL": icon_url,
        "tintColor": TINT,
        "category": "games",
        "screenshots": [],
        "versions": [version_entry],
        # Legacy top-level keys, for clients predating the versions array.
        "version": version,
        "versionDate": date,
        "versionDescription": f"Automatic build {tag}.",
        "downloadURL": download_url,
        "size": size,
    }

    apps = [app]
    stik = stikdebug_app(out_dir, raw_base)
    if stik:
        apps.append(stik)

    source = {
        "name": NAME,
        "identifier": f"io.github.{owner.lower()}.{name.lower()}",
        "subtitle": SUBTITLE,
        "description": DESCRIPTION,
        "iconURL": icon_url,
        "website": f"{server}/{repo}",
        "tintColor": TINT,
        "apps": apps,
        "news": [],
    }

    os.makedirs(out_dir, exist_ok=True)
    payload = json.dumps(source, indent=2, ensure_ascii=False)
    # s.json is the short path people type; apps.json is the conventional name.
    for filename in ("s.json", "apps.json"):
        with open(os.path.join(out_dir, filename), "w", encoding="utf-8") as fh:
            fh.write(payload + "\n")

    raw_url = f"{raw_base}/s.json"
    source_url = f"{pages_base}/s.json" if pages_site else raw_url
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(LANDING.format(
            source_url=f"{pages_base}/s.json",
            raw_url=raw_url,
            version=version,
            repo=repo,
            server=server,
        ))

    print(f"source URL: {source_url}")
    print(f"raw URL:    {raw_url}")
    print("Pages: " + (pages_site or "not enabled - the release notes use the raw URL"))

    # Single source of truth for the URL: the release notes read it back here
    # rather than rebuilding it from the repository name a second time.
    step_output = os.environ.get("GITHUB_OUTPUT")
    if step_output:
        with open(step_output, "a") as fh:
            fh.write(f"source_url={source_url}\n")
            fh.write(f"raw_url={raw_url}\n")
            fh.write(f"pages_url={pages_site}\n")


def stikdebug_app(out_dir, raw_base):
    """StikDebug's newest release, when the workflow downloaded its IPA.

    Name, bundle identifier, version and icon are read from the IPA itself, so
    the entry always matches what the download installs.
    """
    path = os.environ.get("STIK_IPA")
    if not path or not os.path.isfile(path):
        return None
    with zipfile.ZipFile(path) as ipa:
        names = ipa.namelist()
        plist_name = next((n for n in names
                           if re.fullmatch(r"Payload/[^/]+\.app/Info\.plist", n)), None)
        if not plist_name:
            print("StikDebug IPA has no app Info.plist; not listed")
            return None
        info = plistlib.loads(ipa.read(plist_name))
        app_dir = plist_name[: -len("Info.plist")]
        # The largest top-level AppIcon PNG in the bundle is the best icon.
        icons = [n for n in names if n.startswith(app_dir + "AppIcon")
                 and n.endswith(".png") and "/" not in n[len(app_dir):]]
        icon_url = None
        if icons:
            best = max(icons, key=lambda n: ipa.getinfo(n).file_size)
            with open(os.path.join(out_dir, "stikdebug-icon.png"), "wb") as fh:
                fh.write(ipa.read(best))
            icon_url = f"{raw_base}/stikdebug-icon.png"
    with open(path, "rb") as fh:
        data = fh.read()

    name = info.get("CFBundleDisplayName") or info.get("CFBundleName") or "StikDebug"
    version = info.get("CFBundleShortVersionString") or os.environ.get("STIK_TAG", "1.0")
    build = str(info.get("CFBundleVersion") or version)
    date = os.environ.get("STIK_DATE") or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    download_url = os.environ["STIK_URL"]
    description = (
        f"Enables JIT for Madeira. Release {os.environ.get('STIK_TAG', version)} "
        "of github.com/StikDebug/StikDebug, listed here unmodified."
    )
    entry = {
        "version": version,
        "buildVersion": build,
        "date": date,
        "localizedDescription": description,
        "downloadURL": download_url,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    if info.get("MinimumOSVersion"):
        entry["minOSVersion"] = info["MinimumOSVersion"]
    app = {
        "name": name,
        "bundleIdentifier": info["CFBundleIdentifier"],
        "developerName": "StikDebug",
        "subtitle": "JIT for Madeira",
        "localizedDescription": description,
        "tintColor": TINT,
        "category": "utilities",
        "screenshots": [],
        "versions": [entry],
        "version": version,
        "versionDate": date,
        "versionDescription": description,
        "downloadURL": download_url,
        "size": len(data),
    }
    if icon_url:
        app["iconURL"] = icon_url
    print(f"StikDebug {version} ({info['CFBundleIdentifier']}) listed")
    return app


LANDING = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Madeira source</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ font: 16px/1.6 -apple-system, system-ui, sans-serif; max-width: 34rem;
         margin: 0 auto; padding: 3rem 1.25rem; background: #0B1714; color: #fff; }}
  code {{ background: rgba(255,255,255,.12); padding: .15em .4em; border-radius: .3em;
          word-break: break-all; }}
  a.btn {{ display: inline-block; background: #3FB79A; color: #0B1714; text-decoration: none;
           padding: .7em 1.2em; border-radius: .6em; font-weight: 700; }}
  a {{ color: #3FB79A; }}
</style>
</head>
<body>
<h1>Madeira</h1>
<p>SideStore/AltStore source for unsigned Madeira builds. Current version
   <strong>{version}</strong>.</p>
<p><a class="btn" href="sidestore://source?url={source_url}">Add to SideStore</a></p>
<p>Or paste this URL into SideStore &rarr; Sources &rarr; +:</p>
<p><code>{source_url}</code></p>
<p>Works without GitHub Pages too:</p>
<p><code>{raw_url}</code></p>
<p><a href="{server}/{repo}">Source and releases</a></p>
</body>
</html>
"""


if __name__ == "__main__":
    main(sys.argv[1])
