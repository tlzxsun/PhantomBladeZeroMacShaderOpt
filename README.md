# PhantomBladeZeroMacShaderOpt

English | [简体中文](README.zh-CN.md)

An experimental, source-only workaround for repeated shader precompilation in **Phantom Blade Zero (PBZ.exe)** under CrossOver on macOS. It increases the budget passed to Metal's filesystem compiler cache from **1 GiB to 2 GiB** on the tested system.

## What has been verified

| Item | Observed result |
| --- | --- |
| Hardware | Apple M5, 24 GB RAM |
| macOS | 27.2, build `26B123` |
| CrossOver | 26.3, build `26.3.0.39832`, x86_64 Wine 11.0 |
| Game / graphics layer | Phantom Blade Zero, D3DMetal |
| Before | 52,883 live function-cache entries; 1,073,741,688 live bytes, just below 1 GiB |
| After | 119,740 live entries; 1,811,547,872 live bytes, approximately **1.69 GiB** |
| Reopening the game | The new game process opened the expanded cache with a 2 GiB budget; startup felt substantially faster |

These are observations from one machine, not controlled benchmark results. The full 2 GiB budget has not been filled and validated. Some pauses, including near 99% precompilation, remained. This is not a general FPS or stutter fix.

The path filter also recognizes Mullion's `PBZ.exe` cache, but **the successful full-game test used D3DMetal**. This release does not claim equivalent Mullion validation.

## Before installing

This changes the signatures of **two shared CrossOver Wine launchers**. The injection settings are bottle-specific, but the executable signature changes affect that CrossOver installation as a whole. The original launchers are backed up and can be restored.

- Close **all** Wine/Steam applications in every bottle before installing or restoring.
- Use your own CrossOver installation. No CrossOver, Wine, Apple, or precompiled hook binaries are distributed here.
- Install Xcode Command Line Tools (`xcode-select --install`); Python 3.9+, Clang, and `codesign` are required.
- Use a writable CrossOver installation. Do not run this installer with `sudo`.
- Restore any previous manual/experimental shader hook first. The installer refuses already-modified launchers so they cannot become a misleading “original” backup.
- Back up valuable data. The installer backs up the launchers and configuration; it does not make a full bottle or shader-cache backup.

The installer accepts the exact tested macOS/CrossOver builds by default. `--allow-untested` bypasses the version check, **not** the original-signature, architecture, or running-process checks. Private API compatibility on other builds is unknown.

## Install

Clone/download the source and open a terminal in the repository:

```sh
git clone https://github.com/tlzxsun/PhantomBladeZeroMacShaderOpt.git
cd PhantomBladeZeroMacShaderOpt
sh ./install.sh --bottle "$HOME/Library/Application Support/CrossOver/Bottles/PhantomBlade"
```

Replace `PhantomBlade` with your bottle's directory name. Select the directory containing `cxbottle.conf`, **not** its `drive_c` subdirectory. Paths containing spaces are supported.

For a non-default CrossOver location:

```sh
sh ./install.sh --app "$HOME/Applications/CrossOver.app" --bottle "/path/to/your/bottle"
```

The installer locally compiles the hook, backs up the original files, prepares ad-hoc-signed launcher copies without Hardened Runtime, and **atomically replaces the launchers at their original paths**. Atomic replacement avoids modifying existing hard-linked loader aliases in place. It does not add custom `BinPath` or `LibPath` settings or install a custom Wine launcher.

Only these bottle environment settings are managed:

```ini
"DYLD_INSERT_LIBRARIES" = "${WINEPREFIX}/phantomblade-shader-opt/<install-id>/cache_hook.dylib"
"MCH_CACHE_BYTES" = "2147483648"
"MCH_TARGET_EXE" = "PBZ.exe"
"MCH_LOG" = "${WINEPREFIX}/phantomblade-shader-opt/<install-id>/hook.log"
```

`<install-id>` is generated automatically. `${WINEPREFIX}` is expanded by CrossOver; do not substitute it manually. Any preexisting unrelated `DYLD_INSERT_LIBRARIES` value is preserved. `FS_CACHE_SIZE` and `MTL_SHADER_CACHE_SIZE` are not changed.

Start Steam and the game normally. Let precompilation finish, exit the game normally, and test a fresh launch. Installation does not automatically start Steam or the game, and does not clear or merge existing shader caches.

## Verify and restore

```sh
sh ./doctor.sh
python3 ./scripts/cache_status.py
```

The installer/doctor prints the log location. A successful interception looks like:

```text
cache-open budget=1073741824 -> 2147483648 result=0 path=.../d3dm/PBZ.exe/shaders.cache/.../functions
```

`loaded` only confirms library loading. The `cache-open` line confirms the requested budget was forwarded successfully. An `active_bytes` value above `1073741824` in `cache_status.py` confirms live indexed entries exceed the old limit. The `.data` file's physical size alone cannot prove this, because it can contain unused space. The inspection tool reads a privately observed index format; live writes or a future format change can invalidate its output.

Restore after closing every Wine/Steam process:

```sh
sh ./restore.sh
```

State and original-file backups are stored outside the repository and bottle, under:

```text
~/Library/Application Support/PhantomBladeZeroMacShaderOpt/
```

Each backup also contains a self-contained `restore.sh` and Python helper, so the repository may be moved without losing recovery. Restore verifies checksums, refuses to overwrite an unexpected CrossOver update, and preserves unrelated later bottle settings. Changes to a managed setting cause a conflict instead of being overwritten. Hook files, logs, and shader caches are kept.

Only one active managed installation is supported at a time. Restore before switching bottles or updating CrossOver. If Steam shows a blank window, flashing console windows, or the game behaves abnormally, close the bottle and restore before further testing.

## How it works and limitations

On the tested Metal build, `MTLCompilerFSCache` reads `MTL_SHADER_CACHE_SIZE`, then `FS_CACHE_SIZE`, stores the value in a 32-bit field, and clamps it to `0x40000000` (1 GiB). Raising these environment variables alone cannot bypass that clamp; some overflowing values can instead disable caching.

The hook interposes the private `fscache_open_with_key` call. For paths containing `/d3dm/PBZ.exe/` or `/mullion/PBZ.exe/`, it copies the observed 16-byte options structure and raises its first 64-bit budget field to 2 GiB. It does not patch Metal's code or edit cache contents manually. An explicitly disabled cache remains disabled. The budget applies **per matching CoreFSCache instance**, including the observed function and library caches, not to the entire game cache directory as a single total.

The library is injected into the selected bottle's Wine processes and links Metal; only the budget modification is filtered to the game cache paths. It is not process-isolated from Steam. That broader loading behavior is an experimental compatibility risk. Local ad-hoc signing is not Apple notarization and changes the vendor's original signature. No system-wide Gatekeeper/SIP settings are changed by these scripts.

The hook and cache index reader use private implementation details observed on one macOS build. Revalidation is required after macOS/CrossOver updates. Normal cache invalidation after game/driver changes can still occur. The observed game log begins with `PBZLOGE1`; this project does not decode that format or claim a cause for every loading pause.

## Development

```sh
sh ./scripts/build.sh
python3 -m unittest discover -s tests -v
```

Tests cover configuration preservation/conflicts, backup validation, hard-link-safe replacement, and live cache record accounting. They use temporary files and do not install into your real CrossOver application.

References: [Apple: DYLD environment entitlement](https://developer.apple.com/documentation/bundleresources/entitlements/com.apple.security.cs.allow-dyld-environment-variables), [Apple: software notarization](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution), [CrossOver EULA](https://www.codeweavers.com/crossover/eula). The private cache cap and binary layout described above come from local investigation, not an Apple-supported API contract.
