cargo_script = find_program(meson.project_source_root() / 'build-aux' / 'cargo.sh')

custom_target(
  'cargo-build',
  build_by_default: true,
  build_always_stale: true,
  output: 'grammata',
  console: true,
  install: true,
  install_dir: bindir,
  command: [
    cargo_script,
    meson.project_source_root() / 'Cargo.toml',
    meson.project_build_root() / 'target',
    '@OUTPUT@',
  ],
)
