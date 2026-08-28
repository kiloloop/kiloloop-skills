---
name: render-check
description: Review the surface a reader actually sees before reporting it done. Use when a change renders for someone else — a web page or app build, a README or docs page on its hosting site, a pull-request or issue body, a chart, an email or PDF export — and the report must rest on screenshots and measured layout rather than on source inspection or an HTTP 200.
---

# Render Check

A surface is done when it looks right where its reader will look at it, not
when its source reads right. Source inspection, a green build, a byte-size
match, and an HTTP `200` prove that something was served. None of them shows
what was rendered.

## Core rule

Open the live surface, capture what it shows across the viewports, themes, and
states its reader can meet, and report only what the captures show. Say
plainly when a statement is source-only inference. Terminal output the reader
cannot see is not evidence either: paste the receipts into the reply.

## 1. Identify the surface and how to open it live

- **Web page or app**: the dev server, deployed preview, or production URL. If
  the browser tooling refuses `file://` navigation, serve the directory over
  `localhost`; do not route around the policy.
- **README, docs page, pull-request or issue body**: the hosting site's
  rendered view after the push or post. A local Markdown preview is a
  different renderer and counts as source-only for the hosted surface.
- **Chart, image, PDF, or email**: the exported artifact opened in the viewer
  the reader will use, at the size it will be shown.

## 2. Declare the coverage matrix

Write down, before capturing, the cells the pass will cover:

| Axis | Minimum | Add when |
| --- | --- | --- |
| `viewports` | one desktop width and one constrained width, for example `1280` and `320` | the surface has breakpoints or a mobile audience |
| `themes` | `light` and `dark` when the surface or its host has both | a themed host; most documentation hosts and app shells are |
| `states` | `default` | navigation, menus, overlays, modals, detail views, empty, loading, or error states exist and are reachable; for documents, expanded collapsibles and long tables |

The matrix is the pass's own declaration of coverage. The checker enumerates
every declared cell that has no capture, so declare the cells the pass will
visit and then visit them.

## 3. Capture every cell

For each cell:

1. Set the viewport width, emulate the theme, put the page in the state.
2. Wait for `load`. Reload once if sizing looks stale; some apps measure the
   viewport only at mount.
3. Take a screenshot and save it under a name that carries the cell, with an
   extension that matches the bytes: PNG bytes as `.png`, never JPEG bytes
   named `.png` or image bytes with no extension. One capture per cell; a
   retake replaces the earlier file rather than sitting beside it.
4. Record layout metrics from the same render:

```js
({
  viewport_width: window.innerWidth,
  viewport_height: window.innerHeight,
  scroll_width: document.documentElement.scrollWidth,
  scroll_height: document.documentElement.scrollHeight,
  dark: matchMedia("(prefers-color-scheme: dark)").matches,
})
```

`scroll_width` greater than `viewport_width` is horizontal overflow: content
the reader must scroll sideways to reach, or that is clipped. Record it; the
checker surfaces it.

With Playwright, one cell is:

```python
page.set_viewport_size({"width": 320, "height": 800})
page.emulate_media(color_scheme="dark")
page.goto(url, wait_until="load")
page.screenshot(path="captures/mobile-dark.png", full_page=True)
metrics = page.evaluate(
    "({viewport_width: window.innerWidth,"
    " scroll_width: document.documentElement.scrollWidth})"
)
```

Any tooling works as long as each capture has a real screenshot file and
metrics from the same render. Image bytes returned by a browser extension or
an in-app screenshot must be written to disk before they count as evidence.

## 4. Probe the usual defects

Look for these in the captures, not in the source:

- **Overflow at the narrowest width**: a horizontal scrollbar, a clipped table
  or code block, controls pushed outside the viewport.
- **Image sizing**: a 2x export displayed at its intrinsic size dominates the
  column; set the display width to the design's 1x size.
- **Theme variants**: contrast, badges, diagrams, and screenshots that only
  work in one theme; a host that picks images per theme needs both variants.
- **Clipping and overlap**: text, buttons, chips, and menus that clip or
  overlap; overlays that do not fit the viewport; nested scroll traps.
- **Stale sizing**: a layout measured once at startup that ignores a resize.
- **Document structure**: tables that must scroll inside their own container,
  collapsibles that open, embeds that load, anchors that resolve.

## 5. Write the report and run the checker

Describe the pass in a JSON report. Screenshot paths resolve relative to the
report file.

```json
{
  "surface": "https://example.test/preview/",
  "matrix": {
    "viewports": [{"name": "desktop", "width": 1280}, {"name": "mobile", "width": 320}],
    "themes": ["light", "dark"],
    "states": ["default"]
  },
  "captures": [
    {
      "id": "mobile-dark",
      "viewport": "mobile",
      "theme": "dark",
      "state": "default",
      "screenshot": "captures/mobile-dark.png",
      "metrics": {"viewport_width": 320, "scroll_width": 320}
    }
  ],
  "findings": [
    {"id": "F-001", "summary": "Nav overlaps the title below 360px", "evidence": "rendered", "capture": "mobile-dark"},
    {"id": "F-002", "summary": "Print stylesheet hides the sidebar", "evidence": "source-only"}
  ]
}
```

Every finding declares its evidence. `rendered` must cite a capture whose
screenshot is real image bytes; `source-only` is allowed and is labeled as
inference. From this skill directory, run:

```bash
python scripts/render_check.py path/to/report.json
```

One line per cell and per finding:

- `COVERED`: the cell has a capture with real screenshot bytes; the line
  carries its metrics.
- `OVERFLOW`: that capture's `scroll_width` exceeds its `viewport_width`; the
  line carries the overshoot in pixels.
- `MISSING`: the cell has no capture, or its screenshot is absent, empty, not
  image bytes, or named with an extension that disagrees with its bytes
  (including no extension), or its recorded `viewport_width` disagrees with
  the width the matrix declares for that viewport.
- `RENDERED`: a finding backed by a covered capture.
- `REJECTED`: a finding presented as rendered evidence without a usable
  capture behind it, or with no evidence label.
- `INFERENCE`: a finding declared source-only.
- `SUMMARY`: the counts and the exit code.

Exit codes:

- `0`: every cell is covered, every finding is backed or labeled inference,
  and no capture measured overflow. The pass is complete and the surface is
  clean.
- `1`: at least one `MISSING`, `REJECTED`, or `OVERFLOW` line. Do not report
  the surface as verified; complete the pass or fix the surface, then rerun.
- `2`: the report is invalid: no matrix, a capture citing an undeclared cell,
  a second capture for one cell, a repeated id, a malformed metric, or a
  name, id, or path carrying a control character or a lone surrogate. Fix
  the report and rerun.

A capture that cites a viewport, theme, or state outside the matrix is an
input error rather than bonus coverage. The matrix is the declaration, the
captures are the evidence, and the checker reports the difference.

Every receipt is exactly one physical line. Viewport, theme, and state names,
capture and finding ids, and screenshot paths print unquoted, so the checker
rejects any that carry a control character, a line or paragraph separator,
a lone surrogate, or the ` · ` field separator; summaries and reasons are
JSON-quoted with any such character escaped. No field a report supplies can add, split, or forge
a receipt line.

## 6. Report to the person

Lead with whether the pass was live-rendered or source-only, then the
findings, then the receipts. Paste the checker's lines; do not point at a
terminal or a folder.

```markdown
**Rendered check: live, 4 of 4 cells captured, 1 overflow.**

- **Surface:** https://example.test/preview/ at commit `<sha>`
- **Matrix:** desktop 1280 and mobile 320; light and dark; default
- **Browser:** Chromium via Playwright, `prefers-color-scheme` emulated

Findings, most important first:

1. **Mobile overflow (rendered, `mobile-light`)** — the comparison table is
   348px wide in a 320px viewport; `captures/mobile-light.png`.
2. **Hero image at 2x (rendered, `desktop-light`)** — displayed at 1600px in
   an 800px column; `captures/desktop-light.png`.
3. **Dark badge contrast (source-only)** — the badge color is hard-coded in
   the stylesheet; not confirmed in a capture.

Receipts:

COVERED · cell=desktop/light/default · capture=desktop-light · ...
OVERFLOW · cell=mobile/light/default · capture=mobile-light · viewport_width=320 · scroll_width=348 · overshoot=28
SUMMARY · cells=4 · covered=4 · missing=0 · overflow=1 · rendered=2 · rejected=0 · inference=1 · exit=1
```

Keep the qualifiers that make the pass checkable: the surface and its
revision, the widths, the theme mechanism, the browser. Name what was not
covered rather than implying completeness. Report what the captures show;
whether a finding matters is the reader's call unless they asked for a
verdict.

## Limits

The checker validates a report about a pass; it does not perform the pass. It
cannot open a browser, judge design quality, confirm that a screenshot shows
the cell its capture claims, or detect defects that leave no trace in the
recorded metrics. Horizontal overflow is the one defect it measures; the rest
of the probe list is the reviewer's eye on the captures.
