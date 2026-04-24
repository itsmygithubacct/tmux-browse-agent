"""Workflow config normalization and persistence."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_REPO = Path(__file__).resolve().parents[3]
_EXT = _REPO / "extensions" / "agent"
for _p in (_REPO, _EXT):
    _s = str(_p)
    if _s not in sys.path:
        sys.path.insert(0, _s)

from agent import workflows as aw  # noqa: E402
from lib import config as cfg  # noqa: E402


class WorkflowConfigTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self._patches = [
            mock.patch.object(cfg, "STATE_DIR", base),
            mock.patch.object(cfg, "AGENT_WORKFLOWS_FILE", base / "agent-workflows.json"),
            mock.patch.object(cfg, "AGENT_LOG_DIR", base / "agent-logs"),
            mock.patch.object(cfg, "PID_DIR", base / "pids"),
            mock.patch.object(cfg, "LOG_DIR", base / "logs"),
        ]
        for patch in self._patches:
            patch.start()

    def tearDown(self):
        for patch in self._patches:
            patch.stop()
        self._tmp.cleanup()

    def test_normalize_clamps_and_preserves_workflows(self):
        out = aw.normalize({
            "agents": {
                "gpt": {
                    "enabled": "true",
                    "workflows": [{
                        "name": "Sweep",
                        "prompt": "check panes",
                        "interval_seconds": 1,
                    }],
                },
            },
        })
        self.assertTrue(out["agents"]["gpt"]["enabled"])
        self.assertEqual(out["agents"]["gpt"]["workflows"][0]["interval_seconds"], 5)

    def test_save_load_round_trip(self):
        saved = aw.save({
            "agents": {
                "gpt": {
                    "enabled": True,
                    "workflows": [{"name": "Sweep", "prompt": "hi", "interval_seconds": 300}],
                },
            },
        })
        loaded = aw.load()
        self.assertEqual(saved, loaded)


if __name__ == "__main__":
    unittest.main()
