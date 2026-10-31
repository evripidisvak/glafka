# Grammata: Specification (draft 3)

A lightweight, GNOME-native ebook library manager. *Grammata* (Greek: "letters, writings").

- Crate / binary name: `grammata`
- App ID: `io.github.evripidisvak.Grammata`
- License: GPL-3.0-or-later

Status and task breakdown: see `PLAN.md`.

## Goals
- Manage an ebook library: import, browse, search, edit metadata.
- Send books to an e-reader by USB and by email.
- Convert books, most importantly PDF to EPUB.
- Native GTK4 + libadwaita UI following the current GNOME HIG.
- Standalone: no Calibre required. Calibre is an optional enhancer.

## Non-goals (v1)
Built-in reader, RSS/news, content server, plugins, DRM handling (never), OCR for scanned PDFs.

Deferred to v1.1: online metadata lookup, built-in (pure Rust) PDF to EPUB conversion.

## Target user
Fedora/GNOME user, mostly EPUB plus some PDF, one e-reader (first target: PocketBook Era Lite), existing Calibre library.

## Features

### 1. Library
- Library folder chosen by the user on first run (default `~/Books/Grammata`), changeable in Preferences. Changing it opens or creates a library at the new location; books are not moved.
- Layout: `<library>/Author/Title/Title.ext` plus `cover.jpg|png`. SQLite index in hidden `<library>/.grammata/library.db`.
- File paths are stored relative to the library root, so the library folder can be moved or backed up as a whole.
- One book entry, many formats: a book (metadata, cover, tags) links one file per format (e.g. EPUB and PDF).
- Import by drag-and-drop, file picker, or whole folder. Imports run in the background with progress.
- Formats: EPUB (full metadata), PDF (basic metadata), others stored with filename-derived metadata.
- Duplicate detection by content hash, then title+author. A title+author match in a new format is attached to the existing book. An import never overwrites an existing file.
- Import from an existing Calibre library by walking its folders and reading each `metadata.opf` and `cover.jpg`. Calibre's `metadata.db` is never read or written. Source library is left untouched.

### 2. Browse and search
- Cover grid, with a list-view toggle.
- Sidebar: All, Recently added, Authors, Series, Tags, Sent to reader.
- Grouped categories: Authors, Series and Tags separate the books into one section per group, with a header showing the group name (not just a sort order). Series sections are ordered by series index. A book with several tags appears under each of its tags.
- Instant search over title, author, series.
- Sort by title, author, date added, series index.

### 3. Metadata
- Read from EPUB OPF: title, authors, series, index, language, description, tags, cover.
- Edit in a details pane; write back to the file (EPUB) and the index.
- When title or author change, the book's folder and files are renamed to match.
- Series index: whole numbers, unique within a series. 0 in the editor means no index. Fractional values in source metadata are truncated (1.5 becomes 1). On import, a book whose series slot is already taken keeps its series but loses the index.

### 4. Send to reader
- Reader profiles: name, type (USB / email), address, preferred format. Generic: any USB mass-storage reader, any send-to-email address.
- USB first: detect mounted readers via `gio::VolumeMonitor`, offer "Send to <profile>", copy to the device's books folder.
- Email: SMTP with Gmail and Outlook presets, app password in the system keyring (Secret Service). For PocketBook, use its Send-to-PocketBook cloud address; the sender address must be approved in the PocketBook account (verify current instructions at build time).
- Toasts for queued/sent/failed; a sent-history log.
- Warn when a book's format is unsupported by the reader.

### 5. Convert
Pluggable backends behind a `Converter` trait in the core crate. The app picks the best available backend per format pair.

| Conversion | Built-in (pure Rust) | Optional `ebook-convert` |
|---|---|---|
| TXT / HTML / Markdown / FB2 to EPUB | yes | yes |
| PDF (text-based) to EPUB | v1.1, best effort | yes (the v1 path) |
| MOBI / AZW3 / others | no | yes |

Built-in PDF to EPUB (v1.1):
- Extract text with positions and font info.
- Remove repeated headers, footers and page numbers.
- Join lines into paragraphs; fix hyphenation.
- Detect headings from font size; build chapters and a table of contents.
- Detect code blocks by monospace font; preserve indentation from x-offsets; emit `<pre><code>`.
- Tables and figures: best effort, may degrade. Multi-column layouts are not supported.
- Scanned (image-only) PDFs are detected and rejected with a clear message.

Behavior rules:
- If `ebook-convert` is missing, the UI says what is unavailable and why, instead of failing.
- For technical PDFs the UI notes that the PDF itself may read better on the device.
- Conversion runs in the background with progress; the original file is never modified.

## UI (GNOME HIG)
- `AdwNavigationSplitView`: sidebar, content, details (collapses on narrow windows).
- `GtkGridView` with list model for covers; thumbnails cached on disk and loaded asynchronously.
- `AdwToastOverlay`, `AdwPreferencesDialog`, `AdwStatusPage` for the empty state.
- Follows system light/dark and accent color.
- UI is built in Rust code, not Blueprint. May move to Blueprint widget by widget later.
- Errors are shown to the user (toast or status page), never only logged.

## Architecture
Cargo workspace, with a Meson wrapper so GNOME Builder can build and run it:
- `core`: no GTK dependency. Modules: `library`, `metadata`, `import`, `convert`, `send`.
- `ui`: GTK4/libadwaita app, thin layer over `core`.
- Long-running work (import, hashing, conversion, sending) runs off the main thread.
- Database schema is versioned with `PRAGMA user_version`; every schema change ships a migration.

Stack: `gtk4-rs`, `libadwaita-rs`, `rusqlite`, `zip` + `quick-xml`, `lettre`, `oo7`, `epub-builder`; for v1.1, a PDF text-extraction crate (to be evaluated: `pdfium-render`, `lopdf`, `pdf-extract`).

## Open decisions
- Distribution: plain `cargo install` first; Flatpak later (needs removable-media permission for USB; `ebook-convert` backend unavailable in sandbox unless via `flatpak-spawn --host`).
- PDF library choice (v1.1): pick after a spike comparing font and position data quality. Avoid AGPL dependencies (e.g. MuPDF) unless the app itself will be AGPL.
- Name availability: confirm `grammata` on crates.io, Flathub and GitHub before publishing.

## Milestones
1. Workspace skeleton and empty libadwaita window.
2. `core`: SQLite library, EPUB metadata reader, import (files and Calibre library).
3. UI: grid, sidebar with grouped categories, search, details pane.
4. Send by USB, then by email.
5. Converters: `ebook-convert` backend, built-in text formats.
6. Polish: empty states, error messages, packaging.
