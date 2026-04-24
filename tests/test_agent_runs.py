"""Agent run identifiers and lifecycle constants."""

import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_EXT = _REPO / "extensions" / "agent"
for _p in (_REPO, _EXT):
    _s = str(_p)
    if _s not in sys.path:
        sys.path.insert(0, _s)

from agent import runs as agent_runs  # noqa: E402


class RunIdTests(unittest.TestCase):

    def test_run_id_is_string(self):
        rid = agent_runs.new_run_id()
        self.assertIsInstance(rid, str)
        self.assertGreater(len(rid), 10)

    def test_run_ids_are_unique(self):
        ids = {agent_runs.new_run_id() for _ in range(100)}
        self.assertEqual(len(ids), 100)

    def test_run_id_format(self):
        rid = agent_runs.new_run_id()
        parts = rid.split("-")
        self.assertEqual(len(parts), 2)
        self.assertEqual(len(parts[0]), 8)
        self.assertEqual(len(parts[1]), 12)
        int(parts[0], 16)
        int(parts[1], 16)


class ConstantsTests(unittest.TestCase):

    def test_schema_version_is_int(self):
        self.assertIsInstance(agent_runs.LOG_SCHEMA_VERSION, int)
        self.assertGreaterEqual(agent_runs.LOG_SCHEMA_VERSION, 1)

    def test_status_constants_are_distinct(self):
        statuses = {
            agent_runs.STATUS_STARTED,
            agent_runs.STATUS_COMPLETED,
            agent_runs.STATUS_FAILED,
            agent_runs.STATUS_RATE_LIMITED,
        }
        self.assertEqual(len(statuses), 4)


if __name__ == "__main__":
    unittest.main()
