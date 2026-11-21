"""Updates: the newest ctgedit release on GitHub, and its installer downloaded and checked
against the SHA-256 digest GitHub lists for it."""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional

from .. import __version__

REPO = "MSkjel/ctgedit"
LATEST_URL = "https://api.github.com/repos/%s/releases/latest" % REPO
RELEASES_PAGE = "https://github.com/%s/releases" % REPO
_CHUNK = 1 << 16


class UpdateError(Exception):
    """A check or download that failed; the text is shown to the user."""


@dataclass(frozen=True)
class Release:
    version: str
    page: str
    notes: str
    asset_name: str
    asset_url: str
    size: int
    sha256: str


def version_key(text: str) -> Optional[tuple]:
    """``'v1.2.3'`` / ``'1.2.3'`` -> ``(1, 2, 3)``; None when it is not a plain version."""
    m = re.fullmatch(r"v?(\d+(?:\.\d+){0,3})", (text or "").strip())
    return tuple(int(p) for p in m.group(1).split(".")) if m else None


def is_newer(candidate: str, current: str = __version__) -> bool:
    a, b = version_key(candidate), version_key(current)
    if a is None or b is None:
        return False
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


def installer_name(version: str) -> str:
    return "ctgedit-%s-setup.exe" % version


def parse_release(data: dict) -> Release:
    """The release GitHub's API describes in ``data``, with its installer."""
    tag = str(data.get("tag_name") or "")
    if version_key(tag) is None:
        raise UpdateError("The latest release has no version number (%s)." % (tag or "no tag"))
    version = tag[1:] if tag.startswith("v") else tag
    name = installer_name(version)
    asset = next((a for a in data.get("assets") or () if a.get("name") == name), None)
    if asset is None:
        raise UpdateError("ctgedit %s has no installer to download yet." % version)
    digest = str(asset.get("digest") or "")
    if not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
        raise UpdateError("The installer of ctgedit %s has no checksum, so it is not installed." % version)
    url = str(asset.get("browser_download_url") or "")
    if not url.startswith("https://"):
        raise UpdateError("The installer of ctgedit %s has no secure download link." % version)
    return Release(
        version=version,
        page=str(data.get("html_url") or RELEASES_PAGE),
        notes=str(data.get("body") or ""),
        asset_name=name,
        asset_url=url,
        size=int(asset.get("size") or 0),
        sha256=digest[7:].lower(),
    )


def _open(url: str, timeout: float, accept: str):
    req = urllib.request.Request(url, headers={"User-Agent": "ctgedit/" + __version__, "Accept": accept})
    return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 -- https only (callers)


def latest_release(timeout: float = 15.0) -> Release:
    try:
        with _open(LATEST_URL, timeout, "application/vnd.github+json") as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise UpdateError("No release of ctgedit has been published yet.") from e
        raise UpdateError("GitHub answered with error %d. Try again later." % e.code) from e
    except (urllib.error.URLError, OSError) as e:
        raise UpdateError("Could not reach GitHub. Check the internet connection.") from e
    except ValueError as e:
        raise UpdateError("GitHub's answer could not be read.") from e
    if not isinstance(data, dict):
        raise UpdateError("GitHub's answer could not be read.")
    return parse_release(data)


def download(
    release: Release,
    folder: str,
    progress: Optional[Callable[[int, int], None]] = None,
    cancelled: Optional[Callable[[], bool]] = None,
    timeout: float = 30.0,
) -> Optional[str]:
    """Download the installer into ``folder``; its path once its size and SHA-256 match the
    release's, None when ``cancelled`` said so. ``progress(done, total)`` after each chunk."""
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, release.asset_name)
    part = path + ".part"
    digest = hashlib.sha256()
    done = 0
    try:
        with _open(release.asset_url, timeout, "application/octet-stream") as resp, open(part, "wb") as out:
            while True:
                if cancelled is not None and cancelled():
                    break
                chunk = resp.read(_CHUNK)
                if not chunk:
                    break
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if progress is not None:
                    progress(done, release.size)
    except (urllib.error.URLError, OSError) as e:
        _remove(part)
        raise UpdateError("The download failed. Check the internet connection and try again.") from e
    if cancelled is not None and cancelled():
        _remove(part)
        return None
    if done != release.size or digest.hexdigest() != release.sha256:
        _remove(part)
        raise UpdateError("The downloaded installer does not match its checksum, so it is not used.")
    try:
        os.replace(part, path)
    except OSError as e:
        _remove(part)
        raise UpdateError("The installer could not be saved in %s." % folder) from e
    return path


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
