"""App version and GitHub release checks."""

from __future__ import annotations

import json
import re
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass

APP_VERSION = "1.0.0"
APP_REVISION = 22

GITHUB_OWNER = "yourworstnightmare1"
GITHUB_REPO = "mi-led-gui"
GITHUB_URL = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}"
GITHUB_RELEASES_URL = f"{GITHUB_URL}/releases"
GITHUB_API_LATEST = (
    f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
)


@dataclass(frozen=True)
class UpdateInfo:
    current: str
    latest: str
    newer: bool
    release_url: str
    release_name: str
    body: str


def _parse_version(text: str) -> tuple[int, ...]:
    cleaned = (text or "").strip().lstrip("vV")
    parts = re.findall(r"\d+", cleaned)
    if not parts:
        return (0,)
    return tuple(int(p) for p in parts)


def is_newer(latest: str, current: str = APP_VERSION) -> bool:
    a = _parse_version(latest)
    b = _parse_version(current)
    n = max(len(a), len(b))
    a = a + (0,) * (n - len(a))
    b = b + (0,) * (n - len(b))
    return a > b


def _ssl_context() -> ssl.SSLContext:
    """Prefer certifi CAs — macOS Python builds often lack system certs."""
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def check_for_updates(timeout: float = 8.0) -> UpdateInfo:
    """Fetch the latest GitHub release and compare to this build."""
    req = urllib.request.Request(
        GITHUB_API_LATEST,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"mi-led-gui/{APP_VERSION}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return UpdateInfo(
                current=APP_VERSION,
                latest=APP_VERSION,
                newer=False,
                release_url=GITHUB_RELEASES_URL,
                release_name="No releases published",
                body="This repository has no GitHub releases yet.",
            )
        raise
    except Exception as exc:
        message = str(exc)
        if "CERTIFICATE_VERIFY_FAILED" in message or "SSL" in message.upper():
            raise RuntimeError(
                "SSL certificate verification failed. "
                "Install/update certifi (`python3 -m pip install -U certifi`), "
                "or open the releases page in your browser."
            ) from exc
        raise
    tag = str(payload.get("tag_name") or payload.get("name") or "").strip()
    if not tag:
        raise RuntimeError("GitHub latest release response had no tag_name")
    html_url = str(payload.get("html_url") or GITHUB_RELEASES_URL)
    name = str(payload.get("name") or tag)
    body = str(payload.get("body") or "").strip()
    return UpdateInfo(
        current=APP_VERSION,
        latest=tag,
        newer=is_newer(tag, APP_VERSION),
        release_url=html_url,
        release_name=name,
        body=body,
    )


def license_text() -> str:
    from pathlib import Path
    import sys

    candidates = [
        Path(__file__).resolve().parent / "assets" / "LICENSE.txt",
    ]
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        candidates.insert(0, Path(sys._MEIPASS) / "mi_led" / "assets" / "LICENSE.txt")
    for path in candidates:
        try:
            return path.read_text(encoding="utf-8")
        except Exception:
            continue
    return "MIT License — see the project repository for details.\n"
