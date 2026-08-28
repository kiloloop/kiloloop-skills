# Render Check

Review the surface a reader actually sees — screenshots and measured layout
across viewports, themes, and states — before calling it done.

## What it looks like

The shipped fixture describes a pass over a two-viewport, two-theme page in
which one cell was never captured, one capture measured horizontal overflow,
and two findings claim rendered evidence they do not have. Running the checker
on it produces one line per cell and per finding and exits `1`:

```console
$ python scripts/render_check.py fixtures/data/report.json
COVERED · cell=desktop/light/default · capture=desktop-light · screenshot=screenshots/desktop-light.png · viewport_width=1280 · scroll_width=1280
COVERED · cell=desktop/dark/default · capture=desktop-dark · screenshot=screenshots/desktop-dark.png · viewport_width=1280 · scroll_width=1280
COVERED · cell=mobile/light/default · capture=mobile-light · screenshot=screenshots/mobile-light.png · viewport_width=320 · scroll_width=348
OVERFLOW · cell=mobile/light/default · capture=mobile-light · viewport_width=320 · scroll_width=348 · overshoot=28
MISSING · cell=mobile/dark/default · reason="no capture"
RENDERED · finding=F-001 · cell=desktop/light/default · capture=desktop-light · screenshot=screenshots/desktop-light.png · summary="Hero image renders at its 2x export size and dominates the content column"
REJECTED · finding=F-002 · reason="capture not in report: tablet-light" · summary="Table header wraps onto two lines at tablet width"
INFERENCE · finding=F-003 · summary="Badge contrast looks low in the dark stylesheet"
REJECTED · finding=F-004 · reason="rendered evidence cites no capture" · summary="Footer links overlap at narrow widths"
SUMMARY · cells=4 · covered=3 · missing=1 · overflow=1 · rendered=1 · rejected=2 · inference=1 · exit=1
```

The overshoot is arithmetic on the recorded metrics: `348 - 320 = 28`. The
missing cell is named exactly. The rejected findings keep their summaries, so
the reader sees what was claimed without evidence.

## Ask an agent

Paste this after installing the skill and replace the bracketed values:

```text
Use the render-check skill on [THE SURFACE] before reporting it done. Declare
the viewport, theme, and state matrix it should cover, open the live surface,
capture every cell with a screenshot and layout metrics, write the report
JSON, run the checker, and paste its lines into your reply with the findings
ordered by importance. Label anything you inferred from source rather than saw
rendered, and do not call the surface verified while any line is MISSING,
REJECTED, or OVERFLOW.
```

## What it is

`render-check` is a procedure for reviewing rendered output plus a
standard-library Python checker that validates the review's evidence. The
procedure covers what to open, which cells to capture, what to measure, and
how to report. The checker reads a JSON report of the pass and enumerates
uncaptured cells, rejects findings presented as rendered evidence without a
real screenshot behind them, labels source-only inference, and surfaces every
capture whose content is wider than its viewport.

It ships no browser automation. Any tool that produces a screenshot file and
the page's viewport and scroll widths can feed it; the skill instructions show
the Playwright shape.

## Install

Copy the complete `render-check` directory into the skills directory used by
your coding-agent runtime. Keep `SKILL.md`, `scripts/`, and `fixtures/`
together. The checker uses only the Python standard library and installs
nothing at runtime.

## Use it

1. Declare the matrix, then capture each cell with a screenshot and metrics.
2. Write `report.json` in the shape shown in `SKILL.md`. Screenshot paths
   resolve relative to the report file.
3. From the installed skill directory, run:

```bash
python scripts/render_check.py path/to/report.json
```

Exit `0` means every declared cell is covered, every finding is backed or
labeled inference, and no capture measured overflow. Exit `1` means at least
one `MISSING`, `REJECTED`, or `OVERFLOW` line. Exit `2` means the report is
invalid.

## Falsifiable claim

> A render report that passes this checker cannot present source-only
> inference as rendered evidence, cannot cite a capture or screenshot that does
> not exist as real image bytes, and cannot claim coverage while any cell of
> its declared viewport × theme × state matrix lacks a capture. Every missing
> cell is enumerated exactly, every capture whose recorded `scroll_width`
> exceeds its `viewport_width` is surfaced as overflow, and every receipt is
> one physical line that no report-supplied text can split or forge.

The seeded fixture leaves one cell uncaptured, records a 348px-wide render in
a 320px viewport, cites a capture that is not in the report, and presents a
finding as rendered with no capture at all. The fixture asserts the checker's
complete output line by line, including the missing cell, the 28px overshoot,
and both rejections, and that the run exits `1`; a complete, backed,
overflow-free report exits `0`. Making the checker accept an uncited capture,
count a text file as a screenshot, evaluate only the first capture for a cell,
or let a viewport name break a line turns the fixtures red.

## Choices worth knowing about

**The matrix is a declaration, not a discovery.** Cells are the full product
of the declared viewports, themes, and states, and a capture outside that
product is an input error. A pass cannot widen its apparent coverage by
capturing an extra state while never declaring the cells it skipped; it
declares the matrix it will cover and is measured against it.

**Screenshot bytes are checked; file names are not trusted.** A capture covers
its cell only when its file starts with a PNG, JPEG, GIF, or WebP signature and
the extension agrees with the bytes. An empty file, a text placeholder, JPEG
bytes named `.png`, or PNG bytes saved as `.txt` or with no extension leave
the cell `MISSING` with the reason attached. The
checker does not decode the image, so it cannot tell whether the picture shows
the cell the capture claims.

**One capture per cell.** A second capture for a cell that already has one is
an input error, not a retake. Every capture the report carries is evaluated
and printed, so a clean render cannot stand in front of an overflowing one
for the same cell; drop the capture you do not mean before rerunning.

**Every receipt is one physical line.** Names, ids, and screenshot paths print
unquoted, so the checker rejects any that carry a control character, a line
or paragraph separator, a lone surrogate, or the ` · ` field separator.
Summaries and reasons are JSON-quoted with those characters escaped. A reader that consumes the
receipt line by line sees exactly the lines the checker emitted, and nothing
a report supplies can forge a clean `SUMMARY`.

**A capture's width is checked against its viewport's declared width.** When
the matrix gives a viewport a `width` and the capture records a
`viewport_width`, the two must agree, so a capture labeled `mobile` but taken
at a desktop width does not cover the mobile cell. Declaring widths is
optional; without them, cell identity is by name alone.

**Overflow is the one measured defect.** `scroll_width` above
`viewport_width` on the document element is the standard test for horizontal
overflow, the same comparison as
[`Element.scrollWidth`](https://developer.mozilla.org/en-US/docs/Web/API/Element/scrollWidth)
against the visible width. Other defects in the probe list are judged from
the captures by the reviewer; the checker does not pretend to measure them.

**Source-only findings are allowed and labeled.** Reading a stylesheet is a
legitimate way to notice a problem. The skill exists to keep the label honest,
not to forbid inference: `INFERENCE` lines travel with the report and never
count as rendered evidence.

**Exit `1` covers both an incomplete pass and a dirty surface.** A missing
cell, an unbacked finding, and a measured overflow all mean the surface cannot
be reported as verified; the lines say which. A separate exit code per cause
would make the gate easier to script and harder to read in a pasted reply.

## Tradeoffs

- JSON keeps the checker dependency-free at the cost of hand-authoring the
  report; capture tooling can emit it directly.
- A full cross-product matrix grows quickly (2 viewports × 2 themes × 3
  states is 12 cells). Declare the states that exist for the surface, not
  every state imaginable.
- Metrics are optional per capture, so tooling without script access can
  still contribute screenshots. A capture without metrics cannot be checked
  for overflow, and its `COVERED` line says `metrics=absent`.

## What it cannot see

- Whether a screenshot shows the cell its capture claims, or the surface at
  all.
- Vertical clipping, overlap, contrast, typography, and every other defect
  that leaves no trace in the recorded widths.
- Whether the declared matrix is the right matrix for the surface's readers.
- Whether the captured revision is the one that shipped.
- Anything about the pass that was not written into the report.

## Verify the skill

From the repository root:

```bash
python -m pytest -q skills/render-check/fixtures/test_render_receipts.py
python scripts/run_skill_fixtures.py
```

The fixture's negative path exits `1`, names the uncaptured cell, reports the
28px overshoot, and rejects both unbacked findings. The positive path exits
`0` on a complete, backed, overflow-free report.
