"""Token budget enforcement tests."""

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

_REPO = Path(__file__).resolve().parents[3]
_EXT = _REPO / "extensions" / "agent"
for _p in (_REPO, _EXT):
    _s = str(_p)
    if _s not in sys.path:
        sys.path.insert(0, _s)

from agent import budgets as ab  # noqa: E402


class RunBudgetTests(unittest.TestCase):

    def test_under_limit(self):
        r = ab.check_run_budget("gpt", {"total_tokens": 500}, 1000)
        self.assertEqual(r["action"], ab.ACTION_OK)
        self.assertEqual(r["used"], 500)

    def test_at_80_pct(self):
        r = ab.check_run_budget("gpt", {"total_tokens": 800}, 1000)
        self.assertEqual(r["action"], ab.ACTION_WARN)
        self.assertIn("80", r["reason"])

    def test_at_100_pct(self):
        r = ab.check_run_budget("gpt", {"total_tokens": 1000}, 1000)
        self.assertEqual(r["action"], ab.ACTION_STOP)

    def test_over_limit(self):
        r = ab.check_run_budget("gpt", {"total_tokens": 1500}, 1000)
        self.assertEqual(r["action"], ab.ACTION_STOP)
        self.assertIn("exceeded", r["reason"])

    def test_zero_means_unlimited(self):
        r = ab.check_run_budget("gpt", {"total_tokens": 999999}, 0)
        self.assertEqual(r["action"], ab.ACTION_OK)

    def test_uses_prompt_completion_fallback(self):
        r = ab.check_run_budget("gpt",
            {"prompt_tokens": 600, "completion_tokens": 200}, 1000)
        self.assertEqual(r["used"], 800)
        self.assertEqual(r["action"], ab.ACTION_WARN)

    def test_prefers_total_tokens(self):
        r = ab.check_run_budget("gpt",
            {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 900}, 1000)
        self.assertEqual(r["used"], 900)


class DailyBudgetTests(unittest.TestCase):

    def test_under_limit(self):
        agent = {"daily_token_budget": 10000}
        totals = {"gpt": {"total_tokens": 5000}}
        with mock.patch("agent.budgets.agent_store.get_agent", return_value=agent), \
             mock.patch("agent.budgets.agent_costs.per_agent_totals", return_value=totals):
            r = ab.check_daily_budget("gpt")
        self.assertEqual(r["action"], ab.ACTION_OK)

    def test_exceeded(self):
        agent = {"daily_token_budget": 10000}
        totals = {"gpt": {"total_tokens": 12000}}
        with mock.patch("agent.budgets.agent_store.get_agent", return_value=agent), \
             mock.patch("agent.budgets.agent_costs.per_agent_totals", return_value=totals):
            r = ab.check_daily_budget("gpt")
        self.assertEqual(r["action"], ab.ACTION_STOP)

    def test_zero_means_unlimited(self):
        agent = {"daily_token_budget": 0}
        with mock.patch("agent.budgets.agent_store.get_agent", return_value=agent):
            r = ab.check_daily_budget("gpt")
        self.assertEqual(r["action"], ab.ACTION_OK)

    def test_missing_agent_returns_ok(self):
        with mock.patch("agent.budgets.agent_store.get_agent", side_effect=Exception("not found")):
            r = ab.check_daily_budget("missing")
        self.assertEqual(r["action"], ab.ACTION_OK)


class GlobalDailyBudgetTests(unittest.TestCase):

    def test_under_limit(self):
        cfg = {"global_daily_token_budget": 50000}
        totals = {"gpt": {"total_tokens": 10000}, "opus": {"total_tokens": 5000}}
        with mock.patch("agent.budgets.dashboard_config.load", return_value=cfg), \
             mock.patch("agent.budgets.agent_costs.per_agent_totals", return_value=totals):
            r = ab.check_global_daily_budget()
        self.assertEqual(r["action"], ab.ACTION_OK)
        self.assertEqual(r["used"], 15000)

    def test_exceeded(self):
        cfg = {"global_daily_token_budget": 10000}
        totals = {"gpt": {"total_tokens": 8000}, "opus": {"total_tokens": 5000}}
        with mock.patch("agent.budgets.dashboard_config.load", return_value=cfg), \
             mock.patch("agent.budgets.agent_costs.per_agent_totals", return_value=totals):
            r = ab.check_global_daily_budget()
        self.assertEqual(r["action"], ab.ACTION_STOP)

    def test_zero_means_unlimited(self):
        cfg = {"global_daily_token_budget": 0}
        with mock.patch("agent.budgets.dashboard_config.load", return_value=cfg):
            r = ab.check_global_daily_budget()
        self.assertEqual(r["action"], ab.ACTION_OK)


class BudgetStatusTests(unittest.TestCase):

    def test_returns_worst_action(self):
        daily_warn = {"action": ab.ACTION_WARN, "used": 8000, "limit": 10000, "pct": 80, "reason": ""}
        global_stop = {"action": ab.ACTION_STOP, "used": 50000, "limit": 40000, "pct": 125, "reason": ""}
        with mock.patch("agent.budgets.check_daily_budget", return_value=daily_warn), \
             mock.patch("agent.budgets.check_global_daily_budget", return_value=global_stop):
            r = ab.get_budget_status("gpt")
        self.assertEqual(r["worst_action"], ab.ACTION_STOP)

    def test_ok_when_all_ok(self):
        ok = {"action": ab.ACTION_OK}
        with mock.patch("agent.budgets.check_daily_budget", return_value=ok), \
             mock.patch("agent.budgets.check_global_daily_budget", return_value=ok):
            r = ab.get_budget_status("gpt")
        self.assertEqual(r["worst_action"], ab.ACTION_OK)


class TodayStartTests(unittest.TestCase):

    def test_returns_midnight_utc(self):
        ts = ab._today_start()
        gm = time.gmtime(ts)
        self.assertEqual(gm.tm_hour, 0)
        self.assertEqual(gm.tm_min, 0)
        self.assertEqual(gm.tm_sec, 0)


if __name__ == "__main__":
    unittest.main()
