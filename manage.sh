#!/usr/bin/env bash
# manage.sh — small project manager for dsh-desktop (Python + uv + GTK/WebKitGTK).
#
# Commands: ./manage.sh help
# This is deliberately a subset of packrat/manage.sh: no pytest (the suite is
# unittest), no Rust/npm/Flatpak machinery. The desktop runtime cannot be
# checked by a version string, so it is checked by importing Gtk 3.0 and
# WebKit2 4.1, exactly as the app does.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"
PYPROJECT="$REPO_ROOT/pyproject.toml"
README="$REPO_ROOT/README.md"
UV_LOCK="$REPO_ROOT/uv.lock"
CHECK_VERSION="$REPO_ROOT/dev/check_version.py"
BUMP_SCRIPT="$REPO_ROOT/dev/bump-version.sh"
DIST_DIR="$REPO_ROOT/dist"
VENV="$REPO_ROOT/.venv"
PKGBUILD="$REPO_ROOT/packaging/arch/PKGBUILD"
SRCINFO="$REPO_ROOT/packaging/arch/.SRCINFO"
DESKTOP_IN="$REPO_ROOT/assets/dsh-desktop.desktop.in"

SEMVER_RE='^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$'

# sysexits.h-compatible exit codes (64–78), same convention as packrat.
EXIT_ERROR=1
EXIT_USAGE=64
EXIT_DATAERR=65
EXIT_NOINPUT=66
EXIT_UNAVAILABLE=69
EXIT_SOFTWARE=70
EXIT_CONFIG=78

if [ -z "${NO_COLOR:-}" ] && { [ "${FORCE_COLOR:-0}" = "1" ] || { [ "${FORCE_COLOR:-0}" != "0" ] && [ -t 1 ]; }; }; then
  BOLD=$'\033[1m'; DIM=$'\033[2m'; NC=$'\033[0m'
  CYAN=$'\033[36m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'
  RED=$'\033[31m'; BLUE=$'\033[34m'
else
  BOLD=; DIM=; NC=; CYAN=; GREEN=; YELLOW=; RED=; BLUE=
fi

die_code() {
  local code="$1"; shift
  printf 'manage: [%s%s%s] %s\n' "$RED" "$code" "$NC" "$*" >&2
  exit "$code"
}
have() { command -v "$1" >/dev/null 2>&1; }

require_cmd() {
  local c
  for c in "$@"; do
    have "$c" || die_code "$EXIT_UNAVAILABLE" "missing tool: $c"
  done
}

require_file() {
  local f
  for f in "$@"; do
    [ -f "$f" ] || die_code "$EXIT_NOINPUT" "file not found: $f"
  done
}

# Prefer the installed virtualenv (it can import the system PyGObject); fall
# back to the system interpreter for a fresh clone.
python_cmd() {
  if [ -x "$VENV/bin/python" ]; then
    printf '%s\n' "$VENV/bin/python"
  else
    printf 'python3\n'
  fi
}

project_version() {
  python3 - "$PYPROJECT" <<'PY'
import sys, tomllib
with open(sys.argv[1], "rb") as fh:
    print(tomllib.load(fh)["project"]["version"])
PY
}

lock_version() {
  python3 - "$UV_LOCK" <<'PY'
import sys, tomllib
with open(sys.argv[1], "rb") as fh:
    data = tomllib.load(fh)
for pkg in data.get("package", []):
    if pkg.get("name") == "dsh-desktop":
        print(pkg.get("version", ""))
        break
PY
}

pkgbuild_version() {
  sed -n 's/^pkgver=//p' "$PKGBUILD" 2>/dev/null | head -n 1
}

srcinfo_version() {
  sed -n 's/^[[:space:]]*pkgver = //p' "$SRCINFO" 2>/dev/null | head -n 1
}

git_branch() {
  git -C "$REPO_ROOT" branch --show-current 2>/dev/null || echo "(detached)"
}

last_git_tag() {
  git -C "$REPO_ROOT" describe --tags --abbrev=0 --match 'v*' 2>/dev/null || echo "(none)"
}

compute_bump() {
  python3 - "$1" "$2" <<'PY'
import re, sys
kind, current = sys.argv[1], sys.argv[2]
m = re.match(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$", current)
if not m:
    raise SystemExit(f"invalid current version: {current}")
major, minor, patch = map(int, m.group(1, 2, 3))
if kind == "major":
    print(f"{major + 1}.0.0")
elif kind == "minor":
    print(f"{major}.{minor + 1}.0")
elif kind == "patch":
    print(f"{major}.{minor}.{patch + 1}")
else:
    raise SystemExit(f"unknown bump type: {kind}")
PY
}

usage() {
  cat <<EOF
${BOLD}$0${NC} — dsh-desktop project manager ${DIM}(Python + uv + GTK/WebKitGTK)${NC}

${BOLD}Usage:${NC}
  $0 ${BLUE}info${NC}                       Project, version, runtime, lock, git state
  $0 ${BLUE}bump${NC} ${YELLOW}<version|type>${NC} [${GREEN}--dry-run${NC}]
                                      Bump pyproject.toml + uv.lock + packaging/arch
                                      types: major, minor, patch
  $0 ${BLUE}check${NC} [${YELLOW}--tag vX.Y.Z${NC}] [${GREEN}--no-compile${NC}]
                                      Version/lock/packaging consistency, compileall,
                                      shell syntax, desktop entry
  $0 ${BLUE}test${NC} [${YELLOW}unittest args${NC}]     Run the unittest suite
  $0 ${BLUE}build${NC} [${YELLOW}wheel|sdist|both${NC}]
                                      Build distributions into dist/
  $0 ${BLUE}clean${NC} [${YELLOW}cache|build|dist|arch|all${NC}] [${GREEN}--dry-run${NC}] [${GREEN}--yes${NC}]
                                      Remove caches/build artifacts (default: cache)
  $0 ${BLUE}codes${NC}                      Show manage.sh exit codes
  $0 ${BLUE}help${NC}                       This help

${BOLD}Examples:${NC}
  $0 info
  $0 bump patch --dry-run
  $0 bump 0.2.0
  $0 check && $0 test
  $0 build
  $0 clean all --dry-run
  $0 clean arch --yes

${BOLD}Version sources:${NC} pyproject.toml, uv.lock, packaging/arch/PKGBUILD and
.SRCINFO. \`bump\` updates all four; \`check\` fails when they disagree.

${BOLD}Not included:${NC} Rust/Tauri, npm, Flatpak, containers, release publishing.
Installing the app for the current user stays in scripts/install.sh; the Arch
package stays in packaging/arch.
EOF
}

set_packaging_version() {
  python3 - "$PKGBUILD" "$SRCINFO" "$1" <<'PY'
import pathlib, re, sys

pkgbuild, srcinfo, version = sys.argv[1], sys.argv[2], sys.argv[3]

text = pathlib.Path(pkgbuild).read_text(encoding="utf-8")
text, n = re.subn(r"^pkgver=.*$", f"pkgver={version}", text, count=1, flags=re.M)
if n != 1:
    raise SystemExit(f"{pkgbuild}: pkgver not found")
# A new upstream version starts a fresh package release.
text, m = re.subn(r"^pkgrel=.*$", "pkgrel=1", text, count=1, flags=re.M)
if m != 1:
    raise SystemExit(f"{pkgbuild}: pkgrel not found")
pathlib.Path(pkgbuild).write_text(text, encoding="utf-8")

text = pathlib.Path(srcinfo).read_text(encoding="utf-8")
text, n = re.subn(r"^(\s*)pkgver = .*$", rf"\g<1>pkgver = {version}", text, count=1, flags=re.M)
if n != 1:
    raise SystemExit(f"{srcinfo}: pkgver not found")
text, m = re.subn(r"^(\s*)pkgrel = .*$", r"\g<1>pkgrel = 1", text, count=1, flags=re.M)
if m != 1:
    raise SystemExit(f"{srcinfo}: pkgrel not found")
pathlib.Path(srcinfo).write_text(text, encoding="utf-8")
PY
}

runtime_report() {
  # The app's real requirement: import Gtk 3.0 and WebKit2 4.1.
  local py
  py="$(python_cmd)"
  "$py" - <<'PY' 2>/dev/null || true
try:
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("WebKit2", "4.1")
    from gi.repository import Gtk, WebKit2
except Exception:
    print("gtk\tmissing")
    print("webkitgtk\tmissing")
else:
    print(f"gtk\t{Gtk.get_major_version()}.{Gtk.get_minor_version()}.{Gtk.get_micro_version()}")
    print(f"webkitgtk\t{WebKit2.get_major_version()}.{WebKit2.get_minor_version()}.{WebKit2.get_micro_version()}")
PY
}

cmd_info() {
  require_file "$PYPROJECT"
  echo "${BOLD}dsh-desktop${NC}"
  echo
  printf '  %-18s %s\n' "version" "$(project_version)"
  if [ -f "$UV_LOCK" ]; then
    printf '  %-18s %s\n' "lock version" "$(lock_version 2>/dev/null || echo '(missing)')"
  else
    printf '  %-18s %s\n' "lock version" "${RED}missing${NC}"
  fi
  if [ -f "$PKGBUILD" ]; then
    printf '  %-18s %s\n' "PKGBUILD pkgver" "$(pkgbuild_version)"
    printf '  %-18s %s\n' ".SRCINFO pkgver" "$(srcinfo_version)"
  else
    printf '  %-18s %s\n' "packaging/arch" "${YELLOW}missing${NC}"
  fi
  printf '  %-18s %s\n' "requires-python" "$(python3 - "$PYPROJECT" <<'PY'
import sys, tomllib
with open(sys.argv[1], "rb") as fh:
    print(tomllib.load(fh)["project"].get("requires-python", "-"))
PY
)"
  printf '  %-18s %s\n' "python" "$(python_cmd) ($("$(python_cmd)" --version 2>&1))"
  if [ -x "$VENV/bin/python" ]; then
    printf '  %-18s %s\n' "venv" "${GREEN}$VENV${NC}"
  else
    printf '  %-18s %s\n' "venv" "${YELLOW}not created (./scripts/install.sh)${NC}"
  fi
  if have uv; then
    printf '  %-18s %s\n' "uv" "$(uv --version 2>&1)"
  else
    printf '  %-18s %s\n' "uv" "${RED}not found${NC}"
  fi

  echo
  echo "${BOLD}Runtime${NC}"
  local report
  report="$(runtime_report)"
  if [ -n "$report" ]; then
    while IFS=$'\t' read -r name value; do
      if [ "$value" = missing ]; then
        printf '  %-18s %s\n' "$name" "${RED}missing${NC}"
      else
        printf '  %-18s %s\n' "$name" "$value"
      fi
    done <<<"$report"
  else
    printf '  %-18s %s\n' "gtk" "${RED}cannot import gi${NC}"
  fi
  if [ -x "$VENV/bin/dsh-desktop" ]; then
    local diag
    diag="$("$VENV/bin/dsh-desktop" --check 2>/dev/null || true)"
    printf '  %-18s %s\n' "dsh launcher" "$(printf '%s\n' "$diag" | sed -n 's/^launcher *//p' | head -n 1)"
    printf '  %-18s %s\n' "node" "$(printf '%s\n' "$diag" | sed -n 's/^node *//p' | head -n 1)"
  else
    printf '  %-18s %s\n' "dsh launcher" "${DIM}(install first: ./scripts/install.sh)${NC}"
  fi

  echo
  echo "${BOLD}Checks${NC}"
  if [ -f "$CHECK_VERSION" ]; then
    local out
    if out="$(python3 "$CHECK_VERSION" 2>&1)"; then
      printf '  %-18s %s\n' "versions" "${GREEN}OK${NC}"
    else
      printf '  %-18s %s\n' "versions" "${RED}KO${NC}"
      printf '%s\n' "$out" | sed 's/^/    /' >&2
    fi
  else
    printf '  %-18s %s\n' "versions" "${YELLOW}check_version.py missing${NC}"
  fi
  if have uv && [ -f "$UV_LOCK" ]; then
    local lock_out
    if lock_out="$(uv lock --check 2>&1)"; then
      printf '  %-18s %s\n' "uv.lock" "${GREEN}OK${NC}"
    else
      printf '  %-18s %s\n' "uv.lock" "${RED}check failed${NC}"
      printf '%s\n' "$lock_out" | sed 's/^/    /' >&2
    fi
  fi

  echo
  echo "${BOLD}Git${NC}"
  printf '  %-18s %s\n' "branch" "$(git_branch)"
  printf '  %-18s %s\n' "last tag" "$(last_git_tag)"
  printf '  %-18s %s\n' "status" "$(git -C "$REPO_ROOT" status --porcelain | grep -q . && echo modified || echo clean)"
  printf '  %-18s %s\n' "HEAD" "$(git -C "$REPO_ROOT" log -1 --format='%h %s' 2>/dev/null || echo '-')"
  printf '  %-18s %s\n' "hooksPath" "$(git -C "$REPO_ROOT" config --get core.hooksPath 2>/dev/null || echo '(not set)')"

  if [ -d "$DIST_DIR" ] && [ -n "$(ls -A "$DIST_DIR" 2>/dev/null)" ]; then
    echo
    echo "${BOLD}Dist${NC}"
    ls -1 "$DIST_DIR" | sed 's/^/  /'
  fi
}

cmd_bump() {
  local new="" kind="" dry_run=0
  for arg in "$@"; do
    case "$arg" in
      --dry-run) dry_run=1 ;;
      -h|--help) usage; return 0 ;;
      major|minor|patch)
        [ -z "$kind" ] && kind="$arg" || die_code "$EXIT_USAGE" "only one bump type is allowed"
        ;;
      -*) die_code "$EXIT_USAGE" "unknown option: $arg" ;;
      *)
        [ -z "$new" ] && new="$arg" || die_code "$EXIT_USAGE" "only one version is allowed"
        ;;
    esac
  done
  [ -n "$new" ] || [ -n "$kind" ] || die_code "$EXIT_USAGE" "usage: $0 bump <version|type> [--dry-run]"
  [ -z "$new" ] || [ -z "$kind" ] || die_code "$EXIT_USAGE" "give a version OR a bump type, not both"

  require_cmd python3 uv git
  require_file "$PYPROJECT" "$UV_LOCK" "$BUMP_SCRIPT" "$CHECK_VERSION" "$PKGBUILD" "$SRCINFO"
  local tracked=(pyproject.toml uv.lock packaging/arch/PKGBUILD packaging/arch/.SRCINFO)
  if [ "$dry_run" = 0 ]; then
    git diff --quiet -- "${tracked[@]}" &&
      git diff --cached --quiet -- "${tracked[@]}" ||
      die_code "$EXIT_CONFIG" "version files are dirty; commit or stash first"
    git rev-parse -q --verify "refs/tags/v$new" >/dev/null 2>&1 &&
      die_code "$EXIT_CONFIG" "tag v$new already exists; tags are immutable"
  fi
  local old
  old="$(project_version)"
  if [ -n "$kind" ]; then
    new="$(compute_bump "$kind" "$old")" || die_code "$EXIT_DATAERR" "could not compute bump"
  fi
  [[ "$new" =~ $SEMVER_RE ]] || die_code "$EXIT_DATAERR" "version must be canonical X.Y.Z"
  [ "$new" != "$old" ] || die_code "$EXIT_USAGE" "version is already $new"

  echo "Bump: ${BOLD}$old${NC} -> ${BOLD}$new${NC}"
  if [ "$dry_run" = 1 ]; then
    echo "  pyproject.toml       version = \"$new\""
    echo "  uv.lock              dsh-desktop $new (refreshed by uv lock)"
    echo "  packaging/arch       pkgver=$new, pkgrel=1 (PKGBUILD and .SRCINFO)"
    echo "  (dry-run: nothing written)"
    return 0
  fi

  local backup
  backup="$(mktemp -d)"
  cp "$PYPROJECT" "$UV_LOCK" "$PKGBUILD" "$SRCINFO" "$backup"/
  restore() {
    cp "$backup/pyproject.toml" "$PYPROJECT"
    cp "$backup/uv.lock" "$UV_LOCK"
    cp "$backup/PKGBUILD" "$PKGBUILD"
    cp "$backup/.SRCINFO" "$SRCINFO"
    rm -rf "$backup"
  }
  if ! bash "$BUMP_SCRIPT" "$new"; then
    restore
    die_code "$EXIT_ERROR" "bump failed; restored the version files"
  fi
  if ! set_packaging_version "$new"; then
    restore
    die_code "$EXIT_ERROR" "packaging update failed; restored the version files"
  fi
  echo
  if ! python3 "$CHECK_VERSION"; then
    restore
    die_code "$EXIT_DATAERR" "post-bump version check failed; restored the version files"
  fi
  if [ "$(pkgbuild_version)" != "$new" ] || [ "$(srcinfo_version)" != "$new" ]; then
    restore
    die_code "$EXIT_DATAERR" "packaging versions did not follow; restored the version files"
  fi
  rm -rf "$backup"
  echo
  git -C "$REPO_ROOT" status --short -- "${tracked[@]}" | sed 's/^/  /'
  echo
  echo "No commit or tag created. Next: git add ${tracked[*]} && git commit."
}

cmd_check() {
  local no_compile=0 tag=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --no-compile) no_compile=1; shift ;;
      --tag) [ $# -ge 2 ] || die_code "$EXIT_USAGE" "--tag needs a value"; tag="$2"; shift 2 ;;
      -h|--help) usage; return 0 ;;
      *) die_code "$EXIT_USAGE" "unknown check option: $1" ;;
    esac
  done
  if [ -n "$tag" ]; then
    [[ "$tag" =~ ^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]] \
      || die_code "$EXIT_USAGE" "tag must be vX.Y.Z"
  fi
  require_cmd python3 uv git
  require_file "$PYPROJECT" "$UV_LOCK" "$CHECK_VERSION"

  echo "== Versions =="
  if [ -n "$tag" ]; then
    python3 "$CHECK_VERSION" "$tag" || die_code "$EXIT_DATAERR" "version/tag inconsistency"
  else
    python3 "$CHECK_VERSION" || die_code "$EXIT_DATAERR" "version/lock inconsistency"
  fi

  echo "== packaging/arch =="
  require_file "$PKGBUILD" "$SRCINFO"
  local version pkgver srcver
  version="$(project_version)"
  pkgver="$(pkgbuild_version)"
  srcver="$(srcinfo_version)"
  [ "$pkgver" = "$version" ] \
    || die_code "$EXIT_DATAERR" "PKGBUILD pkgver $pkgver != project version $version"
  [ "$srcver" = "$version" ] \
    || die_code "$EXIT_DATAERR" ".SRCINFO pkgver $srcver != project version $version"
  echo "  ${GREEN}OK${NC} ($version)"

  echo "== uv.lock =="
  uv lock --check || die_code "$EXIT_DATAERR" "uv.lock is stale or uv cache is unavailable"
  echo "  ${GREEN}OK${NC}"

  if [ "$no_compile" = 0 ]; then
    echo "== compileall =="
    python3 -m compileall -q "$REPO_ROOT/src" "$REPO_ROOT/tests" || die_code "$EXIT_DATAERR" "compileall failed"
    echo "  ${GREEN}OK${NC}"
  fi

  echo "== git diff --check =="
  git -C "$REPO_ROOT" diff --check || die_code "$EXIT_DATAERR" "unstaged whitespace errors"
  git -C "$REPO_ROOT" diff --cached --check || die_code "$EXIT_DATAERR" "staged whitespace errors"
  echo "  ${GREEN}OK${NC}"

  echo "== shell syntax =="
  local f
  for f in "$REPO_ROOT/manage.sh" "$REPO_ROOT"/scripts/*.sh "$REPO_ROOT"/dev/*.sh "$REPO_ROOT"/dev/hooks/*; do
    [ -f "$f" ] || continue
    bash -n "$f" || die_code "$EXIT_DATAERR" "shell syntax: $f"
  done
  echo "  ${GREEN}OK${NC}"

  if have desktop-file-validate && [ -f "$DESKTOP_IN" ]; then
    echo "== desktop entry =="
    local tmpdir tmp
    tmpdir="$(mktemp -d)"
    # desktop-file-validate insists on a *.desktop filename.
    tmp="$tmpdir/org.deepseek.DshDesktop.desktop"
    sed -e 's|@EXEC@|/usr/bin/dsh-desktop|g' \
        -e 's|@ICON@|org.deepseek.DshDesktop|g' \
        -e 's|@WMCLASS@|org.deepseek.DshDesktop|g' "$DESKTOP_IN" >"$tmp"
    if ! desktop-file-validate "$tmp"; then
      rm -rf "$tmpdir"
      die_code "$EXIT_DATAERR" "desktop entry is invalid"
    fi
    rm -rf "$tmpdir"
    echo "  ${GREEN}OK${NC}"
  fi

  if [ -n "$tag" ] && git -C "$REPO_ROOT" rev-parse -q --verify "refs/tags/$tag^{commit}" >/dev/null 2>&1; then
    [ "$(git -C "$REPO_ROOT" rev-parse "$tag^{commit}")" = "$(git -C "$REPO_ROOT" rev-parse HEAD)" ] \
      || die_code "$EXIT_CONFIG" "tag $tag does not point at HEAD"
  fi
}

cmd_test() {
  require_file "$PYPROJECT"
  local py
  py="$(python_cmd)"
  if [ $# -gt 0 ]; then
    PYTHONPATH="$REPO_ROOT/src" "$py" -m unittest "$@" || die_code "$EXIT_ERROR" "unittest failed"
  else
    PYTHONPATH="$REPO_ROOT/src" "$py" -m unittest discover -s "$REPO_ROOT/tests" \
      || die_code "$EXIT_ERROR" "unittest failed"
  fi
}

cmd_build() {
  local target="both"
  while [ $# -gt 0 ]; do
    case "$1" in
      wheel|sdist|both) target="$1"; shift ;;
      -h|--help) usage; return 0 ;;
      *) die_code "$EXIT_USAGE" "unknown build argument: $1" ;;
    esac
  done
  require_cmd uv
  require_file "$PYPROJECT" "$UV_LOCK"
  mkdir -p "$DIST_DIR"
  local -a args=(--out-dir "$DIST_DIR")
  [ "$target" = "wheel" ] && args+=(--wheel)
  [ "$target" = "sdist" ] && args+=(--sdist)
  uv build "${args[@]}" || die_code "$EXIT_ERROR" "uv build failed"
  echo
  echo "${GREEN}Artifacts:${NC}"
  ls -lh "$DIST_DIR" | sed 's/^/  /'
}

human_size() {
  if have numfmt; then numfmt --from-unit=1024 --to=iec "$1"; else printf '%s K\n' "$1"; fi
}

cmd_clean() {
  local target="cache" dry_run=0 assume_yes=0
  while [ $# -gt 0 ]; do
    case "$1" in
      cache|build|dist|arch|all) target="$1"; shift ;;
      --dry-run) dry_run=1; shift ;;
      --yes|-y) assume_yes=1; shift ;;
      -h|--help) usage; return 0 ;;
      *) die_code "$EXIT_USAGE" "unknown clean target: $1" ;;
    esac
  done

  local -a paths=()
  local -a pycache=()
  if [ "$target" = cache ] || [ "$target" = all ]; then
    paths+=(
      "$REPO_ROOT/.uv-cache"
      "$REPO_ROOT/.pytest_cache"
      "$REPO_ROOT/.mypy_cache"
      "$REPO_ROOT/.ruff_cache"
    )
    while IFS= read -r -d '' d; do
      pycache+=("$d")
    done < <(find "$REPO_ROOT/src" "$REPO_ROOT/tests" "$REPO_ROOT/dev" \
      -type d -name __pycache__ -prune -print0 2>/dev/null || true)
  fi
  if [ "$target" = build ] || [ "$target" = all ]; then
    paths+=("$REPO_ROOT/build" "$REPO_ROOT/src/dsh_desktop.egg-info")
  fi
  if [ "$target" = dist ] || [ "$target" = all ]; then
    paths+=("$DIST_DIR")
  fi
  if [ "$target" = arch ] || [ "$target" = all ]; then
    paths+=(
      "$REPO_ROOT/packaging/arch/src"
      "$REPO_ROOT/packaging/arch/pkg"
    )
    while IFS= read -r -d '' f; do
      paths+=("$f")
    done < <(find "$REPO_ROOT/packaging/arch" -maxdepth 1 \
      \( -name '*.pkg.tar.*' -o -name '*.src.tar.*' \) -print0 2>/dev/null || true)
  fi

  local existing=() total_kb=0 p size
  for p in "${paths[@]}" "${pycache[@]}"; do
    [ -e "$p" ] || continue
    case "$p" in "$REPO_ROOT"/*) ;; *) die_code "$EXIT_CONFIG" "refusing to remove outside repo: $p" ;; esac
    # Never touch the installed environment: it holds the system PyGObject link.
    case "$p" in "$VENV"|"$VENV"/*) die_code "$EXIT_CONFIG" "refusing to remove the virtualenv: $p" ;; esac
    existing+=("$p")
    size="$(du -sk "$p" 2>/dev/null | cut -f1 || echo 0)"
    total_kb=$((total_kb + ${size:-0}))
    printf '  %8s  %s\n' "$(human_size "${size:-0}")" "${p#"$REPO_ROOT"/}"
  done

  if [ "${#existing[@]}" -eq 0 ]; then
    echo "  (nothing to clean)"
    return 0
  fi
  echo "  ${BOLD}Total: $(human_size "$total_kb")${NC}"
  if [ "$dry_run" = 1 ]; then
    echo "  (dry-run: nothing removed)"
    return 0
  fi
  if [ "$assume_yes" != 1 ]; then
    [ -t 0 ] || die_code "$EXIT_USAGE" "use --yes when stdin is not a terminal"
    printf '  Remove? [y/N] '
    read -r answer || true
    case "${answer:-}" in y|Y|yes|YES) ;; *) echo "  cancelled"; return 0 ;; esac
  fi
  for p in "${existing[@]}"; do
    rm -rf -- "$p"
    echo "  ${GREEN}OK${NC} ${p#"$REPO_ROOT"/}"
  done
}

cmd_codes() {
  echo "manage.sh exit codes:"
  printf '  %3s  %s\n' 0 "OK"
  printf '  %3s  %s\n' 1 "command failure (unittest/build/bump)"
  printf '  %3s  %s\n' 64 "usage / unknown command or option"
  printf '  %3s  %s\n' 65 "invalid data / version, lock or packaging mismatch"
  printf '  %3s  %s\n' 66 "missing input file"
  printf '  %3s  %s\n' 69 "unavailable tool"
  printf '  %3s  %s\n' 70 "unexpected internal error"
  printf '  %3s  %s\n' 78 "unsafe/refused configuration"
}


cmd="${1:-help}"
shift || true
case "$cmd" in
  info) cmd_info ;;
  bump) cmd_bump "$@" ;;
  check) cmd_check "$@" ;;
  test) cmd_test "$@" ;;
  build) cmd_build "$@" ;;
  clean) cmd_clean "$@" ;;
  codes) cmd_codes ;;
  help|-h|--help) usage ;;
  *) usage >&2; die_code "$EXIT_USAGE" "unknown command: $cmd" ;;
esac
