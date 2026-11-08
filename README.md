<img src="data/icons/hicolor/scalable/apps/io.github.evripidisvak.Glafka.svg" width="128" alt="">

# Glafka

A lightweight, GNOME-native ebook library manager. *Glafka* is γλαύκα, the owl of Athena, spelled the way Greek says it.

Glafka keeps your ebooks in one tidy library and gets them onto your e-reader, by USB or by email. It needs no Calibre, but can import a Calibre library.

## Features

- Import EPUB and PDF files, whole folders, or a Calibre library (drag and drop works too)
- Browse by author, series and tag, as a cover grid or a list; search and sort
- Edit details; edits are written back into EPUB files, and folders are renamed to match
- Set a cover from any image file (or use the generated one); it goes into the EPUB too, so the reader shows it
- Copy books to a USB e-reader in Calibre's folder layout, or email them (for example to Send-to-PocketBook)
- See which books are on your reader: it is checked each time you plug it in

Early days: version 0.3. Converting between formats is not there yet. Developed and tested on Fedora with GNOME and a PocketBook reader.

## Install (Fedora, GNOME)

```sh
sudo dnf install git meson python3-gobject python3-lxml poppler-utils libsecret
git clone https://github.com/evripidisvak/glafka.git
cd glafka
meson setup _build --prefix="$HOME/.local"
meson install -C _build
```

Glafka then appears in the app grid like any other app.

`main` only gets released versions (tagged `v0.1.0`, `v0.2.0`, ...); new work happens on feature branches.

### Update

```sh
cd glafka
git pull
meson install -C _build
```

A new version may upgrade your library's database on its first start; older versions cannot open it after that.

### Uninstall

```sh
ninja -C _build uninstall
```

Your library folder is not touched.

## Development

```sh
python3 -m glafka                     # run from the source tree
pytest                                # tests (python3-pytest)
ruff check . && ruff format --check . # lint (ruff)
```

`SPEC.md` describes what Glafka should do; `PLAN.md` tracks what is done and what is next.

## License

GPL-3.0-or-later. See `LICENSE`.
