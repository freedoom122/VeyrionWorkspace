# Veyrion Workspace — User Guide

**READ. EDIT. ORGANIZE. CREATE.**

This guide walks through the day-to-day use of Veyrion Workspace. The command
palette (`Ctrl+K`) can jump to almost everything mentioned here — type the
feature name instead of hunting through menus.

---

## 1. First launch

The first time you start Veyrion Workspace you'll see a short onboarding
screen. Everything on it is optional:

- **Theme** — Light, Dark, OLED, Sepia, or High Contrast (change anytime in
  Settings → Appearance).
- **Document folders** — pick folders to import into the Library and watch
  for changes.
- **OCR** — optionally enable OCR now; if Tesseract isn't installed yet,
  you can enable it later in Settings → OCR.
- **Reading defaults** — default PDF mode, zoom fit, and EPUB typography.

You can skip onboarding entirely; nothing is required.

## 2. Opening documents

| Method                      | How                                              |
| --------------------------- | ------------------------------------------------ |
| File → Open (`Ctrl+O`)      | Browse for any supported file                    |
| Drag & drop                 | Drop files anywhere on the window                |
| Double-click a file         | If the installer registered file associations    |
| Library panel              | Double-click an item, or right-click → Open      |
| Command line               | `VeyrionWorkspace.exe file.pdf file2.epub`          |

Each file opens in its own tab. Supported: PDF, EPUB/EPUB3, MOBI/AZW/AZW3,
CBZ/CBR, DOCX, ODT, RTF, TXT, MD, HTML, CSV, JSON, XML, XLSX, PPTX, and
PNG/JPEG/WEBP/BMP/TIFF/GIF images.

If a file can't be opened, the app says why and, when a converter is
available, offers conversion instead.

### Password-protected PDFs
If a PDF asks for a password, type it in the prompt. Passwords are not
remembered unless you explicitly enable that in Settings → Privacy.

## 3. The workspace

- **Top:** document tabs, global search, command palette button, window
  controls.
- **Center:** the document — the hero of the window.
- **Panels** (dockable): Library, Thumbnails, Table of Contents, Annotations,
  Notes, Search Results, Properties, Bookmarks, Page Navigator.
- **Bottom:** page number/count, zoom slider, reading progress, background
  task indicator.

Panels open from the **View → Panels** menu. Drag panel titles to dock them
where you like; layout is remembered. Toggle any panel with its menu item.

### Split view
View → Split (or `Ctrl+Alt+V`) splits the workspace: two documents, the
same document twice, or a document next to a note/search panel. Drag the
divider to resize. Optional synchronized scrolling for side-by-side reading.

## 4. Reading PDFs

### View modes
View → PDF Mode:
- **Single page** — one page at a time.
- **Continuous** — pages flow in one scroll (default).
- **Two-page** — spreads like a printed book.
- **Two-page with cover** — first page alone, then spreads.

### Zoom
- `Ctrl +` / `Ctrl -` to zoom in/out, or the status-bar slider.
- Fit width (`0`), fit page, fit height from the View menu.
- Range: 50% – 800%.

### Navigation
- Scroll, `PgUp`/`PgDn`, or type a page number in the status bar.
- **Thumbnails panel** — jump and reorder by dragging.
- **Page scrubber** — drag the progress area to fly through pages.
- **Presentation mode** — View → Presentation (fullscreen, transitions,
  timer). `Esc` exits.

### Search inside a PDF
`Ctrl+F` finds text on the current document and highlights every hit; the
find bar shows hit counts and navigates hit by hit.

## 5. Annotating

Select a tool on the **Annotations** toolbar, then draw on the page:

| Tool        | What it does                            |
| ----------- | --------------------------------------- |
| Highlight   | Drag across text                        |
| Underline / Strikeout / Squiggly | Text markup           |
| Note        | Click to drop a sticky note             |
| Ink         | Freehand drawing                        |
| Rect / Ellipse / Line / Arrow | Shapes                     |
| Text box    | Click and type                          |
| Stamp       | Add a "Reviewed"-style stamp            |

Customize color, opacity, thickness, and font in Settings → Annotations or
from the toolbar controls. Annotations are stored **inside the PDF** as
standard PDF annotations, so they travel with the file.

**Annotation manager:** the Annotations panel lists every annotation across
documents — filter by type/color, search, edit text, delete, and export to
Markdown, HTML, CSV, JSON, or PDF.

## 6. Editing PDFs

The Edit menu is real editing, not cosmetics:

- **Pages** — rotate, delete, extract, duplicate, reorder (drag in
  thumbnails), insert blank, insert from another PDF, merge, split.
- **Text** — insert text, and where structure allows, edit or remove it.
- **Redact** — mark an area (Edit → Redact), then **Apply Redactions**.
  This *removes the content*, not just paints over it. Use **Verify
  Redaction** afterwards to reopen the saved file, search the phrases, and
  get a report. Redaction is one-way — snapshot/version history can't
  restore redacted bytes in the new file, so keep the original if you might
  need it.
- **Watermarks / headers / footers / Bates numbering** — Edit → Watermark,
  with position, size, and content options.
- **Forms** — Edit → Forms: fill text fields, check boxes, radios, combo
  and list boxes; validate, clear, or reset.
- **Metadata** — Edit → Metadata: title, author, subject, keywords; edit
  page labels.
- **Sign** — Tools → Sign shows existing signature fields with certificate
  details. A visual signature image is *not* a cryptographic signature —
  the dialog makes the distinction explicit.

Every edit is protected: version history snapshots the file before each
save (default 5 versions, Settings → General), and saves are atomic — a
failed save never corrupts the original.

## 7. The Library

The Library panel manages your documents:

- **Import** — Library → Import Folder (or drag a folder in). Imported
  folders can be watched so new files appear automatically.
- **Views** — grid, list, compact, cover, table (toolbar or Settings →
  Library).
- **Organize** — tags, collections, favorites, ratings (right-click an
  item).
- **Reading progress** — a progress bar per item; the library sorts by
  "last opened" by default.
- **Filter & sort** — by format, folder, tag, author, rating, read/unread,
  annotated, recently opened/modified.
- **Batch operations** — multi-select with `Ctrl`/`Shift` then right-click:
  tag, add to collection, delete from library (not from disk), reveal in
  Explorer, export.

The Library is also the search engine's index: imported documents get their
text indexed in the background.

## 8. Global search

Type in the **global search bar** (top of the window) to search all indexed
documents at once. Options in Settings → Search:

- Case sensitive, whole word, regex, fuzzy, phrase search.
- Scope: text, metadata, annotations, bookmarks, notes.

Results show document, page, snippet, and match count; click to jump
straight to the source. Search runs in the background and never freezes the
UI. `Ctrl+F` inside a document searches that document only.

## 9. OCR

Scanned PDFs (no text layer) can be made searchable:

1. Tools → OCR.
2. Choose scope: **current page**, **selected pages**, or **entire
   document**.
3. Pick the language (auto-detect or manual), then run.

OCR runs in the background with progress and cancel. The result can be
saved as a searchable PDF. OCR needs the Tesseract engine — if it's not
found, the menu explains how to enable it (Settings → OCR shows the engine
path).

## 10. E-books & comics

### EPUB
Beautiful reflowable reading: font, size, line height, paragraph spacing,
margins, width, alignment, and themes (light/dark/sepia/OLED/custom) from
the **A** typography toolbar. TOC, footnotes, and internal links work;
reading position is remembered. RTL books render right-to-left.

### EPUB editing
Open an EPUB and use the editor toolbar: metadata, cover, chapters, TOC,
HTML and CSS editing, chapter reorder/create/delete. Structure is validated
before export.

### MOBI / AZW
Read-oriented extraction with metadata and table of contents.

### Comics (CBZ/CBR)
Single or double-page modes, RTL manga mode, page thumbnails, zoom and fit
options. Large pages are downscaled for display automatically.

## 11. Markdown & office files

### Markdown
Edit with syntax highlighting, live preview, or split view. Find/replace,
export to PDF/HTML. Headings, lists, tables, code blocks, links, images,
quotes, checklists all supported.

### DOCX
View and edit paragraphs, styles, bold/italic/underline, lists, tables, and
images. Formatting you don't touch is preserved on save (the original
package parts are kept). Comments are supported where feasible.

### XLSX / PPTX / ODT / RTF
High-quality viewing and preview with export; full Office-grade editing of
these formats is not claimed.

## 12. Read Aloud, dictionary, translation

- **Read Aloud** (Tools → Read Aloud): play, pause, stop, previous/next
  sentence, speed 0.5×–3×, voice and volume. Runs in the background — keep
  working while it reads.
- **Dictionary**: select text → right-click → Dictionary. Offline
  definitions with part of speech and examples. No account needed.
- **Translate**: select text → right-click → Translate. Uses offline
  engines when available; if not, you'll be asked explicitly before any
  online translation. Nothing is ever sent over the network without your
  action — and Offline Mode (Settings → Privacy) blocks even that.

## 13. Presentation mode

View → Presentation: fullscreen slides of your PDF with page transitions
(fade/slide/none), a timer, and navigation controls. Laser-pointer-style
spotlight is available for pointing during presentations.

## 14. Comparison

Tools → Compare: pick two documents (PDF, DOCX, or text) and get a
page-by-page diff. Lines added/removed/changed are shown per page with
similarity. Comparison is line-based — it's honest "what text changed where",
not a semantic essay.

## 15. Conversion & export

File → Export opens a central dialog. What you get depends on the document:

- **PDF**: save as images, extract text/Markdown/HTML, export annotations,
  compress/optimize.
- **Text/Markdown**: export to PDF, HTML.
- **Images**: convert to PDF.
- **CSV**: render as a PDF table.
- **Any document**: export metadata, notes.

Exports never overwrite originals silently — if the target exists you're
asked first, and output is verified.

## 16. Printing

File → Print: page range, copies, orientation, duplex, multiple pages per
sheet, booklet where the printer supports it, and annotations on/off. A
print preview shows exactly what will be printed.

## 17. The Vault

Tools → Vault protects sensitive documents:

1. Create the vault with a strong passphrase (the vault is created
   unlocked — lock it when you're done).
2. Add files (they're encrypted with AES-256-GCM before touching disk).
3. Extract or remove items; change the passphrase anytime.
4. Auto-lock after a configurable idle time (Settings → Security), or lock
   manually.

The passphrase is never stored. Forgetting it means the vault contents are
permanently unrecoverable — that's the point.

## 18. Privacy tools

Tools → Privacy:

- **Metadata inspector** — see exactly what metadata a file carries (PDF
  info, EXIF, XMP) before touching anything.
- **Strip metadata** — create a sanitized copy with personal metadata
  removed.
- **Redaction verification** — the honest report described in §6.
- **Secure delete** — overwrite-then-delete for files you're discarding
  (see SECURITY.md for its limits on journaled filesystems).

## 19. Plugins

Tools → Plugins manages installed plugins:

- Plugins declare the permissions they need (files, network, clipboard,
  documents).
- You approve each plugin before any of its code runs — approval is
  revocable.
- Suspicious or invalid plugins are listed as disabled with the reason.

A sample word-count plugin ships with the app; see `plugins/` for its
source and how to write your own.

## 20. Settings

Settings (`Ctrl+,`) covers: General, Appearance, Reading, PDF, EPUB,
Annotations, OCR, Speech, Library, Search, Security, Privacy, Performance,
Keyboard, Updates, and Data (shows where everything lives: database, cache,
logs, backups, plugins).

- Reset any category, or restore all defaults.
- **Keyboard**: remap shortcuts by pasting a JSON map. *Edit Shortcut
  Overrides File…* creates and opens `shortcuts.json` in your settings
  folder — fill in the actions you want to change, then *Reload Shortcut
  Overrides* applies it immediately (no restart, and the F1 sheet follows
  along). Vim-style navigation (`j`/`k`/`gg`/`G`) is a separate toggle here.
- **Accessibility**: large UI, large text, dyslexia-friendly font, reduced
  motion, high contrast — in Appearance.
- **Performance**: page cache size, thumbnail workers, prefetch distance.
- **Privacy**: Offline Mode, update checks, external-link behavior.

## 21. Recovering from problems

- **Crash recovery:** if the app closes unexpectedly, it detects the
  unfinished session on next launch and offers to restore your tabs,
  scroll positions, and unsaved edits. You can dismiss recovery safely.
- **Autosave:** working copies are written every 120 s (configurable) so
  long editing sessions aren't lost; your original is untouched until you
  save.
- **Version history:** File → Version History rolls a document back to any
  of its recent snapshots. Rollback is itself snapshotted, so it's
  reversible too.
- **Settings corruption:** a damaged settings file is backed up and
  defaults restored, with a notice.

## 22. Keyboard reference

| Keys            | Action                            |
| --------------- | --------------------------------- |
| `Ctrl+O`        | Open file                         |
| `Ctrl+K`        | Command palette                   |
| `Ctrl+F`        | Find in document                  |
| `Ctrl+S` / `Ctrl+Shift+S` | Save / Save As          |
| `Ctrl+P`        | Print                             |
| `Ctrl+Z` / `Ctrl+Y` | Undo / redo                  |
| `Ctrl+B/I/U`    | Bold / italic / underline         |
| `Ctrl+,`        | Settings                          |
| `Ctrl+Tab`      | Next tab                          |
| `F1`            | Keyboard shortcuts cheat sheet     |
| — (click status) | Reading summary (export/print)   |
| `F5`            | Presentation mode                 |
| `F8` / `F9`     | Distraction-free / focus mode     |
| `F11`           | Fullscreen                        |
| `+` / `-`       | Zoom in / out                     |
| `0`             | Fit width                         |
| `PgUp` / `PgDn` | Previous / next page              |
| `j` `k` `gg` `G`| Vim navigation (optional)         |

Press `F1` for the complete list: it is generated from the app's own menus,
so it always matches the build you are running. *Export…* writes whatever is
currently shown as a printable PDF card, a CSV table (handy for spreadsheets),
or a Markdown table you can paste straight into documentation, *Copy as
Markdown* puts that same table on the clipboard without writing a file, and
*Print…* shows a preview of the exact card first — cancelling sends nothing,
approving sends it to your printer.

The annotations panel, the reading summary, and the library panel (its export
button exports the selected documents — or the whole filtered view when nothing
is selected — with pages, size, progress, rating and tags) all export and
print the same way, so those are the places to look whenever you want a record
of what is on screen. Rows whose key you have remapped are marked as custom, and
the status line reports anything a pasted key map got wrong (an unknown action
id, an unreadable key, or a binding that now clashes with another action).

## 23. Data locations

| What         | Windows                          |
| ------------ | -------------------------------- |
| App data     | `%APPDATA%\VeyrionWorkspace`        |
| Database     | `...\database\veyrion.db`     |
| Cache        | `...\cache`                      |
| Logs         | `...\logs`                       |
| Backups      | `...\backups`                    |
| Plugins      | `...\plugins`                    |

See Settings → Data for exact paths (they adapt to Linux/macOS).

## 24. Uninstalling

Use the installer's uninstaller. You'll be asked whether to keep your
documents, library, and settings (recommended) or remove them too. Files
you imported are never deleted from disk.