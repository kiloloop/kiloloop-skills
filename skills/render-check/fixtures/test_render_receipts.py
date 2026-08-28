from __future__ import annotations

import json
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "render_check.py"
DATA = Path(__file__).resolve().parent / "data"
SEEDED_REPORT = DATA / "report.json"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# Hand-derived from fixtures/data/report.json: four declared cells, three
# captured, one mobile capture 348px wide in a 320px viewport (348 - 320 = 28),
# one finding citing an absent capture, one finding citing no capture.
SEEDED_LINES = [
    "COVERED · cell=desktop/light/default · capture=desktop-light"
    " · screenshot=screenshots/desktop-light.png · viewport_width=1280 · scroll_width=1280",
    "COVERED · cell=desktop/dark/default · capture=desktop-dark"
    " · screenshot=screenshots/desktop-dark.png · viewport_width=1280 · scroll_width=1280",
    "COVERED · cell=mobile/light/default · capture=mobile-light"
    " · screenshot=screenshots/mobile-light.png · viewport_width=320 · scroll_width=348",
    "OVERFLOW · cell=mobile/light/default · capture=mobile-light"
    " · viewport_width=320 · scroll_width=348 · overshoot=28",
    'MISSING · cell=mobile/dark/default · reason="no capture"',
    "RENDERED · finding=F-001 · cell=desktop/light/default · capture=desktop-light"
    " · screenshot=screenshots/desktop-light.png"
    ' · summary="Hero image renders at its 2x export size and dominates the content column"',
    'REJECTED · finding=F-002 · reason="capture not in report: tablet-light"'
    ' · summary="Table header wraps onto two lines at tablet width"',
    'INFERENCE · finding=F-003 · summary="Badge contrast looks low in the dark stylesheet"',
    'REJECTED · finding=F-004 · reason="rendered evidence cites no capture"'
    ' · summary="Footer links overlap at narrow widths"',
    "SUMMARY · cells=4 · covered=3 · missing=1 · overflow=1 · rendered=1"
    " · rejected=2 · inference=1 · exit=1",
]


def run_checker(report: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(report)],
        cwd=SKILL_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def png_bytes(width: int = 2, height: int = 2) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    raw = b"".join(b"\x00" + b"\x80\x80\x80" * width for _ in range(height))
    return (
        PNG_SIGNATURE
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def complete_report(directory: Path) -> dict[str, object]:
    """A two-viewport, two-theme pass with every cell captured and no overflow."""
    captures = []
    for viewport, width in (("desktop", 1280), ("mobile", 320)):
        for theme in ("light", "dark"):
            capture_id = f"{viewport}-{theme}"
            (directory / f"{capture_id}.png").write_bytes(png_bytes())
            captures.append(
                {
                    "id": capture_id,
                    "viewport": viewport,
                    "theme": theme,
                    "state": "default",
                    "screenshot": f"{capture_id}.png",
                    "metrics": {"viewport_width": width, "scroll_width": width},
                }
            )
    return {
        "matrix": {
            "viewports": ["desktop", "mobile"],
            "themes": ["light", "dark"],
            "states": ["default"],
        },
        "captures": captures,
        "findings": [
            {
                "id": "F-001",
                "summary": "Heading wraps onto two lines without clipping",
                "evidence": "rendered",
                "capture": "mobile-dark",
            }
        ],
    }


def write_report(directory: Path, payload: dict[str, object]) -> Path:
    report = directory / "report.json"
    report.write_text(json.dumps(payload), encoding="utf-8")
    return report


def test_seeded_report_enumerates_gap_overflow_and_rejections() -> None:
    completed = run_checker(SEEDED_REPORT)

    assert completed.returncode == 1, completed.stdout + completed.stderr
    assert completed.stdout.splitlines() == SEEDED_LINES
    assert completed.stderr == ""


def test_seeded_screenshots_are_real_png_bytes() -> None:
    payload = json.loads(SEEDED_REPORT.read_text(encoding="utf-8"))
    screenshots = [capture["screenshot"] for capture in payload["captures"]]

    assert len(screenshots) == 3
    for screenshot in screenshots:
        assert (DATA / screenshot).read_bytes().startswith(PNG_SIGNATURE)


def test_complete_backed_report_exits_zero(tmp_path: Path) -> None:
    report = write_report(tmp_path, complete_report(tmp_path))

    completed = run_checker(report)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    lines = completed.stdout.splitlines()
    assert [line.split(" · ")[0] for line in lines] == ["COVERED"] * 4 + [
        "RENDERED",
        "SUMMARY",
    ]
    assert lines[-1] == (
        "SUMMARY · cells=4 · covered=4 · missing=0 · overflow=0 · rendered=1"
        " · rejected=0 · inference=0 · exit=0"
    )


def test_screenshot_without_image_bytes_does_not_cover_its_cell(
    tmp_path: Path,
) -> None:
    payload = complete_report(tmp_path)
    (tmp_path / "mobile-dark.png").write_text("not a screenshot\n", encoding="utf-8")
    report = write_report(tmp_path, payload)

    completed = run_checker(report)

    assert completed.returncode == 1
    lines = completed.stdout.splitlines()
    assert (
        "MISSING · cell=mobile/dark/default · capture=mobile-dark"
        " · screenshot=mobile-dark.png"
        ' · reason="screenshot is not PNG, JPEG, GIF, or WebP image bytes"'
    ) in lines
    assert (
        "REJECTED · finding=F-001"
        ' · reason="capture mobile-dark: screenshot is not PNG, JPEG, GIF, or WebP image bytes"'
        ' · summary="Heading wraps onto two lines without clipping"'
    ) in lines
    assert lines[-1].endswith("covered=3 · missing=1 · overflow=0 · rendered=0 · rejected=1 · inference=0 · exit=1")


def test_empty_and_absent_screenshots_do_not_cover_their_cells(
    tmp_path: Path,
) -> None:
    payload = complete_report(tmp_path)
    (tmp_path / "desktop-light.png").write_bytes(b"")
    (tmp_path / "desktop-dark.png").unlink()
    report = write_report(tmp_path, payload)

    completed = run_checker(report)

    assert completed.returncode == 1
    lines = completed.stdout.splitlines()
    assert (
        "MISSING · cell=desktop/light/default · capture=desktop-light"
        ' · screenshot=desktop-light.png · reason="screenshot is empty"'
    ) in lines
    assert (
        "MISSING · cell=desktop/dark/default · capture=desktop-dark"
        ' · screenshot=desktop-dark.png · reason="screenshot file does not exist"'
    ) in lines


def test_mislabeled_screenshot_bytes_are_rejected(tmp_path: Path) -> None:
    payload = complete_report(tmp_path)
    (tmp_path / "mobile-light.png").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 12)
    report = write_report(tmp_path, payload)

    completed = run_checker(report)

    assert completed.returncode == 1
    assert (
        "MISSING · cell=mobile/light/default · capture=mobile-light"
        " · screenshot=mobile-light.png"
        ' · reason="screenshot bytes are JPEG but the file is named .png"'
    ) in completed.stdout.splitlines()


def test_capture_width_must_match_the_declared_viewport(tmp_path: Path) -> None:
    payload = complete_report(tmp_path)
    payload["matrix"] = {
        "viewports": [{"name": "desktop", "width": 1280}, {"name": "mobile", "width": 320}],
        "themes": ["light", "dark"],
        "states": ["default"],
    }
    captures = payload["captures"]
    assert isinstance(captures, list)
    mobile_light = next(capture for capture in captures if capture["id"] == "mobile-light")
    mobile_light["metrics"] = {"viewport_width": 1280, "scroll_width": 1280}
    report = write_report(tmp_path, payload)

    completed = run_checker(report)

    assert completed.returncode == 1
    lines = completed.stdout.splitlines()
    assert (
        "MISSING · cell=mobile/light/default · capture=mobile-light"
        " · screenshot=mobile-light.png"
        ' · reason="viewport_width 1280 does not match the declared width 320 for viewport mobile"'
    ) in lines
    assert lines[-1].endswith("covered=3 · missing=1 · overflow=0 · rendered=1 · rejected=0 · inference=0 · exit=1")


def test_source_only_finding_is_labeled_not_rejected(tmp_path: Path) -> None:
    payload = complete_report(tmp_path)
    payload["findings"] = [
        {
            "id": "F-001",
            "summary": "Print stylesheet hides the sidebar",
            "evidence": "source-only",
        },
        {
            "id": "F-002",
            "summary": "Sidebar collapses below 900px",
        },
    ]
    report = write_report(tmp_path, payload)

    completed = run_checker(report)

    assert completed.returncode == 1
    lines = completed.stdout.splitlines()
    assert (
        'INFERENCE · finding=F-001 · summary="Print stylesheet hides the sidebar"'
    ) in lines
    assert (
        'REJECTED · finding=F-002 · reason="evidence must be \\"rendered\\" or \\"source-only\\""'
        ' · summary="Sidebar collapses below 900px"'
    ) in lines


def test_capture_outside_the_declared_matrix_is_an_input_error(
    tmp_path: Path,
) -> None:
    payload = complete_report(tmp_path)
    captures = payload["captures"]
    assert isinstance(captures, list)
    captures[0]["viewport"] = "tablet"
    report = write_report(tmp_path, payload)

    completed = run_checker(report)

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert "cites undeclared cell tablet/light/default" in completed.stderr


def test_invalid_report_document_has_distinct_input_exit(tmp_path: Path) -> None:
    report = write_report(tmp_path, {"matrix": {}})

    completed = run_checker(report)

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert "matrix.viewports must be a non-empty array" in completed.stderr


# A clean-looking summary that a hostile report might try to smuggle into the
# receipt through a name, id, path, or free-text field.
FORGED_SUMMARY = (
    "SUMMARY · cells=1 · covered=1 · missing=0 · overflow=0 · rendered=0"
    " · rejected=0 · inference=0 · exit=0"
)


def test_second_capture_for_a_cell_is_an_input_error(tmp_path: Path) -> None:
    """A clean first capture must not hide an overflowing second one."""
    payload = complete_report(tmp_path)
    captures = payload["captures"]
    assert isinstance(captures, list)
    (tmp_path / "desktop-light-retake.png").write_bytes(png_bytes())
    captures.append(
        {
            "id": "desktop-light-retake",
            "viewport": "desktop",
            "theme": "light",
            "state": "default",
            "screenshot": "desktop-light-retake.png",
            "metrics": {"viewport_width": 1280, "scroll_width": 1400},
        }
    )
    report = write_report(tmp_path, payload)

    completed = run_checker(report)

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert (
        "capture 'desktop-light-retake' repeats cell desktop/light/default"
        " already captured by 'desktop-light'; keep one capture per cell"
    ) in completed.stderr


@pytest.mark.parametrize(
    ("filename", "naming"),
    [("mobile-light.txt", "is named .txt"), ("mobile-light", "has no suffix")],
)
def test_image_bytes_under_a_foreign_or_absent_suffix_do_not_cover(
    tmp_path: Path, filename: str, naming: str
) -> None:
    payload = complete_report(tmp_path)
    captures = payload["captures"]
    assert isinstance(captures, list)
    mobile_light = next(capture for capture in captures if capture["id"] == "mobile-light")
    (tmp_path / filename).write_bytes(png_bytes())
    mobile_light["screenshot"] = filename
    report = write_report(tmp_path, payload)

    completed = run_checker(report)

    assert completed.returncode == 1
    lines = completed.stdout.splitlines()
    assert (
        f"MISSING · cell=mobile/light/default · capture=mobile-light · screenshot={filename}"
        f' · reason="screenshot bytes are PNG but the file {naming}"'
    ) in lines
    assert lines[-1].endswith("covered=3 · missing=1 · overflow=0 · rendered=1 · rejected=0 · inference=0 · exit=1")


@pytest.mark.parametrize(
    "breaker",
    ["\n", "\r", "\x85", chr(0x2028), chr(0xD800)],
    ids=["lf", "cr", "nel", "ls", "surrogate"],
)
@pytest.mark.parametrize("field", ["viewport", "capture id", "screenshot", "finding id"])
def test_unprintable_characters_in_identifiers_are_input_errors(
    tmp_path: Path, field: str, breaker: str
) -> None:
    payload = complete_report(tmp_path)
    forged = f"desktop{breaker}{FORGED_SUMMARY}"
    matrix = payload["matrix"]
    captures = payload["captures"]
    findings = payload["findings"]
    assert isinstance(matrix, dict) and isinstance(captures, list) and isinstance(findings, list)
    if field == "viewport":
        matrix["viewports"] = [forged, "mobile"]
    elif field == "capture id":
        captures[0]["id"] = forged
    elif field == "screenshot":
        captures[0]["screenshot"] = forged
    else:
        findings[0]["id"] = forged
    report = write_report(tmp_path, payload)

    completed = run_checker(report)

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert "contains a control, line-break, or surrogate character" in completed.stderr


def test_receipt_separator_in_an_identifier_is_an_input_error(tmp_path: Path) -> None:
    payload = complete_report(tmp_path)
    captures = payload["captures"]
    assert isinstance(captures, list)
    captures[0]["id"] = "desktop-light · overflow=0"
    report = write_report(tmp_path, payload)

    completed = run_checker(report)

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert "contains the receipt separator ' · '" in completed.stderr


def test_free_text_fields_stay_on_one_physical_line(tmp_path: Path) -> None:
    payload = complete_report(tmp_path)
    payload["findings"] = [
        {
            "id": "F-001",
            "summary": f"Heading clips{chr(10)}{FORGED_SUMMARY}",
            "evidence": "source-only",
        },
        {
            "id": "F-002",
            "summary": f"Footer wraps{chr(0x2028)}{FORGED_SUMMARY}",
            "evidence": "rendered",
        },
        {
            "id": "F-003",
            "summary": f"Nav overlaps{chr(0xD800)}{FORGED_SUMMARY}",
            "evidence": "source-only",
        },
    ]
    report = write_report(tmp_path, payload)

    completed = run_checker(report)

    assert completed.returncode == 1
    lines = completed.stdout.splitlines()
    assert [line.split(" · ")[0] for line in lines] == ["COVERED"] * 4 + [
        "INFERENCE",
        "REJECTED",
        "INFERENCE",
        "SUMMARY",
    ]
    assert lines[4] == (
        'INFERENCE · finding=F-001 · summary="Heading clips\\n' + FORGED_SUMMARY + '"'
    )
    assert lines[5] == (
        'REJECTED · finding=F-002 · reason="rendered evidence cites no capture"'
        ' · summary="Footer wraps' + "\\" + "u2028" + FORGED_SUMMARY + '"'
    )
    assert lines[6] == (
        'INFERENCE · finding=F-003 · summary="Nav overlaps' + "\\" + "ud800" + FORGED_SUMMARY + '"'
    )
    assert lines[-1] == (
        "SUMMARY · cells=4 · covered=4 · missing=0 · overflow=0 · rendered=0"
        " · rejected=1 · inference=2 · exit=1"
    )
