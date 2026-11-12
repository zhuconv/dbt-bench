from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

from dbt_bench.environment import assess_contexts
from dbt_bench.io import evidence_ref, write_json
from tests.helpers import REPO


class EnvironmentTests(unittest.TestCase):
    def probe(self):
        return {"python": {"executable": "/opt/py312/bin/python", "version": "3.12"},
                "duckdb_module": {"origin": "/opt/py312/lib/duckdb.py"}, "duckdb_version": "1.2.0",
                "dbt": {"path": "/opt/py312/bin/dbt", "shebang": "#!/opt/py312/bin/python", "version": {"returncode": 0}},
                "environment": {"DBT_PROFILES_DIR": "/app"}, "databases": [{"path": "/app/result.duckdb", "readable": True}]}

    def test_import_success_not_cross_context_evidence(self):
        self.assertEqual(assess_contexts(self.probe(), self.probe(), None, root=Path.cwd())["status"], "pending")

    def test_initial_python_path_mismatch(self):
        agent = self.probe()
        agent["python"] = {"executable": "/usr/bin/python3", "version": "3.13.5"}
        agent["duckdb_module"] = {"origin": None}
        agent["duckdb_error"] = "No module named duckdb"
        self.assertEqual(assess_contexts(agent, self.probe(), None, root=Path.cwd())["status"], "fail")

    def test_database_format_or_profile_failure(self):
        verifier = self.probe()
        verifier["databases"][0]["error"] = "Cannot read storage version"
        self.assertEqual(assess_contexts(self.probe(), verifier, None, root=Path.cwd())["status"], "fail")

    def test_dependency_damage_not_ignored_as_retry(self):
        verifier = self.probe()
        verifier["dbt"]["version"]["returncode"] = 1
        self.assertEqual(assess_contexts(self.probe(), verifier, None, root=Path.cwd())["status"], "fail")

    def test_compatible_different_versions_need_explanation_and_bridge(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_json(root / "bridge.json", {"fixture": True, "read": "ok"})
            bridge = {"evidence": evidence_ref(root / "bridge.json", root), "producer_sha256": "abc", "consumer_sha256": "abc", "rows_match": True, "native_transfer_mode": "shared"}
            verifier = self.probe()
            verifier["python"]["version"] = "3.13"
            self.assertEqual(assess_contexts(self.probe(), verifier, bridge, root=root)["status"], "pending")
            bridge["difference_explanation"] = "Fixture: distinct interpreters read identical declared output"
            self.assertEqual(assess_contexts(self.probe(), verifier, bridge, root=root)["status"], "pass")

    def test_probe_reports_actual_executable_without_leaking_api_key(self):
        env = {**os.environ, "OPENAI_API_KEY": "sentinel-must-not-appear"}
        p = subprocess.run([sys.executable, str(REPO / "scripts/context_probe.py")], capture_output=True, text=True, check=True, env=env)
        probe = json.loads(p.stdout)
        self.assertEqual(probe["python"]["executable"], sys.executable)
        self.assertNotIn("sentinel-must-not-appear", p.stdout)
