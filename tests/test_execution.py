import errno
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("check_execution", Path(__file__).resolve().parents[1] / "scripts/check_execution.py")
execution = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(execution)


class ExecutionTests(unittest.TestCase):
    def test_compute_allocation_does_not_override_syscall_denial(self):
        record = {"allocation": {"job_id": "123"}, "sockets": [{"error": {"errno": errno.EPERM}}],
                  "apptainer": {"available": True}, "network": {"status": "reachable"},
                  "container_smoke": {"runs": [{"passed": True}]}, "slurm_query": {"status": "confirmed"}}
        result = execution.diagnose(record)
        self.assertFalse(result["execution_conditions_ready"])
        self.assertEqual(result["blockers"], ["process_socket_operations_denied"])

    def test_permission_failure_is_not_reported_as_dns_outage(self):
        with patch.object(execution.urllib.request, "urlopen") as request:
            result = execution.network_checks([{"error": {"errno": errno.EPERM}}])
            request.assert_not_called()
            self.assertEqual(result["status"], "blocked_by_socket_permissions")

    def test_live_job_and_actual_container_execution_are_required(self):
        record = {"allocation": {"job_id": "123"}, "sockets": [], "apptainer": {"available": True},
                  "network": {"status": "reachable"}, "container_smoke": {"runs": [{"passed": True}]},
                  "slurm_query": {"status": "confirmed"}}
        self.assertTrue(execution.diagnose(record)["execution_conditions_ready"])
        record["slurm_query"]["status"] = "not_requested"
        self.assertFalse(execution.diagnose(record)["execution_conditions_ready"])
        record["container_smoke"]["runs"][0]["passed"] = False
        self.assertIn("apptainer_execution_not_confirmed", execution.diagnose(record)["blockers"])

    def test_socket_errors_preserve_errno_and_stage(self):
        with patch.object(execution.socket, "socket", side_effect=PermissionError(errno.EACCES, "permission denied")):
            results = execution.probe_sockets()
        self.assertEqual(len(results), 3)
        self.assertTrue(all(r["error"]["stage"] == "socket" and r["error"]["errno"] == errno.EACCES for r in results))
