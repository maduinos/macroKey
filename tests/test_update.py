"""Updating: what may be written over what, and what must never be.

Two things here can destroy something that works -- a firmware image written to
a keypad, and a binary written over the running app -- and both arrive from the
network. So the tests are mostly about refusing: a file whose hash does not
match the release is not kept, an asset the release publishes no hash for is
not fetched at all, and nothing at all is written while a version cannot be
read out of the image itself.

Nothing here talks to GitHub. `latest_release` is stubbed, and the assets are
served from a temporary directory over `file://`.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

from macrokey.boards import DEFAULT_BOARD, board_by_id
from macrokey.flash.images import find_image, image_version
from macrokey.update import firmware, releases, selfupdate
from macrokey.update.releases import Release, UpdateError


@pytest.fixture(autouse=True)
def _updates_are_allowed(monkeypatch):
    """conftest switches updating off for the suite; these tests are the point."""
    monkeypatch.delenv("MACROKEY_NO_UPDATE", raising=False)


def publish(directory: Path, files: dict[str, bytes], *, tag: str = "v9.9.9") -> Release:
    """Writes `files` and a SHA256SUMS.txt beside them, as the workflow does."""
    directory.mkdir(parents=True, exist_ok=True)
    assets: dict[str, str] = {}
    lines = []
    for name, body in files.items():
        (directory / name).write_bytes(body)
        assets[name] = (directory / name).as_uri()
        lines.append(f"{hashlib.sha256(body).hexdigest()}  {name}")
    sums = directory / releases.CHECKSUMS_ASSET
    sums.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assets[releases.CHECKSUMS_ASSET] = sums.as_uri()
    return Release(
        tag=tag, version=tag.lstrip("v"), notes="", prerelease=False, assets=assets
    )


# ------------------------------------------------------------------ versions --


@pytest.mark.parametrize(
    ("candidate", "current", "expected"),
    [
        ("0.11.2", "0.11.1", True),
        ("v0.12.0", "0.11.9", True),
        ("1.0.0", "0.99.99", True),
        ("0.11.1", "0.11.1", False),
        ("0.11.0", "0.11.1", False),
        # "0.11" is not newer than "0.11.1" -- comparing them at unequal
        # lengths made the shorter one win and offered a downgrade as an update.
        ("0.11", "0.11.1", False),
        ("0.11.1", "0.11", True),
        # Nothing to compare is not the same as newer.
        ("", "0.11.1", False),
        ("nightly", "0.11.1", False),
        ("0.11.2", "", False),
    ],
)
def test_only_a_clearly_higher_version_counts_as_newer(candidate, current, expected) -> None:
    assert releases.is_newer(candidate, current) is expected


def test_the_switch_that_stops_every_network_call(monkeypatch) -> None:
    monkeypatch.setenv("MACROKEY_NO_UPDATE", "1")
    assert releases.enabled() is False
    with pytest.raises(UpdateError):
        releases.latest_release()


# ----------------------------------------------------------------- downloads --


def test_a_download_is_kept_only_when_it_matches_the_published_hash(tmp_path) -> None:
    release = publish(tmp_path / "release", {"thing.bin": b"payload"})
    into = tmp_path / "into"
    kept = releases.fetch_asset(release, "thing.bin", into)
    assert kept.read_bytes() == b"payload"


def test_a_download_that_does_not_match_is_not_left_on_disk(tmp_path) -> None:
    source = tmp_path / "release"
    source.mkdir()
    release = publish(source, {"thing.bin": b"payload"})
    # The release says one thing; the file served says another. That is the
    # shape of both a corrupted download and a swapped asset.
    (source / "thing.bin").write_bytes(b"something else entirely")
    into = tmp_path / "into"
    with pytest.raises(UpdateError):
        releases.fetch_asset(release, "thing.bin", into)
    assert list(into.iterdir()) == []


def test_an_asset_with_no_published_hash_is_refused(tmp_path) -> None:
    source = tmp_path / "release"
    source.mkdir()
    release = publish(source, {"thing.bin": b"payload"})
    (source / "extra.bin").write_bytes(b"unlisted")
    release = Release(
        tag=release.tag,
        version=release.version,
        notes="",
        prerelease=False,
        assets={**release.assets, "extra.bin": (source / "extra.bin").as_uri()},
    )
    with pytest.raises(UpdateError, match="checksum"):
        releases.fetch_asset(release, "extra.bin", tmp_path / "into")


def test_checksums_are_read_from_the_release_own_file(tmp_path) -> None:
    release = publish(tmp_path / "release", {"a.bin": b"a", "b.bin": b"b"})
    found = releases.checksums(release)
    assert found["a.bin"] == hashlib.sha256(b"a").hexdigest()
    assert found["b.bin"] == hashlib.sha256(b"b").hexdigest()


# ------------------------------------------------------------------ firmware --


def test_the_version_is_read_out_of_the_image_itself(bundled_firmware_image) -> None:
    """No manifest, no filename: the string the pad will report is in the image."""
    assert image_version(find_image(DEFAULT_BOARD)) == bundled_firmware_image


def test_an_image_with_no_version_string_is_never_offered(tmp_path) -> None:
    """"Cannot tell" is not "older". Writing on a guess is how a working pad
    gets reflashed with what it is already running, or with something else."""
    blank = tmp_path / "firmware-promicro.hex"
    blank.write_text(":00000001FF\n", encoding="utf-8")
    candidate = firmware.Candidate(image=blank, version=image_version(blank))
    assert candidate.version is None
    assert candidate.newer_than("0.0.1") is False


def test_a_pad_already_on_the_bundled_version_is_left_alone(bundled_firmware_image) -> None:
    assert firmware.best(DEFAULT_BOARD, bundled_firmware_image, allow_network=False) is None


def test_a_pad_behind_the_bundled_version_gets_it_without_the_network(
    bundled_firmware_image,
) -> None:
    """The ordinary update needs no internet at all, which is the whole point
    of shipping the images inside the app."""
    candidate = firmware.best(DEFAULT_BOARD, "0.0.1", allow_network=False)
    assert candidate is not None
    assert candidate.image == find_image(DEFAULT_BOARD)
    assert candidate.version == bundled_firmware_image and candidate.downloaded is False


def test_the_network_is_not_consulted_when_the_bundled_image_is_enough(
    monkeypatch, bundled_firmware_image
) -> None:
    def refuse() -> Release:
        raise AssertionError("the network was asked about an update already in hand")

    monkeypatch.setattr(releases, "latest_release", refuse)
    assert firmware.best(DEFAULT_BOARD, "0.0.1") is not None


def test_a_published_image_is_used_when_it_is_newer_than_the_bundled_one(
    tmp_path, monkeypatch, bundled_firmware_image
) -> None:
    """A release published after this app was installed. The version comes from
    the downloaded image, not from the release tag: firmware and app are two
    numbers on two different clocks."""
    source = tmp_path / "release"
    source.mkdir()
    payload = _hex_carrying(b" fw=99.0.0 board=promicro ")
    release = publish(source, {DEFAULT_BOARD.firmware_name: payload})
    monkeypatch.setattr(releases, "latest_release", lambda: release)
    monkeypatch.setattr(firmware, "cache_dir", lambda: tmp_path / "cache")
    # The pad is level with what this build carries, so only the network can
    # have anything to add -- which is the case this covers.
    candidate = firmware.best(DEFAULT_BOARD, bundled_firmware_image)
    assert candidate is not None
    assert candidate.downloaded is True
    assert candidate.version == "99.0.0"


def test_nothing_is_downloaded_while_this_app_is_the_newest_release(
    tmp_path, monkeypatch, bundled_firmware_image
) -> None:
    """The firmware follows the app. If this app is the newest release, the
    image it carries is the newest published one -- and the ordinary case, a pad
    that is already current, was downloading a copy of what it was holding on
    every single connect."""
    from macrokey import __version__

    source = tmp_path / "release"
    release = publish(
        source,
        {DEFAULT_BOARD.firmware_name: _hex_carrying(b" fw=99.0.0 board=x ")},
        tag=f"v{__version__}",
    )
    monkeypatch.setattr(releases, "latest_release", lambda: release)
    monkeypatch.setattr(firmware, "cache_dir", lambda: tmp_path / "cache")

    assert firmware.best(DEFAULT_BOARD, bundled_firmware_image) is None
    assert not (tmp_path / "cache").exists(), "it went to the network anyway"


def test_a_published_image_older_than_the_bundled_one_is_ignored(
    tmp_path, monkeypatch, bundled_firmware_image
) -> None:
    source = tmp_path / "release"
    source.mkdir()
    release = publish(
        source, {DEFAULT_BOARD.firmware_name: _hex_carrying(b" fw=0.0.2 board=x ")}
    )
    monkeypatch.setattr(releases, "latest_release", lambda: release)
    monkeypatch.setattr(firmware, "cache_dir", lambda: tmp_path / "cache")
    assert firmware.best(DEFAULT_BOARD, "0.0.1") == firmware.bundled(DEFAULT_BOARD)
    assert firmware.bundled(DEFAULT_BOARD).version == bundled_firmware_image


def test_a_release_that_cannot_be_read_is_not_an_error(
    tmp_path, monkeypatch, bundled_firmware_image
) -> None:
    """Offering an update is optional; the app works with no network at all."""

    def fail() -> Release:
        raise UpdateError("no route to host")

    monkeypatch.setattr(releases, "latest_release", fail)
    monkeypatch.setattr(firmware, "cache_dir", lambda: tmp_path / "cache")
    assert firmware.best(DEFAULT_BOARD, bundled_firmware_image) is None


def _hex_carrying(text: bytes) -> bytes:
    """An Intel HEX file whose data records spell out `text`."""
    lines = []
    for offset in range(0, len(text), 16):
        chunk = text[offset : offset + 16]
        record = bytes([len(chunk), (offset >> 8) & 0xFF, offset & 0xFF, 0x00]) + chunk
        checksum = (-sum(record)) & 0xFF
        lines.append(":" + record.hex().upper() + f"{checksum:02X}")
    lines.append(":00000001FF")
    return ("\n".join(lines) + "\n").encode("ascii")


def test_the_hex_reader_finds_a_version_across_record_boundaries() -> None:
    blob = _hex_carrying(b"HELLO proto=1 fw=1.2.3 board=promicro keys=8")
    path = Path(os.environ["MACROKEY_CONFIG_DIR"]) / "made-up.hex"
    path.write_bytes(blob)
    assert image_version(path) == "1.2.3"


# ----------------------------------------------------------------- self-update --


def test_a_source_checkout_refuses_to_update_itself() -> None:
    """`git pull` is the update for a working tree, and overwriting one with a
    downloaded binary would be a surprising thing for a keypad editor to do."""
    usable, why = selfupdate.supported()
    assert usable is False
    assert "git pull" in why


def test_the_running_binary_is_replaced_only_after_the_new_one_is_verified(
    tmp_path, monkeypatch
) -> None:
    """The swap is the dangerous half. It happens after a checked download is
    already sitting complete on the same filesystem, never during one."""
    source = tmp_path / "release"
    source.mkdir()
    name = "macrokey-linux-x86_64"
    release = publish(source, {name: b"#!/bin/sh\necho new\n"})
    monkeypatch.setattr(selfupdate, "asset_name", lambda: name)

    current = tmp_path / "bin" / "macrokey"
    current.parent.mkdir()
    current.write_bytes(b"#!/bin/sh\necho old\n")

    selfupdate.apply(release, target=current)
    assert current.read_bytes() == b"#!/bin/sh\necho new\n"
    assert os.access(current, os.X_OK), "a downloaded binary arrives without +x"


def test_a_failed_download_leaves_the_running_binary_alone(tmp_path, monkeypatch) -> None:
    source = tmp_path / "release"
    source.mkdir()
    name = "macrokey-linux-x86_64"
    release = publish(source, {name: b"new"})
    (source / name).write_bytes(b"tampered")  # no longer matches the hash
    monkeypatch.setattr(selfupdate, "asset_name", lambda: name)

    current = tmp_path / "bin" / "macrokey"
    current.parent.mkdir()
    current.write_bytes(b"old")
    with pytest.raises(UpdateError):
        selfupdate.apply(release, target=current)
    assert current.read_bytes() == b"old"
    assert list(current.parent.iterdir()) == [current]


@pytest.mark.skipif(sys.platform.startswith("win"), reason="posix rename semantics")
def test_the_retired_binary_is_not_left_behind_on_posix(tmp_path, monkeypatch) -> None:
    """Windows cannot delete the image of the running process, so it cleans up
    at the next start. Everywhere else there is no reason to leave a copy."""
    source = tmp_path / "release"
    source.mkdir()
    name = "macrokey-linux-x86_64"
    release = publish(source, {name: b"new"})
    monkeypatch.setattr(selfupdate, "asset_name", lambda: name)

    current = tmp_path / "bin" / "macrokey"
    current.parent.mkdir()
    current.write_bytes(b"old")
    selfupdate.apply(release, target=current)
    assert [path.name for path in current.parent.iterdir()] == ["macrokey"]


def test_only_platforms_with_a_published_binary_are_offered_one(monkeypatch) -> None:
    monkeypatch.setattr("platform.machine", lambda: "aarch64")
    assert selfupdate.asset_name() is None


def test_the_asset_names_are_the_ones_the_workflow_uploads() -> None:
    """These two strings are a contract with `.github/workflows/release.yml`.
    Nothing fails when they drift -- the download just 404s at the one moment
    it was needed."""
    workflow = Path(__file__).resolve().parent.parent / ".github/workflows/release.yml"
    text = workflow.read_text(encoding="utf-8")
    assert "macrokey-linux-x86_64" in text
    assert "macrokey-windows-x86_64.exe" in text
    assert "SHA256SUMS.txt" in text


def test_the_firmware_asset_names_are_the_ones_the_workflow_uploads() -> None:
    workflow = Path(__file__).resolve().parent.parent / ".github/workflows/release.yml"
    text = workflow.read_text(encoding="utf-8")
    for board_id in ("promicro", "promicro-rp2040"):
        board = board_by_id(board_id)
        assert board is not None
        assert board.firmware_name in text, f"{board.firmware_name} is not published"


def test_the_settings_default_to_keeping_both_current() -> None:
    from macrokey.config.store import Settings

    assert Settings().auto_update_app is True
    assert Settings().auto_update_firmware is True


def test_a_release_reads_its_assets_out_of_the_api_answer() -> None:
    payload = json.loads(
        json.dumps(
            {
                "tag_name": "v1.2.3",
                "prerelease": False,
                "body": "notes",
                "assets": [
                    {"name": "a", "browser_download_url": "https://example/a"},
                    {"name": "b"},  # incomplete, and dropped rather than half-kept
                ],
            }
        )
    )
    release = Release.from_json(payload)
    assert release.version == "1.2.3"
    assert release.assets == {"a": "https://example/a"}


def test_a_release_answer_of_the_wrong_shape_is_not_a_crash() -> None:
    """`MACROKEY_UPDATE_REPO` is settable, so "the API always answers like
    this" is not an assumption worth making."""
    release = Release.from_json(
        {"tag_name": "v1.0.0", "assets": ["not a dict", {"name": "a"}, None]}
    )
    assert release.assets == {}


def test_a_hex_file_cannot_ask_for_four_gigabytes(tmp_path) -> None:
    """One two-byte extended-address record says where the next data goes, so a
    corrupt file naming 0xFFFF0000 had this fill the gap to reach it."""
    from macrokey.flash import images

    body = (
        ":02000004FFFF" + "FC\n"       # extended linear address 0xFFFF0000
        ":0100000041BE\n"              # one byte of data up there
        ":00000001FF\n"
    )
    path = tmp_path / "huge.hex"
    path.write_text(body, encoding="utf-8")
    assert image_version(path) is None
    assert len(images._intel_hex_payload(body)) <= images._MAX_IMAGE_BYTES


def test_a_download_that_cannot_be_put_in_place_says_so(tmp_path) -> None:
    """A read-only directory, or a name that is open on Windows. A bare OSError
    from the final rename reached the CLI as a traceback, not a sentence."""
    release = publish(tmp_path / "release", {"thing.bin": b"payload"})
    into = tmp_path / "into"
    into.mkdir()
    (into / "thing.bin").mkdir()  # the target name is not writable as a file
    with pytest.raises(UpdateError, match="in place"):
        releases.fetch_asset(release, "thing.bin", into)
