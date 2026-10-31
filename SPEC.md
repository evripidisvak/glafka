# Glafka: Specification (draft 3)

A lightweight, GNOME-native ebook library manager. *Glafka*: γλαύκα, the owl, bird of wisdom; spelled the way Greek says it (rhymes with Kafka). Called Grammata until 2026-10-31.

- Package / command name: `glafka`
- App ID: `io.github.evripidisvak.Glafka`
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

Deferred to v1.1: online metadata lookup, built-in (pure Python) PDF to EPUB conversion.

## Target user
Fedora/GNOME user, mostly EPUB plus some PDF, one e-reader (first target: PocketBook Era Lite), existing Calibre library.

## Features

### 1. Library
- Library folder chosen by the user on first run (default `~/Books/Glafka`), changeable in Preferences. Changing it opens or creates a library at the new location; books are not moved.
- Layout: `<library>/Author/Title/Title.ext` plus `cover.jpg|png`. SQLite index in hidden `<library>/.glafka/library.db`.
- File paths are stored relative to the library root, so the library folder can be moved or backed up as a whole.
- One book entry, many formats: a book (metadata, cover, tags) links one file per format (e.g. EPUB and PDF).
- Import by drag-and-drop, file picker, or whole folder. Imports run in the background with progress.
- Formats: EPUB (full metadata), PDF (title and first author from the document info, first page as cover; via poppler-utils `pdfinfo`/`pdftoppm`; junk titles like "Microsoft Word - x.docx" fall back to the file name), others stored with filename-derived metadata.
- Duplicate detection by content hash, then title+author. A title+author match in a new format is attached to the existing book. A title+author match in a format the book already has is skipped and reported.
- An import never overwrites an existing file. A new book whose folder name is already taken gets a numbered suffix: `Title (2)`.
- Preference "Move Files Into the Library" (off by default): after a successful import the original goes to the Trash, also when an identical copy was already in the library. Never for skipped (different edition) or failed files, Calibre libraries, or files that are already inside the library.
- A book without a cover gets a generated cover: title and author on a tile tinted per book.
- Import from an existing Calibre library by walking its folders and reading each `metadata.opf` and `cover.jpg`. Calibre's `metadata.db` is never read or written. Source library is left untouched.

### 2. Browse and search
- Cover grid, with a list-view toggle.
- Sidebar: All, Recently added, Authors, Series, Tags, On reader (books on a USB reader at its last check; their covers carry a small reader badge).
- Grouped categories: Authors, Series and Tags separate the books into one section per group, with a header showing the group name (not just a sort order). Series sections are ordered by series index. A book with several tags appears under each of its tags.
- Instant search over title, author, series.
- Sort by title, author, date added, series index.

### 3. Metadata
- Read from EPUB OPF: title, authors, series, index, language, description, tags, cover.
- Edit in a details pane; write back to the file (EPUB) and the index. Only the package document (OPF) changes, in place and atomically; DRM-protected EPUBs are never modified. The hash of each file as imported is kept, so the original download is still recognised as already in the library.
- When title or author change, the book's folder and files are renamed to match.
- Series index: whole numbers, unique within a series. 0 in the editor means no index. Fractional values in source metadata are truncated (1.5 becomes 1). On import, a book whose series slot is already taken keeps its series but loses the index.

### 4. Send to reader
- Preferences → Sending → "Send Books To": the ways books reach a reader, each a USB drive or an email address, with a name (shown on its button) and preferred formats. Editable; the drive of a USB entry stays as added. Generic: any USB mass-storage reader, any send-to-email address. (In the code: `Destination`, table `destinations`.)
- Send buttons in the details pane are plain pills (Save stays the one suggested action).
- USB: drives are added from the plugged-in ones and recognised by their drive ID (volume UUID), never by name. While one is plugged in, the details pane offers "Copy to <reader>" (USB icon).
- On the device, books go into its books folder in Calibre's layout, `<books folder>/<Last, First>/<Title> - <Author>.<ext>`, so books sent by Calibre and by Glafka share names. Copies are written to a hidden `.part` file, swapped in, and synced; the toast offers Eject.
- An identical file already there is reported, not copied. A different file at that path is replaced only when the user chooses Replace (it can reset the reading position).
- Never written on a device: anything outside the books folder, including Calibre's `metadata.calibre` and `driveinfo.calibre`, the Send-to-PocketBook `SendToPb/` folder and the trash. The scan below reads the whole drive.
- Email: SMTP with a Gmail preset and a custom server option; app password in the system keyring (Secret Service), never in the config file. For PocketBook, use its Send-to-PocketBook address (`username@pbsync.com`); the address registered with the service is approved by default, other senders must be approved from an email PocketBook sends (checked 2026-10-31). Outlook.com is not offered: it no longer accepts app passwords for SMTP, only OAuth2.
- Email readers are their own reader profiles (kind `email`, an address, formats); "Email to <reader>" (mail icon) is always offered for them. The book goes as one attachment named like on USB (`<Title> - <Author>.<ext>`). Encrypted connections only (STARTTLS or SSL/TLS).
- Sent by email means the SMTP server accepted it; delivery problems arrive later as a bounce email. The size is checked against the limit the server advertises; each click sends another copy.
- Emailed books land in the reader's Send-to-PocketBook folder, USB books in its books folder: a book sent both ways is on the device twice.
- Toasts for queued/sent/failed; a sent-history log (destination, and the hash and size of the copy sent).
- What is on each USB reader: scanned in the background each time it is plugged in (system and hidden folders skipped). A file is a library book when its content matches the library file or a copy that was sent, or, for EPUBs, when title and first author match a book that has an EPUB (catches copies Calibre edited). The scan replaces what was known for that reader. Copying to it records the copy right away; an emailed book counts once a scan finds it (in `SendToPb/`).
- Details pane, "On Your Readers": per USB reader on it / not on it and when it was checked. Plugged in: **Delete** removes the copies in the books folder (confirmed; copies elsewhere are reported, never deleted). Unplugged: **Mark as Removed** until the next scan. Email destinations show the last email sent.
- Deleting a file Calibre sent leaves Calibre's `metadata.calibre` out of date; Glafka never edits it (Calibre is expected to notice on its next connection; not verified).
- Warn when a book's format is unsupported by the reader.

### 5. Convert
Pluggable backends behind a `Converter` interface in `glafka.core`. The app picks the best available backend per format pair.

| Conversion | Built-in (Python) | Optional `ebook-convert` |
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
- UI is built in Python code, not Blueprint. May move to Blueprint widget by widget later.
- Errors are shown to the user (toast or status page), never only logged.

## Architecture
Python package `glafka`, installed with Meson so GNOME Builder can build and run it (`python3 -m glafka` runs it from the source tree):
- `glafka.core`: no GTK (`gi`) imports. Modules: `library`, `metadata`, `importer`, `grouping`, `text`, `send` (USB), `mail` (email), later `convert`.
- `glafka.ui`: GTK4/libadwaita app, thin layer over `core`.
- Long-running work (import, hashing, conversion, sending) runs off the main thread.
- Database schema is versioned with `PRAGMA user_version`; every schema change ships a migration.
- Tests with `pytest` (core only); lint and format with `ruff`.

Stack: Python 3.11+, PyGObject (GTK 4, libadwaita), standard library `sqlite3`, `zipfile` + `xml.etree`, `smtplib` + `email`; libsecret through GObject introspection for the keyring; `Gio.VolumeMonitor` for USB. `lxml` (Fedora `python3-lxml`) for rewriting OPF files, because `xml.etree` renames namespace prefixes. Other third-party packages only when needed, Fedora-packaged ones preferred. No virtual environment (PyGObject is a system package).

## Open decisions
- Distribution: Meson install from source first; Flatpak later (needs removable-media permission for USB; `ebook-convert` backend unavailable in sandbox unless via `flatpak-spawn --host`).
- PDF library choice (v1.1): pick after a spike comparing font and position data quality (candidates: `pdfminer.six` (MIT), `pypdf` (BSD)). Avoid AGPL dependencies (e.g. MuPDF / PyMuPDF) unless the app itself will be AGPL.
- Name availability: `glafka` checked 2026-10-31: free on PyPI and Flathub, two small unrelated GitHub repos. Re-check before publishing.

## Milestones
1. Workspace skeleton and empty libadwaita window.
2. `core`: SQLite library, EPUB metadata reader, import (files and Calibre library).
3. UI: grid, sidebar with grouped categories, search, details pane.
4. Send by USB, then by email.
5. Converters: `ebook-convert` backend, built-in text formats.
6. Polish: empty states, error messages, packaging.
