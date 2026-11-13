from __future__ import annotations

from pathlib import Path

import pytest

from scripts.pii_scan import main, scan_text


@pytest.mark.parametrize(
    ("line", "kind"),
    [
        ("contact x7q-not-a-person@gmail.com today", "email"),
        ("call (999) 999-9999", "phone"),
        ("call +1 999 999 9999", "phone"),
        ("sin 130 692 544", "SIN"),
        ("call 9999999999", "phone"),
        ("call +19999999999", "phone"),
        ("call (999)999-9999", "phone"),
        ("appelez +33 1 23 45 67 89", "phone"),
    ],
)
def test_flags_personal_data(line: str, kind: str) -> None:
    assert [k for _, k, _ in scan_text(line)] == [kind]


@pytest.mark.parametrize(
    "line",
    [
        "Co-Authored-By: Claude <noreply@anthropic.com>",
        "send to user@example.com",
        "123+hassan@users.noreply.github.com",
        "version 1.2.3 build 2026-11-13",
        "sin-shaped but fails Luhn: 123 456 789",
        "uuid 550e8400-e29b-41d4-a716-446655440000",
        "sample learner jane@school.ca  # pii-ok",
        "ports 54321-54327 and 55432",
        "fictional (514) 555-0123 and +1 438 555 0199",
        "fictitious SIN 046 454 286 (leading 0 is never assigned)",
        "demo learner sam@school.test",
        "git clone git@github.com:org/repo.git",
        "epoch 1731500000 and sha 2f3a9c1d07",
        "fictional 5145550123",
        "version +1.2.3 and 3.12.4",
    ],
)
def test_allows_the_usual_noise(line: str) -> None:
    assert list(scan_text(line)) == []


def test_non_utf8_files_are_still_scanned(tmp_path: Path) -> None:
    export = tmp_path / "learners.csv"
    export.write_bytes("nom;courriel\nJosé;x7q-not-a-person@ecole.qc.ca\n".encode("cp1252"))
    assert main([str(export)]) == 1


def test_main_exits_nonzero_on_findings(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    clean = tmp_path / "clean.md"
    clean.write_text("nothing here\n")
    dirty = tmp_path / "dirty.md"
    dirty.write_text("ok\nreach me at x7q-not-a-person@gmail.com\n")

    assert main([str(clean)]) == 0
    assert main([str(clean), str(dirty)]) == 1
    assert f"{dirty}:2: possible email" in capsys.readouterr().out
