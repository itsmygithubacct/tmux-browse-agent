"""CLI status detectors: per-agent content parsing + ANSI dispatcher."""

import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_EXT = _REPO / "extensions" / "agent"
for _p in (_REPO, _EXT):
    _s = str(_p)
    if _s not in sys.path:
        sys.path.insert(0, _s)

from agent import cli_detect  # noqa: E402


class DispatcherTests(unittest.TestCase):

    def test_unknown_tool_returns_idle(self):
        self.assertEqual(
            cli_detect.detect_status_from_content("Generating ⠋", "unknown_tool"),
            "idle",
        )

    def test_strips_ansi_before_matching(self):
        # capture-pane -e injects ANSI codes that would otherwise split
        # signal strings. The dispatcher must strip them first.
        ansi_running = (
            "\x1b[38;2;39;62;94m⬝⬝⬝⬝\x1b[0m  "
            "\x1b[38;2;238;238;238mesc \x1b[38;2;128;128;128minterrupt\x1b[0m"
        )
        self.assertEqual(
            cli_detect.detect_status_from_content(ansi_running, "opencode"),
            "running",
            "ANSI around 'esc interrupt' must not block detection",
        )
        ansi_spinner = "\x1b[38;2;255;255;255m⠋\x1b[0m generating"
        self.assertEqual(
            cli_detect.detect_status_from_content(ansi_spinner, "opencode"),
            "running",
            "ANSI around spinner glyphs must not block detection",
        )


class ClaudeStatusTests(unittest.TestCase):
    """Claude Code uses hook-based detection; the content stub stays idle."""

    def test_stub_always_returns_idle(self):
        self.assertEqual(cli_detect.detect_claude_status("anything"), "idle")
        self.assertEqual(cli_detect.detect_claude_status("esc to interrupt"), "idle")


class OpencodeStatusTests(unittest.TestCase):

    def test_running_via_esc_interrupt(self):
        self.assertEqual(
            cli_detect.detect_opencode_status("Processing\nesc to interrupt"),
            "running",
        )
        self.assertEqual(
            cli_detect.detect_opencode_status("Working… esc interrupt"),
            "running",
        )

    def test_running_via_spinner(self):
        self.assertEqual(cli_detect.detect_opencode_status("Generating ⠋"), "running")
        self.assertEqual(cli_detect.detect_opencode_status("Loading ⠹"), "running")

    def test_waiting_via_permission_prompt(self):
        self.assertEqual(
            cli_detect.detect_opencode_status("allow this action? [y/n]"),
            "waiting",
        )
        self.assertEqual(
            cli_detect.detect_opencode_status("continue? (y/n)"),
            "waiting",
        )

    def test_waiting_via_prompt_cursor(self):
        self.assertEqual(
            cli_detect.detect_opencode_status("task complete.\n>"),
            "waiting",
        )
        self.assertEqual(
            cli_detect.detect_opencode_status("Ready\n>>"),
            "waiting",
        )

    def test_waiting_via_numbered_selection(self):
        content = "Select:\n❯ 1. Option A\n  2. Option B"
        self.assertEqual(cli_detect.detect_opencode_status(content), "waiting")

    def test_idle(self):
        self.assertEqual(cli_detect.detect_opencode_status("file saved"), "idle")
        self.assertEqual(cli_detect.detect_opencode_status("random output"), "idle")


class CodexStatusTests(unittest.TestCase):

    def test_running_via_thinking(self):
        self.assertEqual(
            cli_detect.detect_codex_status("thinking about your request"),
            "running",
        )
        self.assertEqual(cli_detect.detect_codex_status("working on task"), "running")
        self.assertEqual(
            cli_detect.detect_codex_status("processing\nesc to interrupt"),
            "running",
        )

    def test_running_via_spinner(self):
        self.assertEqual(cli_detect.detect_codex_status("generating ⠋"), "running")

    def test_waiting_via_approval(self):
        self.assertEqual(
            cli_detect.detect_codex_status("run this command? (y/n)"),
            "waiting",
        )
        self.assertEqual(
            cli_detect.detect_codex_status("approve changes?"),
            "waiting",
        )
        self.assertEqual(
            cli_detect.detect_codex_status("execute this action? [y/n]"),
            "waiting",
        )

    def test_waiting_via_prompt(self):
        self.assertEqual(cli_detect.detect_codex_status("ready\ncodex>"), "waiting")
        self.assertEqual(cli_detect.detect_codex_status("done\n>"), "waiting")

    def test_idle(self):
        self.assertEqual(cli_detect.detect_codex_status("file saved"), "idle")
        self.assertEqual(cli_detect.detect_codex_status("random output text"), "idle")


class VibeStatusTests(unittest.TestCase):
    """Vibe is unusual: Textual TUI can render text vertically (one char
    per line). Detectors must reconstruct words by joining recent lines."""

    def test_running_via_spinner(self):
        self.assertEqual(cli_detect.detect_vibe_status("processing ⠋"), "running")

    def test_running_via_activity_word(self):
        self.assertEqual(cli_detect.detect_vibe_status("Reading file"), "running")
        self.assertEqual(cli_detect.detect_vibe_status("Writing changes"), "running")

    def test_running_via_vertical_text(self):
        # Textual rendering of "Running bash"
        vertical = "⠋\nR\nu\nn\nn\ni\nn\ng\nb\na\ns\nh\n…"
        self.assertEqual(cli_detect.detect_vibe_status(vertical), "running")

    def test_running_via_ellipsis(self):
        self.assertEqual(cli_detect.detect_vibe_status("Loading..."), "running")

    def test_waiting_via_navigation_hints(self):
        self.assertEqual(
            cli_detect.detect_vibe_status("↑↓ navigate  Enter select  ESC reject"),
            "waiting",
        )

    def test_idle(self):
        self.assertEqual(cli_detect.detect_vibe_status("file saved"), "idle")


class GeminiStatusTests(unittest.TestCase):

    def test_running(self):
        self.assertEqual(
            cli_detect.detect_gemini_status("processing\nesc to interrupt"),
            "running",
        )
        self.assertEqual(cli_detect.detect_gemini_status("generating ⠋"), "running")

    def test_waiting(self):
        self.assertEqual(
            cli_detect.detect_gemini_status("run this command? (y/n)"),
            "waiting",
        )
        self.assertEqual(cli_detect.detect_gemini_status("ready\n>"), "waiting")

    def test_idle(self):
        self.assertEqual(cli_detect.detect_gemini_status("file saved"), "idle")


class CursorStatusTests(unittest.TestCase):

    def test_stub_returns_idle(self):
        # Cursor uses hook-based detection; the content stub stays idle.
        self.assertEqual(cli_detect.detect_cursor_status("anything"), "idle")
        self.assertEqual(cli_detect.detect_cursor_status("esc to interrupt"), "idle")


class CopilotStatusTests(unittest.TestCase):

    def test_running(self):
        self.assertEqual(
            cli_detect.detect_copilot_status("Thinking about your request"),
            "running",
        )
        self.assertEqual(cli_detect.detect_copilot_status("loading ⠹"), "running")

    def test_waiting(self):
        self.assertEqual(
            cli_detect.detect_copilot_status("Allow this tool to run?"),
            "waiting",
        )
        self.assertEqual(cli_detect.detect_copilot_status("done\ncopilot>"), "waiting")

    def test_idle(self):
        self.assertEqual(cli_detect.detect_copilot_status("random output"), "idle")


class PiStatusTests(unittest.TestCase):
    """Pi auto-approves everything — only running vs waiting/idle distinctions."""

    def test_running(self):
        self.assertEqual(cli_detect.detect_pi_status("generating ⠋"), "running")
        self.assertEqual(cli_detect.detect_pi_status("thinking about code"), "running")

    def test_prompt_takes_priority_over_lingering_activity(self):
        # "reading" lingers in scrollback after the agent finishes; the
        # prompt cursor near the bottom must still register as waiting.
        self.assertEqual(
            cli_detect.detect_pi_status("reading config.toml\nDone.\n>"),
            "waiting",
        )

    def test_waiting_via_pi_prompt(self):
        self.assertEqual(cli_detect.detect_pi_status("complete\npi>"), "waiting")

    def test_idle(self):
        self.assertEqual(cli_detect.detect_pi_status("file saved"), "idle")


class DroidStatusTests(unittest.TestCase):

    def test_running(self):
        self.assertEqual(
            cli_detect.detect_droid_status("thinking about your request"),
            "running",
        )
        self.assertEqual(cli_detect.detect_droid_status("executing command"), "running")

    def test_waiting(self):
        self.assertEqual(
            cli_detect.detect_droid_status("execute this action? [y/n]"),
            "waiting",
        )
        self.assertEqual(cli_detect.detect_droid_status("ready\ndroid>"), "waiting")

    def test_idle(self):
        self.assertEqual(cli_detect.detect_droid_status("random output"), "idle")


class SettlStatusTests(unittest.TestCase):

    def test_stub_returns_idle(self):
        self.assertEqual(cli_detect.detect_settl_status("anything"), "idle")


class StripAnsiTests(unittest.TestCase):

    def test_removes_csi_color(self):
        self.assertEqual(cli_detect.strip_ansi("\x1b[31mred\x1b[0m"), "red")

    def test_passes_plain_text_through(self):
        self.assertEqual(cli_detect.strip_ansi("plain"), "plain")

    def test_removes_osc(self):
        # ESC ] ... BEL  (operating system command, e.g. titles)
        self.assertEqual(
            cli_detect.strip_ansi("hello\x1b]0;title\x07world"),
            "helloworld",
        )


if __name__ == "__main__":
    unittest.main()
