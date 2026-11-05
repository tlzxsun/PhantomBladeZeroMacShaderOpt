#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
OUT=${1:-"$ROOT/build/cache_hook.dylib"}
if [ "$(uname -s)" != Darwin ]; then echo 'This hook requires macOS.' >&2; exit 1; fi
if ! xcode-select -p >/dev/null 2>&1; then echo 'Install Command Line Tools first: xcode-select --install' >&2; exit 1; fi
mkdir -p "$(dirname -- "$OUT")"
xcrun clang -arch x86_64 -O2 -Wall -Wextra -Werror -dynamiclib -framework Metal \
    -Wl,-undefined,dynamic_lookup -Wl,-install_name,@rpath/cache_hook.dylib \
    "$ROOT/src/cache_hook.c" -o "$OUT"
codesign --force --sign - "$OUT"
codesign --verify --strict "$OUT"
printf 'Built locally: %s\n' "$OUT"
