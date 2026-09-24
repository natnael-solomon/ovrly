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
        self.assertEqual(lines[2], "<blockquote>fix(capture): release pr…</blockquote>")
        self.assertEqual(lines[3], "")
        self.assertEqual(lines[4], '<b>Commit</b>  <a href="https://github.com/o/r/commit/a1b2c3d4e5f60718293a4b5c6d7e8f9012345678">a1b2c3d</a>')
        self.assertEqual(lines[5], "<b>By</b>  <i>dev</i>")
        self.assertEqual(len(lines), 6)
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
            "<blockquote>feat(android): share &lt;in…</blockquote>",
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
        self.assertTrue(text.endswith(
            "<blockquote>Notes &amp; more</blockquote>\n\n<b>Type</b>  pre-release\n<b>By</b>  <i>owner</i>"
        ))
        self.assertNotIn("When", text)
        self.assertNotIn("- item", text)


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

    def test_real_samples_are_preferred(self):
        from telegram_notify import sample_event
        real_pr = dict(pr_event("opened")["pull_request"], number=44, title="real PR")
        real = {"merged_pr": real_pr, "open_pr": real_pr, "release": {
            "tag_name": "v1.0", "name": None, "prerelease": False, "body": "Real notes",
            "html_url": "u", "author": {"login": "owner"}}}
        _, merged = sample_event("pr_merged", REPO, real)
        self.assertEqual(merged["pull_request"]["number"], 44)
        self.assertTrue(merged["pull_request"]["merged"])
        _, ready = sample_event("pr_ready", REPO, real)
        self.assertFalse(ready["pull_request"]["merged"])
        _, release = sample_event("release", REPO, real)
        self.assertEqual(release["release"]["tag_name"], "v1.0")
        _, fallback = sample_event("failure", REPO, real)
        self.assertEqual(fallback["workflow_run"]["head_branch"], "sample")

    def test_fetch_samples_selects_matching_objects(self):
        from telegram_notify import fetch_samples
        responses = {
            "pulls?state=open&per_page=5": [{"number": 9, "draft": True}, {"number": 8, "draft": False}],
            "pulls?state=closed&sort=updated&direction=desc&per_page=20": [{"number": 7, "merged_at": None}, {"number": 6, "merged_at": "t"}],
            "actions/runs?status=failure&per_page=20": {"workflow_runs": [
                {"id": 1, "event": "pull_request", "pull_requests": []},
                {"id": 2, "event": "pull_request", "pull_requests": [{"number": 8}]},
                {"id": 3, "event": "push"},
            ]},
            "releases/latest": {"tag_name": "v2"},
        }

        def fake(url, method="GET", payload=None, headers=None):
            path = url.split("/repos/o/r/", 1)[1]
            return (200, responses[path]) if path in responses else (404, {})

        real = fetch_samples("https://api.github.com/repos/o/r", "gh", fake)
        self.assertEqual(real["open_pr"]["number"], 8)
        self.assertEqual(real["merged_pr"]["number"], 6)
        self.assertEqual(real["pr_run"]["id"], 2)
        self.assertEqual(real["branch_run"]["id"], 3)
        self.assertEqual(real["release"]["tag_name"], "v2")

    def test_board_changes_sample_uses_real_items(self):
        from telegram_notify import sample_board_changes
        items = {
            f"i{n}": {"number": n, "title": f"Task {n}", "url": f"u{n}", "blocked": False,
                      "assignees": ["dev"], "status": "Ready", "priority": None, "area": None}
            for n in (4, 5, 6, 7)
        }
        text = sample_board_changes(REPO, items)
        self.assertIn("<code>Ready</code> <b>→</b> <code>In progress</code><blockquote><a href=\"u4\">#4 Task 4</a>", text)
        self.assertIn("<code>Ready</code> <b>→</b> <code>In review</code><blockquote><a href=\"u5\">#5 Task 5</a>", text)
        self.assertIn("<b>Removed</b><blockquote><a href=\"u6\">#6 Task 6</a>", text)
        self.assertNotIn("#7", text)
        self.assertEqual(items["i4"]["status"], "Ready", "must not mutate the real snapshot")
        self.assertIn("issues/1\">#1 Sample task", sample_board_changes(REPO, None))

    def test_unknown_event(self):
        self.assertIn("No handler", handle("issues", {}, FakeTelegram(), {}))


if __name__ == "__main__":
    unittest.main()
