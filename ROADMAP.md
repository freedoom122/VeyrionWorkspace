# Veyrion Workspace — Viewer Feature Roadmap

100 planned viewer features, phased by dependency and leverage. Effort is
deliberately coarse (S/M/L) — these are relative sizes, not promises.

**Effort key:** `S` = hours, `M` = 1–3 days, `L` = 1–2 weeks, `XL` = multi-week.

**Status key:** ✅ shipped this session · 🟡 partially built · ⬜ not started

---

## Phase 0 — Already shipped (this session)

These shipped in earlier turns and are the foundation everything else builds on.

| # | Feature | Status |
|---|---------|--------|
| 3 | Go-to-page box (toolbar + Ctrl+G) | ✅ |
| 5 | Reading history back/forward (Alt+←/→) | ✅ |
| 9 | Reading progress ("42% read" in status bar) | ✅ |
| 12 | Command palette (Ctrl+K) + new commands | ✅ |
| 13 | Annotation undo/redo (Ctrl+Z/Y, per-view stack) | ✅ |
| 15 | Annotations panel (dock, jump-to-page) | 🟡 needs live refresh + delete-in-panel |
| 1 | Thumbnail sidebar with drag-to-jump | 🟡 needs drag reorder + live previews |
| 2 | TOC/outline panel | 🟡 needs expand-state persistence |
| 6 | Bookmarks panel + add-bookmark action | 🟡 needs rename/keyboard flow |
| 11 | Continue-where-you-left-off | 🟡 via ProgressRepository; needs Settings toggle |
| 92 | Universal drag-and-drop | 🟡 window accepts drops; needs multi-file + open-URL |
| 20 | Export highlights to markdown | 🟡 core export exists; needs panel entry point |

> 🟡 items above are quick wins — most of the panel polish is hours, not days.

### Phase 0.5 — Quick wins (1–2 days total)

Finish the 🟡 items. Highest value-per-hour in the whole roadmap.

| # | Item | Effort | Unblocks |
|---|------|--------|----------|
| 15a | Live-refresh the Annotations panel on `content_changed` | S | #14, #16, #29 |
| 15b | Delete/locate buttons per annotation row | S | #16 |
| 1a | Thumbnail drag-to-reorder pages | S→M | #62 |
| 2a | TOC panel: expand-state persistence + current-section highlight | S | — |
| 6a | Bookmark rename/delete in-panel + Ctrl+B label flow | S | — |
| 20a | "Export highlights" button in Annotations panel → markdown | S | #74 |
| 11a | Settings toggle: restore last page on open | S | — |
| 92a | Multi-file drop + open-URL support | S | #73 |
| 12a | Palette: "go to bookmark X" commands (top 10) | S | — |

**Total quick wins: ~1–2 days for 9 items.**

---

## Phase 1 — Reading experience (week 1)

Low-risk, high-visibility UX items that share infrastructure.

| # | Item | Effort | Unblocks |
|---|---|---|---|
| 7 | Autoscroll mode + speed control | S | #8 |
| 8 | Slideshow auto-advance | S | — |
| 10 | Two-page spread gap tuning + cover offset setting | S | — |
| 59 | Right-to-left reading order (manga mode) | S | #58 |
| 77 | Per-view themes (dark PDF, sepia EPUB) | M | #47 |
| 82 | Actionable toasts (Undo button in "Annotation deleted") | M | builds on #13 |
| 81 | Status bar: zoom %, tool, cache use, unsaved dot | S | — |
| 84 | Floating quick-toolbar near cursor while annotating | M | #24, #25 |
| 78 | Customizable toolbar (hide/show, reorder) | M | — |
| 79 | Distraction-free mode with auto-hiding chrome | S | — |

**Phase 1 exit criteria:** reader feels finished; 10 items shipped.

## Phase 2 — Annotations, part 2 (weeks 2–3)

Builds directly on the Phase 0 undo stack and panels.

| # | Item | Effort | Unblocks |
|---|---|---|---|
| 14 | Marquee multi-select + group ops | M | #16, #18 |
| 16 | Post-creation edit: move/resize/recolor existing annots | L | #30 |
| 18 | Lasso tool (freehand select of markup regions) | M | #14 |
| 30 | Per-annotation opacity/blend picker | S | — |
| 17 | Ink smoothing + tablet pressure | M | — |
| 24 | Callout annotations (arrow + boxed text) | M | — |
| 25 | Polygon/cloud shapes | M | #19 |
| 26 | File-attachment annotations | M | — |
| 27 | Audio-note annotations | L | — |
| 29 | Threaded replies on annotations | M | #74 |
| 19 | Calibrated measure tool | M | #25 |
| 21 | Signature tool: draw/import once, place anywhere | M | #67 |
| 22 | Custom stamp library | S | — |
| 23 | True redaction flow: confirm → apply → flatten | M | #70 |
| 28 | Annotation XFDF import/export | M | #74 |
| 31–36 | Sticky note cluster (colors, edge resize, tags, collapse, threads, convert) | M each | #74 |

**Phase 2 exit criteria:** annotation editing rivals desktop PDF suites.

## Phase 3 — Search & text intelligence (week 4)

| # | Item | Effort | Unblocks |
|---|---|---|---|
| 37 | Search results panel with snippets + next/prev | S | #38 |
| 38 | Incremental search-as-you-type | S | — |
| 44 | Saved searches + regex toggle | S | #95 |
| 39 | OCR text-layer indexing for scans (cached) | M | #96 |
| 41 | Dictionary popup on double-click | S | — |
| 42 | Translate selection (pluggable provider) | S | — |
| 43 | Quick-lookup side panel | M | — |
| 40 | Copy-as-markdown for selections | S | — |
| 96 | OCR invisible-text-layer export | M | — |
| 98 | Local AI helpers: summarize page/selection, ask-the-doc | L | — |

**Phase 3 exit criteria:** "find anything, understand it faster".

## Phase 4 — Rendering & performance (week 5)

| # | Item | Effort | Notes |
|---|---|---|---|
| 51 | GPU viewport (QOpenGLWidget) | M | biggest visual win; needs fallback path; do before #52 |
| 52 | Tile-based progressive rendering | L | unblocks #60; pairs with #53, #55 |
| 53 | Adaptive cache budget + cache inspector | M | pairs with #52 |
| 54 | Idle prefetch of next N pages | S | — |
| 55 | Render queue with cancellation on fast scroll | M | pairs with #52 |
| 56 | Night filters (invert/grayscale/sepia) | S | — |
| 57 | Brightness/contrast/gamma per doc | M | — |
| 58 | Long-strip mode (webtoon) | M | pairs with #59 |
| 60 | Render-DPI override for deep zoom | M | needs #52 tiles |

**Phase 4 exit criteria:** deep-zoom and huge-document performance parity with commercial viewers.

## Phase 5 — Document management & export (weeks 6–7)

| # | Item | Effort | Unblocks |
|---|---|---|---|
| 62 | Thumbnail page manager: reorder/insert/replace/delete | L | #61 (build this first) |
| 61 | Merge/split in-app, drag pages between tabs | L | — |
| 63 | Rotate/export arbitrary page ranges | S | — |
| 64 | Batch tools: watermark, headers/footers, page numbers, Bates | M | — |
| 65 | Save with compression/linearization + size preview | S | — |
| 66 | AcroForm filling with field highlighting | M | — |
| 67 | Digital signatures: verify + sign (pyhanko) | L | de-risk first; sweep already failing |
| 68 | PDF/A validation + conversion hints | M | — |
| 69 | Export pages as PNG/JPEG with DPI picker | S | — |
| 70 | One-click flattened export (annotations burned in) | M | builds on #23 |
| 71 | Region snapshot → clipboard | S | read-only (not undoable) |
| 72 | Print dialog: booklet, n-up, ranges | M | — |
| 73 | LAN share: temporary link/QR | M | builds on #92a |
| 74 | Export annotations/notes as markdown/CSV/JSON | M | builds on #20a, #28 |
| 75 | Save/load entire session as workspace | M | builds on #11 |
| 76 | Auto-save + crash recovery with backups | M | builds on #11 |

**Phase 5 exit criteria:** serious document workflow tool, not just a reader.

## Phase 6 — Platform & power (weeks 8–10)

| # | Item | Effort | Notes |
|---|---|---|---|
| 83 | Split-view scroll sync with offset lock | M | — |
| 85 | Multi-window support | L | design first; touches widget ownership everywhere |
| 86 | Touch gestures (pinch zoom, radial menu) | L | — |
| 87 | EPUB typography controls | M | — |
| 88 | EPUB search/TOC/footnote popups | M | EpubView already has a toolbar; extend it |
| 89 | Image view parity (pan/zoom/rotate/crop) | M | — |
| 90 | Comic archive (CBR/CBZ) with transitions | M | — |
| 91 | Home tab: recents with thumbnails + search | M | builds on #75 |
| 91b | Recents thumbnail rendering off the UI thread | S | — |
| 91c | Library scanner integration for Home tab | M | LibraryRepository exists; wire it |
| 97 | Headless CLI: open/annotate/export | M | cheapest integration surface; ship before plugins |
| 94 | Scripting console (Python) | L | — |
| 93 | Plugin API: toolbar buttons/panels from third parties | XL | do after Phase 2 so the surface is worth exposing |
| 95 | Watched-folder auto-ingest into library | M | builds on #44 |
| 99 | Annotation sidecar JSON (cloud-drive-safe) | M | builds on #76 |
| 100 | Reading-stats dashboard | M | builds on #9; persist duration events |

**Phase 6 exit criteria:** platform, plugins, EPUB/comic parity, stats.

---

## Dependency highlights (what unblocks what)

- **#13 undo/redo (shipped)** → #14, #16, #18, #82, and all Phase 2 editing tools. No destructive tool should land before this.
- **#51 GPU viewport** → amplifies every rendering item; do before #52.
- **#52 tile rendering** → #60 crisp deep zoom; pairs with #53, #55.
- **#15a annotations panel live-refresh (quick win)** → #14, #16, #29 (replies need a panel to live in).
- **#62 page manager** → #61 merge/split UX; both need the thumbnail panel to support drag.
- **#11 session restore (🟡)** → #75 workspace save/load and #76 crash recovery extend it.
- **#97 CLI** → ship before #93 plugins; it is the cheapest integration surface and validates the API.

## Riskiest / de-risk first

1. **#27 audio-note annotations** — media framework + PDF embedded-file spec; spike first.
2. **#67 digital signing (pyhanko)** — already failing in the feature sweep; needs dependency work before UI.
3. **#85 multi-window** — touches tab/widget ownership everywhere; design before building smaller items on top of single-window assumptions.
4. **#93 plugin API** — stable internal API surface needed; do after Phase 2 so the surface is worth exposing.

## Suggested cut lines

- **MVP+ (2 weeks):** Phase 0.5 quick wins + Phase 1 — a reader that feels *finished*.
- **Annotator's cut (4 weeks):** + Phase 2 — editing cluster; hardest but most differentiating.
- **Pro cut (7–8 weeks):** + Phases 3–5 — search intelligence, performance, document management.
- **1.2 (10–12 weeks):** + Phase 6 — platform, plugins, EPUB/comic parity, stats.

---

## Verification plan (per phase)

Every phase ends with:
1. `python -m compileall src` clean.
2. `pytest tests/test_ui_smoke.py` green.
3. `scripts/feature_sweep.py` — no new failures vs. baseline (currently 20 known
   pre-existing failures: vault/pyhanko/TTS/settings/tasks/SplitView).
4. New unit tests for each new pure-logic piece (stacks, parsers, geometry).
   UndoStack/nav-history runtime tests exist as one-off scripts; promote them to
   `tests/` during Phase 0.5.
5. A short entry in `CHANGELOG.md` per shipped phase.

## Appendix: numbering sanity check

The 100-item list was written as one big list in an earlier turn; this roadmap
re-derives the mapping from the actual codebase. Several items already exist
(some shipped this session, some pre-existing app scaffolding):

- Thumbnails (#1), TOC (#2), bookmarks (#6), annotations panel (#15), notes panel,
  search panel, tasks panel, properties panel — dockable panels exist.
- Command palette (#12) with Open/Save/Print/OCR/Merge/Split/Compare/Vault/Settings commands.
- Reading progress persisted per document (#9/#11 partial).
- Go-to-page (#3), reading history (#5), undo/redo (#13) — shipped this session.
- Drag-drop open (#92 partial).
- TTS read-aloud (#45-adjacent), OCR (#39-adjacent), translate/dictionary/summarize
  dialogs (#41–43 adjacent, real dialogs exist).
- Merge/split (#61/#62 partial: engine methods exist, UI is dialog-based not drag-based).
- Forms (#66 partial: form_fields/set_field_value exist, no highlighting UI).
- Signatures (#67 partial: inspection exists, signing needs pyhanko).
