# Veyrion Workspace

**READ. EDIT. ORGANIZE. CREATE.**

Veyrion Workspace is a local-first desktop document workspace: a PDF reader and
editor, e-book reader, comic viewer, annotation workspace, OCR tool, document
library, conversion utility, and secure document vault — in one application.

It is designed to feel like a serious desktop product, not a web page in a
window: document-first composition, dockable panels, keyboard-first command
palette, and a deliberately restrained visual identity.

---

## Highlights

- **Read** PDF, EPUB/EPUB3, MOBI/AZW/AZW3, CBZ, CBR, DOCX, ODT, RTF, TXT, MD,
  HTML, CSV, JSON, XLSX, PPTX, and common image formats (PNG, JPEG, WEBP, BMP,
  TIFF, GIF).
- **Edit** PDFs: insert/remove text, redact (true content removal), page
  operations (rotate, delete, extract, reorder, merge, split), watermarks,
  headers/footers, Bates numbering, and more.
- **Annotate** with standard PDF annotations: highlight, underline, strikeout,
  squiggly, sticky notes, freehand ink, shapes, text boxes, stamps — persisted
  directly into the PDF.
- **Search** everything with a local SQLite FTS5 index: document text,
  metadata, annotations, and notes, with case/whole-word/regex/fuzzy modes.
- **Organize** a document library with folders, tags, collections, ratings,
  smart collections, reading progress, and multiple views (grid, list, cover,
  table).
- **Convert** between formats: PDF ⇄ text/Markdown/HTML/images, text →
  PDF, images → PDF, CSV → PDF tables, and more.
- **Protect** documents with a local encrypted vault (AES-256-GCM, PBKDF2),
  metadata/privacy stripping, and PDF redaction verification.
- **Listen** — built-in text-to-speech with speed/voice control.
- **Offline-first**: no telemetry, no tracking, no account, no cloud. A global
  offline mode blocks network features entirely.

## System requirements

- Windows 10/11 (64-bit) — the primary supported platform
- Linux and macOS are architecturally supported (platform abstraction layer)
- Python 3.11+ for running from source; packaged builds need no Python

## Installation

### Packaged build

Download the one-time installer from the
[releases page](https://github.com/freedoom122/VeyrionWorkspace/releases/latest)
and run `VeyrionWorkspace-Setup-1.1.0.exe`. It installs to
`%LOCALAPPDATA%\VeyrionWorkspace`, creates Start Menu and desktop shortcuts, and
requires no admin rights. A `.sha256` checksum file is published next to the
installer for verification. No terminal is required.

> **Upgrading from the former name.** If you have run the earlier
> `OmniReaderPro` build, Veyrion Workspace carries your library, settings,
> vault, and session journal into `%APPDATA%\VeyrionWorkspace` on first launch,
> renaming the old database and log files as it goes. Your original folder is
> left untouched, and the installer removes the previous install directory and
> its shortcuts so you never end up with two copies installed side by side.

Optional runtime components (detected automatically, not bundled):

| Component          | Used for                          | Notes                          |
| ------------------ | --------------------------------- | ------------------------------ |
| Tesseract OCR      | OCR of scanned pages              | Add its install dir to `PATH`  |
| unrar / 7-Zip      | RAR-based comic archives (.cbr)   | Used if installed              |
| Calibre tools      | MOBI/AZW conversion fallbacks     | Used if installed              |

### From source

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -r requirements.txt
python run_veyrion.py          # or double-click run_veyrion.pyw
```

On Linux: `source .venv/bin/activate`; install Tesseract via your package
manager for OCR.

### Building the packaged app

```powershell
.\scripts\build_windows.ps1       # generates icons + runs PyInstaller
# Installer (optional, needs Inno Setup):
ISCC.exe installer\veyrion_setup.iss
```

See `packaging/veyrion.spec` for the PyInstaller configuration (windowed,
no console, app icon, versioned output).

## Quick start

1. **Onboarding** — the first launch walks you through theme, folders, OCR,
   and reading defaults. All optional, all skippable.
2. **Open a document** — `Ctrl+O`, drag & drop, double-click an associated
   file, or browse the Library.
3. **Command palette** — `Ctrl+K` searches every action, recent file, and
   setting. Every important feature is reachable there.
4. **Library** — add folders with `Library → Import Folder`; the app indexes
   text in the background so search works across everything.
5. **Annotate** — pick a tool on the Annotations toolbar and draw directly on
   the page; annotations are saved into the PDF.

## Feature map

| Area                | Where to look                                                     |
| ------------------- | ----------------------------------------------------------------- |
| PDF reading         | View menu: single/continuous/two-page modes, zoom, rotation       |
| PDF editing         | Edit menu: page ops, redact, watermark, header/footer, Bates      |
| Annotations         | Annotations toolbar + Annotations panel (manage, export)          |
| Forms & signatures  | Edit menu → Forms; Tools → Sign                                   |
| OCR                 | Tools → OCR (current page, selection, or whole document)          |
| Library             | Library panel (grid/list/cover/table, tags, collections)          |
| Search              | Global search bar; `Ctrl+F` inside a document                     |
| Compare             | Tools → Compare                                                   |
| Presentation        | View → Presentation (`F5`-style fullscreen)                       |
| Text-to-speech      | Tools → Read Aloud                                                |
| Dictionary          | Select text → right-click → Dictionary (built-in wordnet source)  |
| Translation         | Select text → right-click → Translate (offline when available)    |
| Vault               | Tools → Vault (AES-256-GCM encrypted document storage)            |
| Privacy             | Tools → Privacy (metadata stripping, redaction verify)            |
| Settings            | `Ctrl+,` or File → Settings                                       |
| Plugins             | Tools → Plugins (permission-gated Python plugins)                 |

## Supported formats

**Read & view:** PDF, EPUB, EPUB3, MOBI, AZW, AZW3, CBZ, CBR, DJVU*,
XPS/CHM*, DOCX, DOC*, ODT, RTF, TXT, MD, HTML, XML, CSV, JSON, SVG*,
PNG, JPG, JPEG, WEBP, BMP, TIFF, GIF, PPTX, XLSX.

\* DJVU, XPS, CHM, DOC, and SVG require an external converter when available;
the app detects them and offers conversion rather than pretending.

**Edit:** PDF (pages, text, redaction, annotations, forms, metadata,
watermarks, Bates), DOCX (paragraphs, styles, tables), EPUB (metadata, cover,
chapters, TOC), Markdown, plain text.

**Convert:** PDF → text/Markdown/HTML/images; text → PDF; images → PDF;
CSV → PDF; DOCX → Markdown; annotations → Markdown/HTML/CSV/JSON/PDF.

## Keyboard shortcuts

| Shortcut        | Action                       |
| --------------- | ---------------------------- |
| `Ctrl+O`        | Open file                    |
| `Ctrl+K`        | Command palette              |
| `Ctrl+F`        | Find in document             |
| `Ctrl+S`        | Save (`Ctrl+Shift+S` as)     |
| `Ctrl+P`        | Print                        |
| `Ctrl+Z` / `Y`  | Undo / redo                  |
| `Ctrl+B/I/U`    | Bold / italic / underline (text & Markdown) |
| `Ctrl+,`        | Settings                     |
| `F11`           | Fullscreen                   |
| `Ctrl+Tab`      | Next tab                     |
| `+` / `-`       | Zoom in / out                |
| `0`             | Fit width                    |
| `PgUp`/`PgDn`   | Page navigation              |
| `j`/`k`, `gg`, `G` | Vim-style navigation (Settings → Keyboard, opt-in) |

Press `F1` for the live cheat sheet of every action and its key, exportable as
a printable PDF card, a CSV table, or a Markdown table for pasting into docs —
or copied straight to the clipboard as Markdown, and printable — with a preview
of the exact card before anything is sent. The
annotations list, the reading summary (click the progress segment in the status
bar) and the library's document selection export, copy and print the same way.
To remap anything, paste a JSON key map into
`shortcuts.json` (Settings → Edit Shortcut Overrides File…) and pick Settings →
Reload Shortcut Overrides — the cheat sheet updates to match, unknown ids and
unreadable keys are reported, and deleting an entry restores the built-in key.
Vim-style navigation stays a toggle in Settings → Keyboard.

## Architecture

```
src/veyrion_workspace/
  app/          bootstrap, paths, logging
  core/         document engines, annotations, search, ocr, tts, security,
                conversion, comparison, printing, plugins, updates
  services/     settings, task manager, recovery journal, file watcher
  storage/      SQLite (WAL) database, migrations, repositories
  ui/           main window, readers, editors, panels, dialogs, theming
  utils/        safe I/O, path validation
tests/          unit + integration + UI smoke suites (pytest)
```

Design principles:

- **Local-first & private.** All data lives on your machine. No telemetry.
  Network use only for explicit user-initiated features, gated by offline
  mode.
- **Documents are untrusted.** Path validation, archive traversal guards,
  zip-bomb limits, controlled temp dirs, and no automatic code execution from
  documents.
- **Never corrupt the original.** Every save goes through a temp file,
  fsync, then atomic replace; version history (default 5) is kept per
  document; the session journal enables crash recovery.
- **Background work never blocks the UI.** OCR, indexing, thumbnails,
  conversion, and comparison run on a task manager with progress and cancel.
- **Honest security.** Redaction removes content and can verify removal —
  with documented format limitations. The vault uses AES-256-GCM. No
  overclaiming, ever.

See `SECURITY.md` for the threat model and `DECISIONS.md` for the
engineering decisions log.

## Troubleshooting

| Problem                          | Fix                                                                 |
| -------------------------------- | ------------------------------------------------------------------- |
| OCR menu disabled                | Install Tesseract and add it to `PATH` (Settings → OCR shows engine path) |
| `.cbr` comics won't open         | Install `unrar` (Windows: add to PATH) — fallback is archive listing |
| App data location                | `%APPDATA%\VeyrionWorkspace` (Windows), `~/.local/share/VeyrionWorkspace` (Linux), `~/Library/Application Support/VeyrionWorkspace` (macOS) |
| Logs                             | `Help → Diagnostics → Open Logs Folder`                              |
| A file won't open                | The error dialog states why; unknown formats offer conversion        |
| Crash on startup                 | Check `logs/`; delete `settings.json` to restore defaults (a backup is kept automatically) |

## Tests

```bash
.venv\Scripts\python -m pytest tests/
```

The suite is hermetic: sample documents are generated on the fly with the
same libraries the app ships, and `VEYRION_DATA_DIR` redirects all app
data to a temp dir so the real user database is never touched.

## License

Veyrion Workspace is licensed under the MIT License (see `LICENSE`).
Third-party dependencies and their licenses are listed in
`THIRD_PARTY_LICENSES.md`.