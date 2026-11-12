import importlib.util
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("bootstrap_runtime", Path(__file__).resolve().parents[1] / "scripts/bootstrap_runtime.py")
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)


class BootstrapLockTests(unittest.TestCase):
    def check_lock(self, version, wheel_hash):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "uv.lock"
            path.write_text(f'[[package]]\nname = "harbor"\nversion = "{version}"\n'
                            f'wheels = [{{hash = "sha256:{wheel_hash}"}}]\n')
            bootstrap.verify_harbor_lock(path, {"harbor": {"version": "0.25.0", "wheel_sha256": "a" * 64}})

    def test_resolved_lock_must_preserve_pinned_harbor_wheel(self):
        self.check_lock("0.25.0", "a" * 64)

    def test_same_version_different_wheel_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "wheel hash"):
            self.check_lock("0.25.0", "b" * 64)

    def test_silent_version_change_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "pinned Harbor version"):
            self.check_lock("0.26.0", "a" * 64)
