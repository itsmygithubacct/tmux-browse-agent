"""Per-REPL context persistence and render helpers."""

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

from agent import repl_context as ctx_mod  # noqa: E402
from lib import config as cfg  # noqa: E402


class _IsolatedCtx:
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._patches = [
            mock.patch.object(cfg, "STATE_DIR", root),
            mock.patch.object(cfg, "AGENT_CONTEXT_DIR", root / "ctx"),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self._tmp.cleanup()


class LoadSaveTests(_IsolatedCtx, unittest.TestCase):

    def test_default_when_missing(self):
        ctx = ctx_mod.load("opus")
        self.assertEqual(ctx["mode"], "observe")
        self.assertEqual(ctx["observed_panes"], [])
        self.assertEqual(ctx["exec_target"], "")
        self.assertEqual(ctx["tick_sec"], ctx_mod.DEFAULT_TICK_SEC)

    def test_save_and_reload(self):
        ctx_mod.save("opus", {
            "exec_target": "work:",
            "observed_panes": ["dash:", "build:"],
            "mode": "act",
            "tick_sec": 15,
        })
        ctx = ctx_mod.load("opus")
        self.assertEqual(ctx["exec_target"], "work:")
        self.assertEqual(ctx["observed_panes"], ["dash:", "build:"])
        self.assertEqual(ctx["mode"], "act")
        self.assertEqual(ctx["tick_sec"], 15)

    def test_invalid_mode_falls_back_to_observe(self):
        ctx_mod.save("opus", {"mode": "act"})
        # Hand-corrupt the file to an invalid mode, load should normalize.
        path = cfg.AGENT_CONTEXT_DIR / "opus.json"
        path.write_text('{"mode":"dance"}')
        ctx = ctx_mod.load("opus")
        self.assertEqual(ctx["mode"], "observe")

    def test_observed_pane_cap(self):
        many = [f"p{i}:" for i in range(20)]
        ctx_mod.save("opus", {"observed_panes": many})
        ctx = ctx_mod.load("opus")
        self.assertEqual(len(ctx["observed_panes"]), ctx_mod.MAX_OBSERVED_PANES)

    def test_add_observed_dedupes(self):
        ctx_mod.add_observed("opus", "dash:")
        ctx_mod.add_observed("opus", "dash:")
        ctx = ctx_mod.load("opus")
        self.assertEqual(ctx["observed_panes"], ["dash:"])

    def test_add_observed_respects_cap(self):
        for i in range(ctx_mod.MAX_OBSERVED_PANES):
            ctx_mod.add_observed("opus", f"p{i}:")
        with self.assertRaises(ValueError):
            ctx_mod.add_observed("opus", "extra:")

    def test_remove_observed(self):
        ctx_mod.add_observed("opus", "a:")
        ctx_mod.add_observed("opus", "b:")
        ctx_mod.remove_observed("opus", "a:")
        self.assertEqual(ctx_mod.load("opus")["observed_panes"], ["b:"])

    def test_tick_minimum(self):
        ctx_mod.set_tick("opus", 1)
        self.assertEqual(ctx_mod.load("opus")["tick_sec"], ctx_mod.MIN_TICK_SEC)


class RenderBlockTests(_IsolatedCtx, unittest.TestCase):

    def test_empty_default_ctx_renders_blank(self):
        ctx = ctx_mod.load("opus")
        self.assertEqual(ctx_mod.render_block(ctx), "")

    def test_populated_ctx_renders_block(self):
        ctx = ctx_mod.save("opus", {
            "exec_target": "work:",
            "observed_panes": ["build:", "logs:"],
            "mode": "watch",
        })
        block = ctx_mod.render_block(ctx)
        self.assertIn("REPL context:", block)
        self.assertIn("work:", block)
        self.assertIn("build:", block)
        self.assertIn("watch", block)

    def test_observe_mode_is_omitted(self):
        ctx = ctx_mod.save("opus", {"exec_target": "w:", "mode": "observe"})
        block = ctx_mod.render_block(ctx)
        self.assertIn("w:", block)
        self.assertNotIn("Mode:", block)


if __name__ == "__main__":
    unittest.main()
