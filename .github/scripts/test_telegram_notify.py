import unittest

from telegram_notify import RULE, closing_issues, handle, render_card, render_failure, render_release
from test_telegram_api import FakeTelegram

REPO = {"html_url": "https://github.com/o/r", "name": "r"}


def run_event(conclusion="failure", event="pull_request", numbers=(42,), actor="dev", branch="feat/x"):
    return {
        "repository": REPO,
        "workflow_run": {
            "id": 9, "name": "Android checks", "conclusion": conclusion, "event": event,
            "head_branch": branch, "head_sha": "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678",
            "html_url": "https://github.com/o/r/actions/runs/9",
            "head_commit": {"message": "fix(capture): release projection\n\nDetails <here>"},
            "actor": {"login": actor},
            "pull_requests": [{"number": n} for n in numbers],
        },
    }


def pr_event(action, number=42, draft=False, merged=False, state="open", user="dev", body=""):
    return {
        "action": action,
        "repository": REPO,
        "pull_request": {
            "number": number, "title": "feat(android): share <intake>", "draft": draft,
            "merged": merged, "state": state, "body": body,
            "html_url": f"https://github.com/o/r/pull/{number}",
            "user": {"login": user},
            "base": {"ref": "main", "repo": {"html_url": REPO["html_url"]}},
        },
    }


class RenderingTest(unittest.TestCase):
    def test_failure_on_pull_request(self):
        text = render_failure(run_event()["workflow_run"], REPO["html_url"])
        lines = text.split("\n")
        self.assertEqual(lines[0], '<b><a href="https://github.com/o/r/actions/runs/9">Android checks failed on PR#42</a></b>')
        self.assertEqual(lines[1], RULE)
        self.assertEqual(lines[2], "<blockquote>fix(capture): release projection</blockquote>")
        self.assertEqual(lines[3], "")
        self.assertEqual(lines[4], '<b>Commit</b>  <a href="https://github.com/o/r/commit/a1b2c3d4e5f60718293a4b5c6d7e8f9012345678">a1b2c3d</a>')
        self.assertEqual(lines[5], "<b>By</b>  <i>dev</i>")
        self.assertTrue(lines[6].startswith("<b>When</b>  <tg-time"))
        self.assertEqual(len(lines), 7)
        self.assertNotIn("Details", text)

    def test_failure_on_branch_and_timeout(self):
        run = run_event(conclusion="timed_out", event="push", numbers=(), branch="main")["workflow_run"]
        text = render_failure(run, REPO["html_url"])
        self.assertIn(">Android checks timed out on main</a></b>", text)

    def test_card_states(self):
        self.assertIn("<b>Status</b>  Draft", render_card(pr_event("opened", draft=True)["pull_request"]))
        self.assertIn("<b>Status</b>  Ready for review", render_card(pr_event("opened")["pull_request"]))
        merged = pr_event("closed", merged=True, state="closed", body="Closes #17, fixes #19")
        text = render_card(merged["pull_request"])
        self.assertEqual(text.split("\n")[:4], [
            '<b><a href="https://github.com/o/r/pull/42">PR#42</a></b>',
            RULE,
            "<blockquote>feat(android): share &lt;intake&gt;</blockquote>",
            "",
        ])
        self.assertIn("<b>Status</b>  Merged into <code>main</code>, closes <a", text)
        self.assertIn('<a href="https://github.com/o/r/issues/17">#17</a>', text)
        self.assertIn("issues/19", text)
        self.assertTrue(text.endswith("<b>By</b>  <i>dev</i>"))
        self.assertNotIn(">link<", text)
        self.assertIn("Closed without merge", render_card(pr_event("closed", state="closed")["pull_request"]))

    def test_closing_keywords(self):
        self.assertEqual(closing_issues("Closes #1\nresolved #2 fix #1 Fixed: #3"), ["1", "2", "3"])
        self.assertEqual(closing_issues(None), [])

    def test_release_with_notes(self):
        release = {
            "tag_name": "v0.3.0", "name": None, "prerelease": True, "body": "Notes & more\n- item",
            "html_url": "https://github.com/o/r/releases/tag/v0.3.0", "author": {"login": "owner"},
        }
        text = render_release(release, "ovrly")
        self.assertTrue(text.startswith(
            f'<b><a href="https://github.com/o/r/releases/tag/v0.3.0">ovrly v0.3.0</a></b>\n{RULE}\n'
            "<blockquote expandable>Notes &amp; more\n- item</blockquote>\n\n<b>Type</b>  Pre-release\n<b>By</b>  <i>owner</i>\n<b>When</b>  <tg-time"
        ))


class WorkflowRunTest(unittest.TestCase):
    def test_pr_failure_replaces_previous_and_success_clears(self):
        telegram, state = FakeTelegram(), {}
        handle("workflow_run", run_event(), telegram, state)
        self.assertEqual(state["pr_failures"], {"42": 101})
        self.assertFalse(telegram.calls[0][1]["disable_notification"])

        handle("workflow_run", run_event(), telegram, state)
        self.assertEqual(telegram.methods(), ["sendMessage", "deleteMessage", "sendMessage"])
        self.assertEqual(state["pr_failures"], {"42": 102})

        handle("workflow_run", run_event(conclusion="success"), telegram, state)
        self.assertEqual(telegram.methods()[-1], "deleteMessage")
        self.assertEqual(state["pr_failures"], {})

    def test_main_failures_are_permanent(self):
        telegram, state = FakeTelegram(), {}
        handle("workflow_run", run_event(event="push", numbers=(), branch="main"), telegram, state)
        handle("workflow_run", run_event(conclusion="success", event="push", numbers=(), branch="main"), telegram, state)
        self.assertEqual(telegram.methods(), ["sendMessage"])
        self.assertEqual(state, {})

    def test_cancelled_and_bots_are_ignored(self):
        telegram, state = FakeTelegram(), {}
        handle("workflow_run", run_event(conclusion="cancelled"), telegram, state)
        handle("workflow_run", run_event(actor="dependabot[bot]"), telegram, state)
        self.assertEqual(telegram.calls, [])


class PullRequestTest(unittest.TestCase):
    def test_card_lifecycle(self):
        telegram, state = FakeTelegram(), {}
        handle("pull_request", pr_event("opened", draft=True), telegram, state)
        self.assertEqual(state["pr_cards"], {"42": 101})

        handle("pull_request", pr_event("ready_for_review"), telegram, state)
        self.assertEqual(telegram.methods(), ["sendMessage", "editMessageText"])
        self.assertIn("<b>Status</b>  Ready for review", telegram.calls[-1][1]["text"])

        handle("workflow_run", run_event(), telegram, state)
        handle("pull_request", pr_event("closed", merged=True, state="closed"), telegram, state)
        self.assertEqual(telegram.methods()[-2:], ["editMessageText", "deleteMessage"])
        self.assertIn("Merged into", telegram.calls[-2][1]["text"])
        self.assertEqual(state, {"pr_cards": {}, "pr_failures": {}})

    def test_missing_card_is_recreated(self):
        telegram, state = FakeTelegram(missing={5}), {"pr_cards": {"42": 5}}
        handle("pull_request", pr_event("ready_for_review"), telegram, state)
        self.assertEqual(telegram.methods(), ["editMessageText", "sendMessage"])
        self.assertEqual(state["pr_cards"]["42"], 101)

    def test_close_without_card_only_cleans_failures(self):
        telegram, state = FakeTelegram(), {"pr_failures": {"42": 9}}
        handle("pull_request", pr_event("closed", state="closed"), telegram, state)
        self.assertEqual(telegram.methods(), ["deleteMessage"])

    def test_dependabot_is_skipped(self):
        telegram, state = FakeTelegram(), {}
        handle("pull_request", pr_event("opened", user="dependabot[bot]"), telegram, state)
        self.assertEqual(telegram.calls, [])


class OtherEventsTest(unittest.TestCase):
    def test_release_is_loud(self):
        telegram = FakeTelegram()
        event = {"repository": REPO, "release": {
            "tag_name": "v1", "name": "v1", "prerelease": False, "body": "",
            "html_url": "u", "author": {"login": "owner"}}}
        handle("release", event, telegram, {})
        self.assertFalse(telegram.calls[0][1]["disable_notification"])

    def test_dispatch_default_and_custom_message(self):
        telegram = FakeTelegram()
        handle("workflow_dispatch", {"inputs": {}}, telegram, {})
        handle("workflow_dispatch", {"inputs": {"message": "hi <team>"}}, telegram, {})
        self.assertIn("Telegram notifications test", telegram.sent()[0])
        self.assertIn("hi &lt;team&gt;", telegram.sent()[1])

    def test_dispatch_samples_leave_state_untouched(self):
        from telegram_notify import SAMPLES
        for sample in SAMPLES:
            with self.subTest(sample=sample):
                telegram, state = FakeTelegram(), {"pr_cards": {"1": 1}}
                outcome = handle("workflow_dispatch", {"repository": REPO, "inputs": {"sample": sample}}, telegram, state)
                self.assertIn(f"Previewed {sample}", outcome)
                self.assertGreaterEqual(len(telegram.sent()), 1)
                self.assertEqual(state, {"pr_cards": {"1": 1}})

    def test_unknown_event(self):
        self.assertIn("No handler", handle("issues", {}, FakeTelegram(), {}))


if __name__ == "__main__":
    unittest.main()
