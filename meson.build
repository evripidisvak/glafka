project(
  'glafka',
  version: '0.2.1',
  meson_version: '>= 0.64.0',
  license: 'GPL-3.0-or-later',
)

python = import('python')
py = python.find_installation('python3')

prefix = get_option('prefix')
bindir = prefix / get_option('bindir')
datadir = prefix / get_option('datadir')
pkgdatadir = datadir / meson.project_name()

# Launcher: puts pkgdatadir on sys.path and runs glafka.main
conf = configuration_data()
conf.set('PYTHON', py.full_path())
conf.set('pkgdatadir', pkgdatadir)
launcher = configure_file(
  input: 'glafka.in',
  output: 'glafka.configured',
  configuration: conf,
)
# Installed via a target, not configure_file: Builder only offers installed targets as run commands
custom_target(
  'glafka',
  input: launcher,
  output: 'glafka',
  command: ['cp', '@INPUT@', '@OUTPUT@'],
  install: true,
  install_dir: bindir,
  install_mode: 'rwxr-xr-x',
)

# Every module must be listed here, or the installed app cannot import it
install_data(
  files(
    'glafka/__init__.py',
    'glafka/__main__.py',
    'glafka/main.py',
    'glafka/core/__init__.py',
    'glafka/core/config.py',
    'glafka/core/db.py',
    'glafka/core/device.py',
    'glafka/core/errors.py',
    'glafka/core/grouping.py',
    'glafka/core/history.py',
    'glafka/core/importer.py',
    'glafka/core/library.py',
    'glafka/core/mail.py',
    'glafka/core/models.py',
    'glafka/core/send.py',
    'glafka/core/text.py',
    'glafka/core/metadata/__init__.py',
    'glafka/core/metadata/calibre.py',
    'glafka/core/metadata/epub.py',
    'glafka/core/metadata/epub_writer.py',
    'glafka/core/metadata/pdf.py',
    'glafka/ui/__init__.py',
    'glafka/ui/application.py',
    'glafka/ui/book_card.py',
    'glafka/ui/book_object.py',
    'glafka/ui/details_pane.py',
    'glafka/ui/group_view.py',
    'glafka/ui/history_view.py',
    'glafka/ui/job_progress.py',
    'glafka/ui/preferences.py',
    'glafka/ui/drives.py',
    'glafka/ui/secrets.py',
    'glafka/ui/style.py',
    'glafka/ui/thumbnails.py',
    'glafka/ui/trash.py',
    'glafka/ui/window.py',
    'glafka/ui/worker.py',
  ),
  install_dir: pkgdatadir,
  preserve_path: true,
)

subdir('data')

gnome = import('gnome')
gnome.post_install(gtk_update_icon_cache: true, update_desktop_database: true)
