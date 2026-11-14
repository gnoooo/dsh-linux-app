# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-11-14

### Added

- Native GTK 3 / WebKitGTK 4.1 window for the `dsh web` GUI, with an
  application icon, desktop entry, and keyboard shortcuts. No browser runtime is
  bundled; the distribution's WebKitGTK is used.
- Backend supervision: `dsh web --no-open --port 0` runs in its own process
  group, its tokenised `dsh web: <url>` line is read and loaded as the first
  WebView navigation, and the group is stopped with `SIGTERM`/`SIGKILL` when the
  window closes.
- Distro-aware diagnostics (`--check`) and install hints for Fedora,
  Debian/Ubuntu, Arch, openSUSE, and Void, including the correct package names
  for each.
- Configuration precedence: built-in defaults, then
  `~/.config/dsh-desktop/config.toml`, then `DSH_DESKTOP_*` environment
  variables, then command-line flags.
- Pass-through launcher flags: unknown options are forwarded to `dsh web` ahead
  of the wrapper's own `--no-open`/`--port` flags, so overlays such as
  `--patch overlay.yml` work.
- Single-instance `Gtk.Application`: launching the app again activates the
  existing window instead of starting a second backend.
- Per-user installer and uninstaller (`scripts/install.sh`,
  `scripts/uninstall.sh`) plus an Arch `PKGBUILD` and `.SRCINFO` for a native
  system package.
- Headless `unittest` suite for the GTK-free modules (`cli`, `config`,
  `distro`, `server`) plus `manage.sh` smoke tests.
- GitHub Actions CI: `uv lock --check`, the headless suite on Python 3.11 and
  3.13, `./manage.sh check`, and sdist/wheel artifacts.

### Changed

- Version handling is centralized in `manage.sh`: `bump` keeps
  `pyproject.toml`, `uv.lock`, `packaging/arch/PKGBUILD`, and
  `packaging/arch/.SRCINFO` in sync, and `check` fails when they disagree.

### Fixed

- Test isolation for system-wide `dsh` installs: tests can disable the
  conventional `/usr/local/bin` and `/usr/bin` candidate paths, so a host
  `dsh` no longer changes launcher-resolution results.
- `--check` now exits `1` when Node.js is absent or older than 24.9, or when the
  configured workspace directory does not exist, matching its documented role
  as a preflight check.
- The Arch PKGBUILD invokes `python3`, so a stray `python` earlier on `PATH`
  cannot break `makepkg`.

[Unreleased]: https://github.com/gnoooo/dsh-linux-app/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/gnoooo/dsh-linux-app/releases/tag/v0.1.0
