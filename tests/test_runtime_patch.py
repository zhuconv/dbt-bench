from pathlib import Path
import json
import os
import shlex
import subprocess
import tempfile
import textwrap
import unittest


class RuntimeRootPatchTests(unittest.TestCase):
    def run_command(self, uid, command):
        root = Path(__file__).resolve().parents[1]
        patch = json.loads((root / "patches/harbor-025-apptainer-exec-v1/metadata.json").read_text())
        scope = {"command": command, "resolved_user": "root", "user_arg": "root", "shlex": shlex}
        exec(textwrap.dedent(patch["files"][0]["after_text"]), scope)
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            for name, content in {"id": f"#!/bin/sh\nprintf '{uid}\\n'\n",
                                  "su": "#!/bin/sh\nprintf 'SU_REQUESTED\\n'\nexit 42\n"}.items():
                path = directory / name
                path.write_text(content)
                path.chmod(0o755)
            return subprocess.run(["/bin/bash", "-c", scope["command"]], capture_output=True, text=True,
                                  env={**os.environ, "PATH": str(directory) + ":/usr/bin:/bin"}, timeout=10)

    def test_already_root_preserves_failure_without_retrying_su(self):
        result = self.run_command(0, "printf 'ORIGINAL_COMMAND\\n'; exit 37")
        self.assertEqual(result.returncode, 37)
        self.assertEqual(result.stdout, "ORIGINAL_COMMAND\n")

    def test_nonroot_does_not_silently_skip_requested_identity_change(self):
        result = self.run_command(1234, "printf 'SHOULD_NOT_RUN\\n'")
        self.assertEqual(result.returncode, 42)
        self.assertEqual(result.stdout, "SU_REQUESTED\n")
