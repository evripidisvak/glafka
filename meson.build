project(
  'grammata',
  version: '0.1.0',
  meson_version: '>= 0.60.0',
)

dependency('gtk4', version: '>= 4.14')
dependency('libadwaita-1', version: '>= 1.5')

prefix = get_option('prefix')
bindir = prefix / get_option('bindir')
datadir = prefix / get_option('datadir')

subdir('data')
subdir('src')
