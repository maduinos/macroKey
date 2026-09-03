"""What the project has published, read from the GitHub releases API.

One HTTPS GET against a public endpoint, no token, no third-party client. The
API answers with every asset the release workflow uploaded -- the two app
binaries, one firmware image per board, and `SHA256SUMS.txt` -- and everything
downloaded here is checked against that file before it is used for anything.

Nothing in this module is allowed to be load-bearing. The network is optional:
the app ships firmware inside itself and works with no internet at all, so a
failure here is logged and swallowed by the caller rather than shown as a
problem with the keypad.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .. import __version__

log = logging.getLogger(__name__)

#: Where releases come from. Overridable so the tests can point at a local
#: file:// tree instead of the internet, and so a fork can point at itself.
DEFAULT_REPO = "maduinos/macroKey"
API_ROOT = "https://api.github.com"

#: Short on purpose. This runs while someone is plugging a keypad in, and a
#: stalled connection must not be the reason the app feels stuck.
TIMEOUT = 10.0
#: Downloads are megabytes rather than kilobytes, so they get their own.
DOWNLOAD_TIMEOUT = 120.0

USER_AGENT = f"macroKey/{__version__}"

#: The asset the workflow writes every other asset's hash into.
CHECKSUMS_ASSET = "SHA256SUMS.txt"

Status = Callable[[str], None]


class UpdateError(RuntimeError):
    """Looking for, or fetching, an update failed."""


def repo() -> str:
    return os.environ.get("MACROKEY_UPDATE_REPO", "").strip() or DEFAULT_REPO


def enabled() -> bool:
    """False when this build must never reach the network by itself.

    `MACROKEY_NO_UPDATE=1` is for CI, for packagers whose distribution owns
    updating, and for anyone who would rather the app not phone home.
    """
    return os.environ.get("MACROKEY_NO_UPDATE", "").strip() not in ("1", "true", "yes")


def _version_tuple(version: str) -> tuple[int, ...]:
    """`"v0.11.1"` -> `(0, 11, 1)`. Trailing junk is dropped, not guessed at."""
    cleaned = version.strip().lstrip("vV").split("+")[0].split("-")[0]
    parts: list[int] = []
    for piece in cleaned.split("."):
        digits = "".join(_leading_digits(piece))
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def _leading_digits(text: str) -> list[str]:
    """The digits at the front of `text`, stopping at the first thing that is
    not one -- so a "1rc2" component reads as 1 rather than raising."""
    kept: list[str] = []
    for character in text:
        if not character.isdigit():
            break
        kept.append(character)
    return kept


def is_newer(candidate: str, current: str) -> bool:
    """Whether `candidate` is a later version than `current`.

    Unparseable on either side means "no". An update is a thing that overwrites
    what someone is running, so anything not clearly newer is left alone.
    """
    left, right = _version_tuple(candidate), _version_tuple(current)
    if not left or not right:
        return False
    # Compared at equal length: 0.11 is not newer than 0.11.1.
    width = max(len(left), len(right))
    left += (0,) * (width - len(left))
    right += (0,) * (width - len(right))
    return left > right


@dataclass(frozen=True)
class Release:
    """One published release, and the files hanging off it."""

    tag: str
    #: The tag with any `v` taken off, which is what versions are compared as.
    version: str
    notes: str
    prerelease: bool
    #: Asset filename -> download URL.
    assets: dict[str, str]

    def asset(self, name: str) -> str | None:
        return self.assets.get(name)

    @classmethod
    def from_json(cls, data: dict) -> Release:
        tag = str(data.get("tag_name") or "")
        return cls(
            tag=tag,
            version=tag.lstrip("vV"),
            notes=str(data.get("body") or ""),
            prerelease=bool(data.get("prerelease")),
            assets={
                str(item["name"]): str(item["browser_download_url"])
                for item in data.get("assets") or []
                # Shape-checked rather than trusted. The repository is settable
                # (`MACROKEY_UPDATE_REPO`), so "the API always answers like
                # this" is an assumption this does not need to make.
                if isinstance(item, dict)
                and item.get("name")
                and item.get("browser_download_url")
            },
        )


def _open(url: str, *, timeout: float):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        return urllib.request.urlopen(request, timeout=timeout)  # noqa: S310 - https, fixed host
    except urllib.error.HTTPError as exc:
        raise UpdateError(f"{url} answered {exc.code} {exc.reason}") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise UpdateError(f"could not reach {url}: {exc}") from exc


def latest_release() -> Release:
    """The newest published release, prereleases excluded.

    `/releases/latest` is what excludes them, and that matters here: the
    workflow keeps a rolling `latest` prerelease for every push to main, and
    nobody's keypad should be reflashed with a build of a work in progress.
    """
    if not enabled():
        raise UpdateError("updates are switched off (MACROKEY_NO_UPDATE)")
    url = f"{API_ROOT}/repos/{repo()}/releases/latest"
    with _open(url, timeout=TIMEOUT) as response:
        try:
            data = json.loads(response.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise UpdateError(f"{url} did not answer with JSON") from exc
    if not isinstance(data, dict) or not data.get("tag_name"):
        raise UpdateError(f"{url} answered without a release")
    release = Release.from_json(data)
    log.debug("latest release is %s with %d assets", release.tag, len(release.assets))
    return release


def checksums(release: Release) -> dict[str, str]:
    """`filename -> sha256`, from the release's own checksum file.

    Empty when the release has none. An older release predates the file, and a
    missing hash is reported by `download` rather than being invented here.
    """
    url = release.asset(CHECKSUMS_ASSET)
    if url is None:
        return {}
    with _open(url, timeout=TIMEOUT) as response:
        text = response.read().decode("utf-8", "replace")
    found: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and len(parts[0]) == 64:
            # `sha256sum` writes "hash  name", with a `*` before a binary name.
            found[parts[1].lstrip("*")] = parts[0].lower()
    return found


def download(
    url: str,
    target: Path,
    *,
    sha256: str | None = None,
    status: Status = lambda _message: None,
    timeout: float = DOWNLOAD_TIMEOUT,
) -> Path:
    """Fetches `url` to `target`, verifying it before it is given that name.

    Written next door and renamed into place, so an interrupted download cannot
    leave something that looks like a firmware image and is half of one. A hash
    mismatch deletes the file and raises: this ends in either the bytes the
    release published or nothing at all.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f".{target.name}.part")
    digest = hashlib.sha256()
    status(f"Downloading {target.name}")
    try:
        with _open(url, timeout=timeout) as response, partial.open("wb") as handle:
            while True:
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                handle.write(chunk)
    except UpdateError:
        partial.unlink(missing_ok=True)
        raise
    except OSError as exc:
        partial.unlink(missing_ok=True)
        raise UpdateError(f"could not write {target}: {exc}") from exc

    if sha256 is not None and digest.hexdigest() != sha256.lower():
        partial.unlink(missing_ok=True)
        raise UpdateError(
            f"{target.name} does not match the published checksum -- it was not kept"
        )
    try:
        partial.replace(target)
    except OSError as exc:
        # The one file operation that was outside the error contract. It fails
        # for ordinary reasons -- a read-only directory, and on Windows a name
        # that is open -- and a bare OSError from here reached the CLI as a
        # traceback instead of a sentence.
        partial.unlink(missing_ok=True)
        raise UpdateError(f"could not put {target} in place: {exc}") from exc
    return target


def fetch_asset(
    release: Release,
    name: str,
    into: Path,
    *,
    status: Status = lambda _message: None,
    require_checksum: bool = True,
) -> Path:
    """Downloads one named asset of `release` into the directory `into`.

    `require_checksum` is the safety catch, and it is on: an asset the release
    publishes no hash for is refused rather than trusted, because the whole
    reason to check is that this file is about to be written to a keypad or
    over the running program.
    """
    url = release.asset(name)
    if url is None:
        raise UpdateError(f"release {release.tag} has no asset called {name}")
    published = checksums(release)
    expected = published.get(name)
    if expected is None and require_checksum:
        raise UpdateError(
            f"release {release.tag} publishes no checksum for {name} -- refusing it"
        )
    return download(url, into / name, sha256=expected, status=status)
