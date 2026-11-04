# Glafka: Plan

Spec: `SPEC.md`. Updated 2026-11-04.

## Working agreement
- We build it together: Claude writes code in small, reviewable steps; Evripidis reviews, tests in GNOME Builder, steers.
- Bugs get a failing test first, then the fix.
- Done means: `pytest` green, `ruff check` clean, runs from GNOME Builder.
- New work happens on a `feature/<name>` branch; one commit at the end of each work day. `main` only gets released versions, which is what installs follow (`git pull`, `meson install -C _build`).
- Release: on the branch, bump the version in `meson.build`, `glafka/__init__.py`, a `<release>` in the metainfo and the README; commit; `git merge --ff-only` into `main`; tag `vX.Y.Z`; push `main` and the tag; delete the branch.

## Status

| Milestone | State |
|---|---|
| 1. Skeleton | Done (Python) |
| 2. Core | Done in core; Calibre import not reachable from the UI |
| 3. UI | Partial: grid, search, details pane, preferences done; see Phase 3 |
| 4. Send | Not started |
| 5. Convert | Not started |
| 6. Polish | Not started |

## Phase 0: Python port
- [x] Core and tests ported (`glafka/core`, `tests/`).
- [x] UI ported (`glafka/ui`).
- [x] Meson install and launcher, so Builder's Play works ("Install Before Running" ON). The launcher must be a `custom_target`: Builder only runs installed Meson targets.
- [x] Rust code removed from the tree (kept in git history, commit `fe837a3`); Rust toolchain uninstalled.
- [x] Verified in Builder against the real library.

## Phase 1: Clean-up
- [x] Git: `git init`, first commit.
- [x] Move the library out of the project folder.
- [x] Bug: importing a different file with the same title, author and format overwrote the existing file; DB got two rows for one path (`importer.py` `import_file` and `import_calibre_directory`). Also hits different books whose folder names collide (80-char cut). Regression test: `tests/test_importer.py::test_import_never_overwrites_existing_format`.
  - [x] `import_file`: decide before writing; skip + report when the format exists; copies and covers never overwrite (`copy_without_overwrite`).
  - [x] `import_file`: numbered folder suffix for new books whose folder is taken (`claim_book_dir`); new formats go into the existing book's folder; a failed import leaves no folder or file behind.
  - [x] `delete_book` also removes folders it leaves empty, so re-importing gets the plain folder name.
  - [x] `import_calibre_directory`: same rules (every file goes through the same path as `import_file`); `ImportSummary` counts per file.
  - [x] Report the outcome (`ImportResult`: imported / format added / already in library / format exists); one summary toast per import, including failures.
  - [x] Bug: attaching a format added a second tile for the same book (now updates the existing tile).
  - [x] DB backstop `UNIQUE(book_id, format)` on `book_files` (migration to version 2).
- [x] Show errors instead of a blank window (library open fails) or an empty grid (book load fails): error page with "Choose Library Folder…"; the library setting is saved only after the new library opens.
- [x] About dialog and metainfo: developer name Evripidis Vakalis.
- [x] Delete a book: "Delete Book" in the details pane, with a confirmation dialog. Index first, then files and cover; only paths inside the library; folders removed only when empty.
- [x] Bug: after closing the details pane, clicking the still-selected tile did not reopen it. Now the pane is open exactly while a book is selected: closing it unselects, nothing is selected automatically.

## Phase 2: Foundations
- [x] Schema versioning: `PRAGMA user_version` plus `MIGRATIONS` in `db.py` (SQL or Python steps, each all-or-nothing). Rust-era databases are stamped as version 1.
- [x] Store file and cover paths relative to the library root (migration to version 2). Paths of a moved or copied library are recovered by matching `Author/Title/file` under the new root.
- [x] Background work: one long-lived worker thread (`ui/worker.py`) with its own library connection (a `sqlite3` connection only works in the thread that created it). Results and progress return on the main thread via `GLib.idle_add`; core stays GTK-free. Rules:
  - Exactly one worker, jobs in order: import's check-then-write logic relies on it.
  - The worker commits per file, never per batch (the window's connection may need to write meanwhile).
  - A job remembers its library; switching library cancels it and its late results are dropped.
  - Closing the window cancels jobs; the current file finishes; late callbacks do nothing.
  - Unexpected errors in a job reach the UI as a failure and are logged, never lost in the thread.
  - Jobs never touch GTK; callbacks may.
  - Import progress: bar at the top of the grid ("Importing 12 of 300: Title") with Cancel.
  - Done for file import (`import_paths`); folder, drag-and-drop and Calibre import (Phase 3) reuse it.

## Phase 3: Finish milestones 2 and 3
- [x] Import from folder, drag-and-drop, Calibre library; on the background worker, with progress. One path (`plan_import` then `run_import`): a folder with `metadata.db` imports as a Calibre library, other folders are searched for ebooks (hidden folders and the library itself skipped). Entry points: + split button ("Add Folder or Calibre Library…"), empty-library page, dropping files or folders on the window.
- [x] Preference "Move Files Into the Library": originals go to the Trash after a successful import (also identical duplicates); never skipped/failed files, Calibre libraries, or files inside the library. Core takes a `remove_original` callback; the UI passes GIO's trash.
- [x] Thumbnails: 280x400 JPEGs in `~/.cache/glafka/thumbnails/` (keyed by cover path, size, mtime), made on 2 background threads (`ui/thumbnails.py`); tiles show a neutral placeholder until ready. 60-book test: 44 ms -> 1.3 ms per tile on the main thread, 8.6 MB -> 0.43 MB per cover.
  - [x] Unused thumbnails (deleted books, moved or changed covers) pruned in the background each time a library opens.
- [x] Grouped categories: Authors, Series, Tags show one section per group with a header and book count (`core/grouping.py`, `ui/group_view.py`); series sections ordered by index, unindexed last; a book appears under each of its tags; groups match case-insensitively.
  - GTK 4.22 `GridView` has no section headers, so sections are a `ListView` of groups, each wrapping its tiles (clamped to the grid's tile width).
- [x] Icons: `emblem-documents-symbolic` did not exist in the theme (missing-icon fallback was shown). All Books and the empty page now use `accessories-dictionary-symbolic`; books without a cover get a generated cover (`ui/style.py`, `generated_cover`).
- [x] Sort control: header menu (Title / Author / Date Added, ascending / descending), remembered in the config; applies to the grid and inside Authors/Tags sections; disabled in Recently Added and Series (fixed orders).
- [x] Grid / list toggle: header button, remembered in the config. List rows: small cover, title and author, series, formats. Flat views share one selection model (switching keeps the selected book); grouped views show rows inside their sections.
- [x] Write metadata edits back into the EPUB (`core/metadata/epub_writer.py`, lxml): only the OPF changes; other entries copied unchanged and streamed; mimetype first, stored; atomic replace; DRM refused; runs on the worker after Save. Schema v3 adds `source_sha256` so originals stay recognised.
- [x] Rename book folder and files when title or author change (`LibraryManager.sync_files`): `Title (n)` if taken; rolled back on failure; old folder removed when empty.
- [x] PDF basic metadata (`core/metadata/pdf.py`): title and first author from the document info, page 1 rendered as the cover; poppler-utils CLI (installed, no AGPL); falls back to the file name.
- [x] "Reload Details from File" (details pane, confirmed): EPUB first, else PDF; what the file says wins, gaps keep their values; series slot taken drops the index; cover swapped atomically (unchanged cover kept); then EPUB write-back and folder rename like a save (`LibraryManager.reload_metadata`).
- [x] Bug: EPUB3 series index read from the `content` attribute instead of the element text (fixed in the port).
- [x] Series names compare case-insensitively everywhere (conflict check, unique index via schema v4, grouping); slots taken twice by case variants resolved like imports (the later book loses the index).
- [x] Empty series name means no series (and no index): cleaned when reading metadata and in the database layer.
- [x] Bug: descriptions stored as HTML (common in Calibre-made EPUBs) showed as raw markup. Now shown as plain text (`core/text.py`); saving without touching it keeps the HTML, an edited description is saved as plain text.

## Phase 4: Send (milestone 4)
- [x] Reader status (`core/device.py`, schema v8 `on_device` + send hash/size): scan on plug-in (content, else EPUB title + first author), On Reader category, cover badge, details pane rows with Delete (books folder only) and Mark as Removed. Decisions 2026-10-31: scan reads `SendToPb/`, auto-scan, Delete built now, badge yes.
- [x] Destinations (ways to send to a reader): table `readers` (schema v5, stored per library), renamed `destinations` in v7 along with the code (`Reader` → `Destination`, `ui/readers.py` → `ui/drives.py`). Preferences → Sending page: "Send Books To" (Add USB Drive, Add Email Address, edit name/folder/address/formats, remove) and Email Account.
- [x] USB: `ui/drives.py` (`Gio.VolumeMonitor`, matched by volume UUID); `core/send.py` (Calibre layout, FAT-safe names, `.part` + fsync, identical/different copy checks, free space); Send button under the cover; Replace and Eject toasts.
- [x] Sent history (sent or found identical). Its "Sent to Reader" category became "On Reader" (what the last scan found).
- [x] History (decisions 2026-11-04: page and per book, Glafka's own actions only, kept for deleted books, Recently Added icon now `document-new-symbolic`): schema v9 `history` (from `sent_history`; book title and author kept, `book_id` set to NULL when the book is deleted), `core/history.py` records copies, emails, already-there, deletes from the reader and marks; `ui/history_view.py` (day sections, filter, search); details pane History row.
- [x] Email: SMTP (Gmail preset + custom server), app password in the keyring (libsecret). Outlook dropped: OAuth2 only (decision 2026-10-31).
  - `core/mail.py` (smtplib, CRLF message, server SIZE limit checked before login), `ui/secrets.py` (async libsecret), schema v6 (`email` column), Preferences: Email Account group and Add Email Address.
  - First real send 2026-10-31 (9.2 MB EPUB): arrived in `SendToPb/` under Glafka's file name, identical to the library file. Still unseen: non-ASCII titles (RFC 2231 encoded names).
  - Send buttons name the route: "Copy to <reader>" (USB icon) and "Email to <reader>" (mail icon); two "Send to" buttons were confusing.

## Phase 5: Convert (milestone 5)
- [ ] `Converter` interface in core.
- [ ] `ebook-convert` backend (PDF, MOBI, AZW3). Calibre is not installed at the moment.
- [ ] Built-in TXT / HTML / Markdown / FB2 to EPUB.

## Phase 6: Polish and packaging
- [x] App icon (decisions 2026-10-31): the Athenian coin owl stamped in gold on a blue book, red ribbon (`data/icons/hicolor/scalable/apps/…Glafka.svg`), and a 16 px symbolic coin owl (body in profile, face to the viewer, ringed eyes, beak notch, wing line), drawn on the 16 px grid (a scaled-down silhouette read as a duck). The two SVGs are the source; drafts were not kept. Installed by Meson; icon cache refreshed on install.
- [x] Metainfo: launchable, developer, OARS rating, branding colours, release 0.1.0, homepage and bug tracker (github.com/evripidisvak/glafka), description of what exists.
- [x] Before publishing: README (install into `~/.local` from a version tag, update, uninstall), GPL-3.0 `LICENSE`, desktop file with the launcher's full path (`desktop.in`, set by Meson), keyboard shortcuts (Ctrl+O, Ctrl+Shift+O, Ctrl+F, Ctrl+S, Ctrl+,, Ctrl+W, Ctrl+Q), About dialog icon, author and links (the app adds its own icon folder to GTK's search path, so the icon shows when run from the source tree or Builder too), empty On Reader page, the reader's drive ID kept out of the repo.
- Flatpak, local first (decision 2026-10-31; Flathub later, after conversions are designed for the sandbox): runtime `org.gnome.Platform//51`; bundle `lxml` and poppler-utils (`pdfinfo`, `pdftoppm`); libsecret uses the Secret portal (app password entered once more). Permissions: `xdg-documents` + `~/Books` (decision: not the whole home folder; a library elsewhere gets a clear error), `/run/media` for USB, network. Test in the sandbox: USB scan/copy/eject, Trash, keyring. Flatpak config starts fresh (library folder asked again).
- Optional check: run `epubcheck` on a few rewritten EPUBs.

## v1.1
- Built-in PDF to EPUB, after a library spike (`pdfminer.six`, `pypdf`).
- Online metadata lookup.

## Decisions log
- 2026-10-31: UI built in code, no Blueprint for now.
- 2026-10-31: EPUB write-back and folder rename on title/author change are in v1.
- 2026-10-31: built-in PDF to EPUB moves to v1.1; v1 uses `ebook-convert`.
- 2026-10-31: send by USB before email.
- 2026-10-31: series index: whole numbers, unique per series, 0 = none, fractions truncated, dropped on import conflict.
- 2026-10-31: Authors, Series and Tags categories group books into sections, not just sort them.
- 2026-10-31: import of a format a book already has is skipped and reported; a new book whose folder is taken gets `Title (2)`.
- 2026-10-31: switched from Rust to Python + PyGObject (Rust friction; Python needed soon). Rust version in git history, commit `fe837a3`. Same library format, so existing libraries keep working.
- 2026-10-31: no Claude/Evripidis task split; build together normally.
- 2026-10-31: background work: one serial worker thread with its own DB connection (not asyncio, not processes); import progress as a bar with Cancel at the top of the grid.
- 2026-10-31: EPUB write-back with lxml (keeps namespace prefixes); schema v3 keeps each file's hash as imported (`source_sha256`) for duplicate detection.
- 2026-10-31: renamed Grammata -> Glafka (γλαύκα, owl; spelled as pronounced). App ID `io.github.evripidisvak.Glafka`. Old config, library index folder (`.grammata` -> `.glafka`) and thumbnail cache migrate automatically on first start.
- 2026-10-31: USB sending: user's reader is a PocketBook (label `PB710`, FAT32, mounted at `/run/media/<user>/PB710`). Calibre managed it before (`metadata.calibre`, `driveinfo.calibre`; books in `Books/<Last, First>/<Title> - <Author>.epub`). Glafka uses the same `Books/` folder and Calibre's layout; never touches `metadata.calibre`, `driveinfo.calibre`, `SendToPb/` (Send-to-PocketBook cloud) or `.Trash-1000`. A different copy at the same path is replaced only after the user confirms (resets reading position).
- 2026-10-31: All Books icon: open book (`accessories-dictionary-symbolic`); books without a cover get a generated cover (title and author on a tinted tile).
- 2026-10-31: optional "move" import: originals go to the Trash (not deleted) after a successful import, including identical duplicates; never skipped, failed or Calibre files.
