# Grammata: Plan

Spec: `SPEC.md`. Updated 2026-10-31.

## Working agreement
- Owners: **[C]** Claude writes it (GTK/UI plumbing, boilerplate). **[E]** Evripidis writes it, Claude designs, hints and reviews (core logic).
- Bugs get a failing test first, then the fix.
- Done means: `cargo test --workspace` green, no warnings, runs from GNOME Builder.

## Status

| Milestone | State |
|---|---|
| 1. Skeleton | Done |
| 2. Core | Done in core; Calibre import not reachable from the UI |
| 3. UI | Partial: grid, search, details pane, preferences done; see Phase 3 |
| 4. Send | Not started |
| 5. Convert | Not started |
| 6. Polish | Not started |

## Phase 1: Clean-up
- [ ] Git: `git init`, track `Cargo.lock`, first commit. **[E]**
- [ ] Move the library out of the project folder (re-import; stored paths are absolute today). **[E]**
- [ ] Bug: importing a different file with the same title, author and format overwrites the existing file; DB gets two rows for one path (`import.rs` `import_file` and `import_calibre_directory`). Decide behavior, failing test first. **[E]**
- [ ] Show errors instead of a blank window (library open fails) or an empty grid (book load fails). **[C]**
- [x] About dialog: developer name Vakalis. **[C]**

## Phase 2: Foundations
- [ ] Schema versioning: `PRAGMA user_version` plus an ordered list of migrations. **[E]**
- [ ] Store file and cover paths relative to the library root (first migration). **[E]**
- [ ] Background work: design how imports and other long tasks run off the main thread (`rusqlite::Connection` is not `Sync`). **[C+E]** design together; **[E]** core side, **[C]** UI side.

## Phase 3: Finish milestones 2 and 3
- [ ] Import from folder, drag-and-drop, Calibre library; on the background worker, with progress. **[C]** UI, **[E]** core.
- [ ] Thumbnails: scaled copies cached on disk, loaded asynchronously. **[C]**
- [ ] Grouped categories: one section per series / author / tag with a header; series ordered by index; a book appears under each of its tags. **[C]**
  - Check: section headers in `GtkGridView` (`GtkSectionModel`, header factory) in GTK 4.22 and gtk4-rs; fallback if unsupported.
  - Tags need a model where one book can appear in several sections.
- [ ] Sort control. **[C]**
- [ ] Grid / list toggle. **[C]**
- [ ] Write metadata edits back into the EPUB (rewrite the OPF inside the zip). **[E]**
- [ ] Rename book folder and files when title or author change. **[E]**
- [ ] PDF basic metadata (document info: title, author). **[E]**
- [ ] Bug: EPUB3 series index read from the `content` attribute; EPUB3 puts it in the element text (`epub.rs`). **[E]**
- [ ] Bug: series compared case-sensitively in the DB, case-insensitively in the grid sort. Decide one rule. **[E]**
- [ ] Bug: empty series name (`Some("")`) skips the friendly conflict message. **[E]**

## Phase 4: Send (milestone 4)
- [ ] Reader profiles: DB table, Preferences UI.
- [ ] USB: detect mounted readers (`gio::VolumeMonitor`), copy to the books folder.
- [ ] Sent history; make the "Sent to Reader" category real (today it shows all books).
- [ ] Email: SMTP (Gmail / Outlook presets), app password in the keyring.

## Phase 5: Convert (milestone 5)
- [ ] `Converter` trait in core.
- [ ] `ebook-convert` backend (PDF, MOBI, AZW3).
- [ ] Built-in TXT / HTML / Markdown / FB2 to EPUB.

## Phase 6: Polish and packaging

## v1.1
- Built-in PDF to EPUB, after a crate spike.
- Online metadata lookup.

## Decisions log
- 2026-10-31: work split (c): Claude does UI plumbing, Evripidis writes core pieces with guidance.
- 2026-10-31: UI stays in Rust code, no Blueprint for now.
- 2026-10-31: EPUB write-back and folder rename on title/author change are in v1.
- 2026-10-31: built-in PDF to EPUB moves to v1.1; v1 uses `ebook-convert`.
- 2026-10-31: send by USB before email.
- 2026-10-31: series index: whole numbers, unique per series, 0 = none, fractions truncated, dropped on import conflict.
- 2026-10-31: Authors, Series and Tags categories group books into sections, not just sort them.
