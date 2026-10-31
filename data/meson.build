# Full launcher path: GNOME may not have ~/.local/bin on its PATH
desktop_conf = configuration_data()
desktop_conf.set('bindir', bindir)
configure_file(
  input: 'io.github.evripidisvak.Glafka.desktop.in',
  output: 'io.github.evripidisvak.Glafka.desktop',
  configuration: desktop_conf,
  install_dir: datadir / 'applications',
)

install_data(
  'io.github.evripidisvak.Glafka.metainfo.xml',
  install_dir: datadir / 'metainfo'
)

install_data(
  'icons/hicolor/scalable/apps/io.github.evripidisvak.Glafka.svg',
  install_dir: datadir / 'icons' / 'hicolor' / 'scalable' / 'apps'
)

install_data(
  'icons/hicolor/symbolic/apps/io.github.evripidisvak.Glafka-symbolic.svg',
  install_dir: datadir / 'icons' / 'hicolor' / 'symbolic' / 'apps'
)
