#!/usr/bin/env python3
"""Source-only installer for the tested direct-launcher replacement workaround."""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from datetime import datetime
import uuid

PROJECT = Path(__file__).resolve().parent.parent
STATE_DEFAULT = Path.home() / "Library/Application Support/PhantomBladeZeroMacShaderOpt"
TESTED_MAC_BUILD = "26B123"
TESTED_CROSSOVER_BUILD = "26.3.0.39832"
VENDOR_TEAM = "9C6B7X7Z8E"
MANAGED_KEYS = ("DYLD_INSERT_LIBRARIES", "MCH_CACHE_BYTES", "MCH_TARGET_EXE", "MCH_LOG")


def run(args, **kwargs):
    return subprocess.run([str(x) for x in args], check=True, capture_output=True,
                          text=True, **kwargs)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def atomic_bytes(path, content, mode=0o600):
    """Replace the directory entry; never mutate a hard-linked executable in place."""
    path = Path(path)
    fd, name = tempfile.mkstemp(prefix=".pbz-opt-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def atomic_copy(source, target):
    target = Path(target)
    fd, name = tempfile.mkstemp(prefix=".pbz-opt-", dir=target.parent)
    os.close(fd)
    try:
        shutil.copy2(source, name)
        os.replace(name, target)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def save_json(path, value):
    atomic_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())


def entries(text, section, key):
    current = ""
    found = []
    for i, line in enumerate(text.splitlines(keepends=True)):
        heading = re.match(r"^\s*\[([^\]]+)\]", line)
        if heading:
            current = heading[1]
        elif current == section:
            field = re.match(r'^\s*"' + re.escape(key) + r'"\s*=\s*"((?:[^"\\]|\\.)*)"\s*$', line)
            if field:
                found.append((i, line, field[1]))
    if len(found) > 1:
        raise ValueError(f"Duplicate setting: [{section}] {key}")
    return found[0] if found else None


def set_entry(text, section, key, line):
    lines = text.splitlines(keepends=True)
    old = entries(text, section, key)
    if old:
        lines[old[0]:old[0] + 1] = [line] if line is not None else []
        return "".join(lines)
    if line is None:
        return text
    for i, value in enumerate(lines):
        if re.match(r"^\s*\[" + re.escape(section) + r"\]", value):
            end = i + 1
            while end < len(lines) and not re.match(r"^\s*\[", lines[end]):
                end += 1
            if end and not lines[end - 1].endswith("\n"):
                lines[end - 1] += "\n"
            lines.insert(end, line)
            return "".join(lines)
    return text + ("" if text.endswith("\n") else "\n") + f"\n[{section}]\n" + line


def prepare_config(original, install_id):
    for key in MANAGED_KEYS[1:]:
        if entries(original, "EnvironmentVariables", key):
            raise ValueError("Existing shader hook found; restore it first.")
    dyld = entries(original, "EnvironmentVariables", MANAGED_KEYS[0])
    prior = dyld[2] if dyld else ""
    if "metal-cache-hook" in prior or "phantomblade-shader-opt" in prior:
        raise ValueError("Existing shader hook found; restore it first.")
    relative = "${WINEPREFIX}/phantomblade-shader-opt/" + install_id
    values = {
        "DYLD_INSERT_LIBRARIES": relative + "/cache_hook.dylib" + (":" + prior if prior else ""),
        "MCH_CACHE_BYTES": "2147483648",
        "MCH_TARGET_EXE": "PBZ.exe",
        "MCH_LOG": relative + "/hook.log",
    }
    updated = original
    changes = []
    newline = "\r\n" if "\r\n" in original else "\n"
    for key, value in values.items():
        old = entries(updated, "EnvironmentVariables", key)
        # 'prior' is already escaped in CrossOver's configuration syntax.
        line = f'"{key}" = "{value}"' + newline
        changes.append({"key": key, "previous": old[1] if old else None, "installed": line})
        updated = set_entry(updated, "EnvironmentVariables", key, line)
    return updated, changes


def restored_config(current, original, installed, changes):
    if current in (original, installed):
        return original
    result = current
    for edit in changes:
        item = entries(result, "EnvironmentVariables", edit["key"])
        if not item or item[1] != edit["installed"]:
            raise ValueError("Managed setting changed; refusing to overwrite: " + edit["key"])
        result = set_entry(result, "EnvironmentVariables", edit["key"], edit["previous"])
    return result


def wine_processes():
    # Do not read or print process environments, Steam tokens, or command arguments.
    rows = run(["/bin/ps", "-axo", "pid=,comm="]).stdout
    result = []
    for line in rows.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) != 2:
            continue
        name = fields[1].replace("\\", "/").rsplit("/", 1)[-1].lower()
        if name in {"wine", "wine64", "wineloader", "wineserver", "wine-preloader"} or name.endswith(".exe"):
            result.append((fields[0], name))
    return result


def require_stopped():
    active = wine_processes()
    if active:
        raise RuntimeError("Quit ALL Wine/Steam bottles first.\n" +
                           "\n".join(f"PID {pid}: {name}" for pid, name in active))


def app_info(app):
    app = app.expanduser().resolve()
    with (app / "Contents/Info.plist").open("rb") as stream:
        info = plistlib.load(stream)
    co = app / "Contents/SharedSupport/CrossOver"
    targets = [(co / "bin/wineloader").resolve(), (co / "lib/wine/x86_64-unix/wine").resolve()]
    for target in targets:
        if not target.is_relative_to(app) or not target.is_file():
            raise ValueError("Unexpected launcher path: " + str(target))
    return app, co, info, targets


def verify_original(target):
    run(["codesign", "--verify", "--strict", target])
    details = run(["codesign", "-dv", target]).stderr
    if f"TeamIdentifier={VENDOR_TEAM}" not in details or "(runtime)" not in details:
        raise RuntimeError("Launcher is already modified or unrecognized; restore it first. "
                           "Path: " + str(target))
    if "x86_64" not in run(["file", target]).stdout:
        raise RuntimeError("Only x86_64 Wine is supported.")


@contextlib.contextmanager
def state_lock(state_dir):
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (state_dir / ".lock").open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def restore_manifest(manifest, check_changes=True):
    """Preflight every file before restoring anything; keep logs/cache untouched."""
    backup = Path(manifest["backup_dir"])
    for name, expected in manifest["config_checksums"].items():
        if digest(backup / name) != expected:
            raise RuntimeError("Configuration backup checksum mismatch.")
    for item in manifest["files"]:
        if digest(item["backup"]) != item["original_sha256"]:
            raise RuntimeError("Backup checksum mismatch.")
        if check_changes and digest(item["target"]) not in (item["original_sha256"], item["installed_sha256"]):
            raise RuntimeError("CrossOver changed since installation; do not overwrite the new version. "
                               "Path: " + item["target"])
    cfg = Path(manifest["config"])
    config_bytes = None
    if cfg.exists():
        original = (backup / "cxbottle.conf.original").read_bytes().decode("utf-8")
        installed = (backup / "cxbottle.conf.installed").read_bytes().decode("utf-8")
        config_bytes = restored_config(cfg.read_bytes().decode("utf-8"), original, installed,
                                       manifest["changes"]).encode("utf-8")
    for item in manifest["files"]:
        atomic_copy(item["backup"], item["target"])
    if config_bytes is not None:
        atomic_bytes(cfg, config_bytes, manifest["config_mode"])


def install(args):
    if os.geteuid() == 0:
        raise RuntimeError("Do not run with sudo. Use a user-writable CrossOver installation.")
    require_stopped()
    app, co, info, targets = app_info(args.app)
    mac_build = run(["sw_vers", "-buildVersion"]).stdout.strip()
    if (mac_build != TESTED_MAC_BUILD or info.get("CFBundleVersion") != TESTED_CROSSOVER_BUILD) and not args.allow_untested:
        raise RuntimeError("Untested version. Tested: macOS build 26B123, CrossOver 26.3.0.39832. "
                           "Read the limitations before using --allow-untested.")
    for target in targets:
        verify_original(target)
        if not os.access(target.parent, os.W_OK):
            raise RuntimeError("CrossOver is not writable: " + str(target.parent))
    bottle = args.bottle.expanduser().resolve()
    cfg = bottle / "cxbottle.conf"
    if not cfg.is_file() or cfg.is_symlink():
        raise ValueError("Choose a local bottle root containing cxbottle.conf.")
    original = cfg.read_bytes().decode("utf-8")
    arch = entries(original, "Bottle", "WineArch")
    if not arch or arch[2] != "win64":
        raise ValueError("Only win64 bottles are supported.")
    install_id = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
    updated, changes = prepare_config(original, install_id)
    state_dir = args.state_dir.expanduser().resolve()
    with state_lock(state_dir):
        active_file = state_dir / "install.json"
        if active_file.exists() and json.loads(active_file.read_text())["phase"] != "restored":
            raise RuntimeError("An installation/transaction already exists; restore it first.")
        run(["sh", PROJECT / "scripts/build.sh"])
        hook_dir = bottle / "phantomblade-shader-opt" / install_id
        backup = state_dir / "backups" / install_id
        backup.mkdir(parents=True, mode=0o700)
        shutil.copy2(cfg, backup / "cxbottle.conf.original")
        atomic_bytes(backup / "cxbottle.conf.installed", updated.encode())
        files = []
        for index, target in enumerate(targets):
            saved = backup / f"launcher-{index}.original"
            prepared = backup / f"launcher-{index}.prepared"
            shutil.copy2(target, saved)
            shutil.copy2(target, prepared)
            run(["codesign", "--force", "--sign", "-", "--options", "0", prepared])
            run(["codesign", "--verify", "--strict", prepared])
            files.append({"target": str(target), "backup": str(saved), "prepared": str(prepared),
                          "original_sha256": digest(saved), "installed_sha256": digest(prepared)})
        manifest = {"version": 1, "phase": "prepared", "app": str(app), "config": str(cfg),
                    "config_mode": stat.S_IMODE(cfg.stat().st_mode), "backup_dir": str(backup),
                    "hook_dir": str(hook_dir), "changes": changes, "files": files,
                    "config_checksums": {name: digest(backup / name) for name in
                                         ("cxbottle.conf.original", "cxbottle.conf.installed")}}
        shutil.copy2(__file__, backup / "restore.py")
        command = 'exec python3 "$HERE/restore.py" restore --state-dir ' + shlex.quote(str(state_dir))
        atomic_bytes(backup / "restore.sh", ('#!/bin/sh\nset -eu\nHERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)\n' + command + '\n').encode(), 0o700)
        save_json(backup / "manifest.json", manifest)
        save_json(active_file, manifest)  # Recoverable even if interrupted after this point.
        try:
            require_stopped()
            if cfg.read_bytes().decode() != original or any(digest(i["target"]) != i["original_sha256"] for i in files):
                raise RuntimeError("Files changed during preparation.")
            hook_dir.mkdir(parents=True, mode=0o700)
            shutil.copy2(PROJECT / "build/cache_hook.dylib", hook_dir / "cache_hook.dylib")
            for item in files:
                atomic_copy(item["prepared"], item["target"])
            atomic_bytes(cfg, updated.encode(), manifest["config_mode"])
            env = os.environ.copy()
            env.update(DYLD_INSERT_LIBRARIES=str(hook_dir / "cache_hook.dylib"),
                       MCH_CACHE_BYTES="2147483648", MCH_TARGET_EXE="PBZ.exe", MCH_LOG=str(hook_dir / "hook.log"))
            smoke = run([co / "bin/wineloader", "--version"], env=env, timeout=15)
            if "metal-cache-hook" not in smoke.stderr or "loaded" not in smoke.stderr:
                raise RuntimeError("Hook did not load.")
            manifest["phase"] = "installed"
            save_json(active_file, manifest)
            save_json(backup / "manifest.json", manifest)
        except BaseException:
            # Do not roll back an unrelated concurrent edit detected before replacement.
            if any(digest(i["target"]) == i["installed_sha256"] for i in files):
                restore_manifest(manifest)
            manifest["phase"] = "restored"
            save_json(active_file, manifest)
            raise
    print("Installed. Budget: 2 GiB per matching cache.")
    print("Backup:", backup)
    print("Log:", hook_dir / "hook.log")
    print("Restore: sh", shlex.quote(str(backup / "restore.sh")))
    print("Launch the game yourself; finish compilation and exit normally.")


def restore(args):
    require_stopped()
    state_dir = args.state_dir.expanduser().resolve()
    if not (state_dir / "install.json").exists():
        raise ValueError("No managed installation found.")
    with state_lock(state_dir):
        active_file = state_dir / "install.json"
        manifest = json.loads(active_file.read_text())
        if manifest["phase"] == "restored":
            print("Already restored.")
            return
        restore_manifest(manifest)
        manifest["phase"] = "restored"
        save_json(active_file, manifest)
        save_json(Path(manifest["backup_dir"]) / "manifest.json", manifest)
    print("Original launchers/configuration restored; shader caches and logs kept.")


def doctor(args):
    app, co, info, targets = app_info(args.app)
    print("App:", app, "\nCrossOver:", info.get("CFBundleVersion"))
    print("macOS build:", run(["sw_vers", "-buildVersion"]).stdout.strip())
    print("Active Wine processes:", wine_processes())
    for target in targets:
        details = run(["codesign", "-dv", target]).stderr
        print(target)
        print("\n".join(line for line in details.splitlines() if "flags=" in line or "TeamIdentifier=" in line))
    state = args.state_dir.expanduser().resolve() / "install.json"
    if state.exists():
        manifest = json.loads(state.read_text())
        print("Managed phase:", manifest["phase"])
        print("Log:", Path(manifest["hook_dir"]) / "hook.log")
    else:
        print("No managed installation. Legacy/manual patches are not auto-adopted.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "restore", "doctor"))
    parser.add_argument("--app", type=Path, default=Path("/Applications/CrossOver.app"))
    parser.add_argument("--bottle", type=Path, help="Bottle ROOT, not drive_c")
    parser.add_argument("--state-dir", type=Path, default=STATE_DEFAULT)
    parser.add_argument("--allow-untested", action="store_true")
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("macOS only")
    if args.action == "install" and not args.bottle:
        parser.error("install requires --bottle")
    try:
        globals()[args.action](args)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print("ERROR:", exc, file=sys.stderr)
        if isinstance(exc, subprocess.CalledProcessError):
            print(exc.stderr or "", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
