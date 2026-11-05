import importlib.util
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


manage = load("manage", ROOT / "scripts/manage.py")
cache = load("cache_status", ROOT / "scripts/cache_status.py")


class ConfigurationTests(unittest.TestCase):
    original = '[Bottle]\n"WineArch" = "win64"\n[EnvironmentVariables]\n"FS_CACHE_SIZE" = "4294901760"\n'

    def test_install_preserves_old_dyld_and_never_changes_runtime_paths(self):
        original = self.original + '"DYLD_INSERT_LIBRARIES" = "/a path/profile.dylib"\n'
        installed, changes = manage.prepare_config(original, "test")
        self.assertIn('cache_hook.dylib:/a path/profile.dylib"', installed)
        self.assertNotIn("BinPath", installed)
        self.assertNotIn("LibPath", installed)
        self.assertEqual(manage.restored_config(installed, original, installed, changes), original)

    def test_restore_keeps_unrelated_later_changes(self):
        installed, changes = manage.prepare_config(self.original, "test")
        current = installed + '"UNRELATED" = "keep"\n'
        restored = manage.restored_config(current, self.original, installed, changes)
        self.assertIn('"UNRELATED" = "keep"', restored)
        self.assertNotIn("MCH_LOG", restored)

    def test_restore_refuses_changed_managed_settings(self):
        installed, changes = manage.prepare_config(self.original, "test")
        with self.assertRaisesRegex(ValueError, "Managed setting changed"):
            manage.restored_config(installed.replace('"2147483648"', '"1073741824"'),
                                   self.original, installed, changes)

    def test_duplicate_and_legacy_settings_are_rejected(self):
        for extra in ['"MCH_CACHE_BYTES" = "1"\n',
                      '"DYLD_INSERT_LIBRARIES" = "${WINEPREFIX}/metal-cache-hook/cache_hook.dylib"\n',
                      '"DYLD_INSERT_LIBRARIES" = "a"\n"DYLD_INSERT_LIBRARIES" = "b"\n']:
            with self.assertRaises(ValueError):
                manage.prepare_config(self.original + extra, "test")

    def test_crlf_is_kept_and_restored_exactly(self):
        original = self.original.replace("\n", "\r\n")
        installed, changes = manage.prepare_config(original, "test")
        self.assertNotIn("\n", installed.replace("\r\n", ""))
        self.assertEqual(manage.restored_config(installed, original, installed, changes), original)


class FileSafetyTests(unittest.TestCase):
    def test_atomic_replacement_does_not_modify_other_hard_links(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "wine"
            alias = root / "cached-alias"
            source = root / "prepared"
            target.write_bytes(b"official original")
            os.link(target, alias)
            source.write_bytes(b"local signed copy")
            manage.atomic_copy(source, target)
            self.assertEqual(alias.read_bytes(), b"official original")
            self.assertEqual(target.read_bytes(), b"local signed copy")

    def make_manifest(self, root):
        cfg = root / "cxbottle.conf"
        original = ConfigurationTests.original
        installed, changes = manage.prepare_config(original, "test")
        cfg.write_text(installed)
        (root / "cxbottle.conf.original").write_text(original)
        (root / "cxbottle.conf.installed").write_text(installed)
        records = []
        for n in range(2):
            target = root / f"target-{n}"
            saved = root / f"saved-{n}"
            target.write_bytes(b"modified")
            saved.write_bytes(b"original")
            records.append({"target": str(target), "backup": str(saved),
                            "original_sha256": manage.digest(saved),
                            "installed_sha256": manage.digest(target)})
        return {"config": str(cfg), "config_mode": 0o600, "backup_dir": str(root),
                "changes": changes, "files": records,
                "config_checksums": {name: manage.digest(root / name) for name in
                                     ("cxbottle.conf.original", "cxbottle.conf.installed")}}

    def test_restore_validates_all_backups_before_touching_first_launcher(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self.make_manifest(root)
            Path(manifest["files"][1]["backup"]).write_bytes(b"damaged")
            with self.assertRaisesRegex(RuntimeError, "Backup checksum"):
                manage.restore_manifest(manifest)
            self.assertEqual((root / "target-0").read_bytes(), b"modified")

    def test_restore_does_not_overwrite_a_crossover_update(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self.make_manifest(root)
            (root / "target-1").write_bytes(b"new vendor version")
            with self.assertRaisesRegex(RuntimeError, "CrossOver changed"):
                manage.restore_manifest(manifest)
            self.assertEqual((root / "target-0").read_bytes(), b"modified")

    def test_complete_restore_and_config_conflict_preflight(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self.make_manifest(root)
            cfg = root / "cxbottle.conf"
            cfg.write_text(cfg.read_text().replace('"2147483648"', '"1"'))
            with self.assertRaises(ValueError):
                manage.restore_manifest(manifest)
            self.assertEqual((root / "target-0").read_bytes(), b"modified")
            cfg.write_bytes((root / "cxbottle.conf.installed").read_bytes())
            manage.restore_manifest(manifest)
            self.assertEqual((root / "target-0").read_bytes(), b"original")
            self.assertEqual(cfg.read_bytes(), (root / "cxbottle.conf.original").read_bytes())

    def test_wine_detection_handles_spaces_without_reading_environments(self):
        text = "12 /a path/bin/wine\n13 C:\\Program Files (x86)\\Steam\\steam.exe\n14 /Applications/CrossOver.app/Contents/MacOS/CrossOver\n"
        with patch.object(manage, "run") as run:
            run.return_value.stdout = text
            self.assertEqual(manage.wine_processes(), [("12", "wine"), ("13", "steam.exe")])
            self.assertEqual(run.call_args.args[0], ["/bin/ps", "-axo", "pid=,comm="])


class CacheInspectionTests(unittest.TestCase):
    def test_sandboxed_cache_discovery_can_use_darwin_tmpdir(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "T").mkdir()
            (root / "C").mkdir()
            with patch.object(cache.subprocess, "run", side_effect=OSError("sandbox")), \
                 patch.dict(cache.os.environ, {"TMPDIR": str(root / "T") + "/"}):
                self.assertEqual(cache.default_cache_root(), root / "C")

    def test_replacement_and_tombstone_counts_are_live_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "functions.list"
            key1, key2 = b"a" * 32, b"b" * 32
            header = bytes.fromhex("45362718") + b"x" * 32 + struct.pack("<Q", 2)
            record = lambda size, offset, key, flag: struct.pack("<QQ32sQ", size, offset, key, flag)
            path.write_bytes(header + record(100, 0, key1, 0) + record(200, 128, key2, 0)
                             + record(300, 384, key1, 0) + record(0, 0, key2, 1))
            self.assertEqual(cache.inspect(path)["entries"], 1)
            self.assertEqual(cache.inspect(path)["active_bytes"], 300)

    def test_unknown_format_is_not_silently_parsed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "functions.list"
            path.write_bytes(b"not this format" * 8)
            with self.assertRaises(ValueError):
                cache.inspect(path)


class InstallRollbackTests(unittest.TestCase):
    def exercise(self, failure):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = root / "CrossOver.app"
            app.mkdir()
            bottle = root / "Bottle with spaces"
            bottle.mkdir()
            cfg = bottle / "cxbottle.conf"
            cfg.write_text(ConfigurationTests.original)
            targets = [app / "wineloader", app / "wine"]
            for target in targets:
                target.write_bytes(b"original")
            (root / "build").mkdir()
            (root / "build/cache_hook.dylib").write_bytes(b"test hook")
            args = SimpleNamespace(app=app, bottle=bottle, state_dir=root / "state", allow_untested=False)
            actual_copy = manage.atomic_copy
            stopped_calls = 0

            def stopped():
                nonlocal stopped_calls
                stopped_calls += 1
                if failure == "concurrent-config-edit" and stopped_calls == 2:
                    cfg.write_text(cfg.read_text() + '"NEW_SETTING" = "keep"\n')

            def fake_run(argv, **kwargs):
                argv = [str(x) for x in argv]
                if argv[0] == "sw_vers":
                    return SimpleNamespace(stdout=manage.TESTED_MAC_BUILD, stderr="")
                if argv[0] == "codesign" and "--force" in argv:
                    path = Path(argv[-1])
                    path.write_bytes(path.read_bytes() + b"-locally-signed")
                return SimpleNamespace(stdout="wine-11.0", stderr="no hook loaded")

            def copy(source, target):
                if failure == "second-replacement" and Path(target) == targets[1] and str(source).endswith(".prepared"):
                    raise OSError("Simulated second replacement failure")
                actual_copy(source, target)

            with patch.object(manage, "PROJECT", root), \
                 patch.object(manage.os, "geteuid", return_value=501), \
                 patch.object(manage, "require_stopped", side_effect=stopped), \
                 patch.object(manage, "app_info", return_value=(app, app, {"CFBundleVersion": manage.TESTED_CROSSOVER_BUILD}, targets)), \
                 patch.object(manage, "verify_original"), \
                 patch.object(manage, "run", side_effect=fake_run), \
                 patch.object(manage, "atomic_copy", side_effect=copy):
                with self.assertRaises((OSError, RuntimeError)):
                    manage.install(args)
            for target in targets:
                self.assertEqual(target.read_bytes(), b"original")
            if failure == "concurrent-config-edit":
                self.assertIn('"NEW_SETTING" = "keep"', cfg.read_text())
            else:
                self.assertEqual(cfg.read_text(), ConfigurationTests.original)
            self.assertEqual(json.loads((root / "state/install.json").read_text())["phase"], "restored")

    def test_failed_smoke_check_rolls_back_launchers_and_configuration(self):
        self.exercise("smoke-check")

    def test_failed_second_replacement_rolls_back_first(self):
        self.exercise("second-replacement")

    def test_concurrent_config_edit_is_not_lost(self):
        self.exercise("concurrent-config-edit")


if __name__ == "__main__":
    unittest.main()
