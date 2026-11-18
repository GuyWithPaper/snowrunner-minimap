"""Auto-update from GitHub Releases.

Publishing a new version = creating a GitHub release tagged vX.Y.Z with MapRunner-Setup.exe attached
(release.ps1 does this). Installed copies check on start and every few hours, download the new setup,
run it silently, and the installer restarts the app.
"""
import json, os, subprocess, sys, tempfile, threading, time, urllib.request

from version import VERSION, REPO

API = f"https://api.github.com/repos/{REPO}/releases/latest"
UA = {"User-Agent": "MapRunner-updater", "Accept": "application/vnd.github+json"}


def _ver(s):
    s = s.strip().lstrip("vV")
    out = []
    for p in s.split("."):
        n = "".join(ch for ch in p if ch.isdigit())
        out.append(int(n) if n else 0)
    return tuple(out + [0] * (3 - len(out)))


def latest():
    req = urllib.request.Request(API, headers=UA)
    with urllib.request.urlopen(req, timeout=15) as r:
        rel = json.load(r)
    asset = next((a for a in rel.get("assets", []) if a["name"].lower().endswith("setup.exe")), None)
    return rel.get("tag_name", "0"), asset


def check(log=print):
    """-> (tag, download_url) if a newer release exists, else None."""
    try:
        tag, asset = latest()
    except Exception as e:
        log(f"update check failed: {e}")
        return None
    if asset and _ver(tag) > _ver(VERSION):
        return tag, asset["browser_download_url"]
    return None


def install(url, log=print):
    """Download the setup and run it silently; caller should quit right after."""
    dst = os.path.join(tempfile.gettempdir(), "MapRunner-Setup.exe")
    req = urllib.request.Request(url, headers={"User-Agent": UA["User-Agent"]})
    with urllib.request.urlopen(req, timeout=120) as r, open(dst, "wb") as f:
        while True:
            b = r.read(1 << 16)
            if not b:
                break
            f.write(b)
    log(f"downloaded update to {dst}")
    subprocess.Popen([dst, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", "/RESTARTAPPLICATIONS"],
                     close_fds=True, creationflags=0x00000008)  # DETACHED_PROCESS
    return True


def can_self_update():
    # only the installed (frozen) build updates itself; running from source never does
    return getattr(sys, "frozen", False)
