import contextlib
import io
import json
import unittest

from telegram_api import GitHubClient
from telegram_notify import (
    OPEN_PULLS, RULE, closing_issues, handle, load_state, render_card, render_failure, render_release,
)
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
        "pull_request": open_pull(number, draft=draft, merged=merged, state=state, user=user, body=body),
    }


def open_pull(number=42, draft=False, merged=False, state="open", user="dev", body=""):
    """A pull request object as both the webhook payload and the list API return it."""
    return {
        "number": number, "title": "feat(android): share <intake>", "draft": draft,
        "merged": merged, "state": state, "body": body,
        "html_url": f"https://github.com/o/r/pull/{number}",
        "user": {"login": user},
        "base": {"ref": "main", "repo": {"html_url": REPO["html_url"]}},
    }


class FakeGitHub(GitHubClient):
    """Serves the open pull request list from memory; `pages` maps a URL to (status, body, headers)."""

    def __init__(self, pulls, link=None):
        super().__init__("token", "o/r")
        self.pulls = pulls
        self.link = link or {}
        self.urls = []

    def pages(self, url, headers):
        self.urls.append(url)
        if url in self.link:
            body, next_url = self.link[url]
            return 200, body, {"Link": f'<{next_url}>; rel="next"'} if next_url else {}
        return 200, self.pulls, {}


class FakeStore:
    def __init__(self, value, exists=True):
        self.value = value
        self.exists = exists

    def load(self):
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


class RenderingTest(unittest.TestCase):
    def test_failure_on_pull_request(self):
        text = render_failure(run_event()["workflow_run"], REPO["html_url"])
        lines = text.split("\n")
        self.assertEqual(lines[0], '<b><a href="https://github.com/o/r/actions/runs/9">Android checks failed on PR #42</a></b>')
        self.assertEqual(lines[1], RULE)
        self.assertEqual(lines[2], "<blockquote>fix(capture): relea…</blockquote>")
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
        self.assertIn("<b>Status</b>  draft", render_card(pr_event("opened", draft=True)["pull_request"]))
        self.assertIn("<b>Status</b>  ready for review", render_card(pr_event("opened")["pull_request"]))
        merged = pr_event("closed", merged=True, state="closed", body="Closes #17, fixes #19")
        text = render_card(merged["pull_request"])
        self.assertEqual(text.split("\n")[:4], [
            '<b><a href="https://github.com/o/r/pull/42">PR #42</a></b>',
            RULE,
            "<blockquote>feat(android): shar…</blockquote>",
            "",
        ])
        self.assertIn("<b>Status</b>  merged into <code>main</code>, closes <a", text)
        self.assertIn('<a href="https://github.com/o/r/issues/17">#17</a>', text)
        self.assertIn("issues/19", text)
        self.assertTrue(text.endswith("<b>By</b>  <i>dev</i>"))
        self.assertNotIn(">link<", text)
        self.assertIn("closed without merge", render_card(pr_event("closed", state="closed")["pull_request"]))

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
        self.assertIn("<b>Status</b>  ready for review", telegram.calls[-1][1]["text"])

        handle("workflow_run", run_event(), telegram, state)
        handle("pull_request", pr_event("closed", merged=True, state="closed"), telegram, state)
        self.assertEqual(telegram.methods()[-2:], ["editMessageText", "deleteMessage"])
        self.assertIn("merged into", telegram.calls[-2][1]["text"])
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
        handle("pull_request", pr_event("synchronize", user="dependabot[bot]"), telegram, state)
        handle("pull_request", pr_event("reopened", user="dependabot[bot]"), telegram, state)
        self.assertEqual(telegram.calls, [])
        self.assertEqual(state, {})


class DroppedWebhookTest(unittest.TestCase):
    def test_synchronize_without_announcement_posts_once(self):
        telegram, state = FakeTelegram(), {}
        outcome = handle("pull_request", pr_event("synchronize"), telegram, state)
        self.assertIn("Announced PR #42", outcome)
        self.assertEqual(state["pr_cards"], {"42": 101})
        self.assertIn("<b>Status</b>  ready for review", telegram.sent()[0])
        self.assertTrue(telegram.calls[0][1]["disable_notification"])

        outcome = handle("pull_request", pr_event("synchronize"), telegram, state)
        self.assertIn("already announced", outcome)
        self.assertEqual(telegram.methods(), ["sendMessage"])
        self.assertEqual(state["pr_cards"], {"42": 101})

    def test_synchronize_after_opened_posts_nothing(self):
        telegram, state = FakeTelegram(), {}
        handle("pull_request", pr_event("opened"), telegram, state)
        handle("pull_request", pr_event("synchronize"), telegram, state)
        self.assertEqual(telegram.methods(), ["sendMessage"])
        self.assertEqual(state["pr_cards"], {"42": 101})

    def test_reopened_behaves_like_synchronize(self):
        telegram, state = FakeTelegram(), {}
        handle("pull_request", pr_event("reopened"), telegram, state)
        handle("pull_request", pr_event("reopened"), telegram, state)
        self.assertEqual(telegram.methods(), ["sendMessage"])
        self.assertEqual(state["pr_cards"], {"42": 101})

    def test_reopened_after_close_gets_a_fresh_card(self):
        telegram, state = FakeTelegram(), {}
        handle("pull_request", pr_event("opened"), telegram, state)
        handle("pull_request", pr_event("closed", state="closed"), telegram, state)
        self.assertEqual(state["pr_cards"], {})
        handle("pull_request", pr_event("reopened"), telegram, state)
        self.assertEqual(telegram.methods(), ["sendMessage", "editMessageText", "sendMessage"])
        self.assertEqual(state["pr_cards"], {"42": 102})

    def test_draft_synchronize_announces_draft_then_ready_edits_once(self):
        telegram, state = FakeTelegram(), {}
        handle("pull_request", pr_event("synchronize", draft=True), telegram, state)
        self.assertIn("<b>Status</b>  draft", telegram.sent()[0])
        handle("pull_request", pr_event("ready_for_review"), telegram, state)
        handle("pull_request", pr_event("synchronize"), telegram, state)
        self.assertEqual(telegram.methods(), ["sendMessage", "editMessageText"])
        self.assertIn("<b>Status</b>  ready for review", telegram.calls[1][1]["text"])
        self.assertEqual(state["pr_cards"], {"42": 101})

    def test_existing_card_is_not_touched_on_synchronize(self):
        telegram, state = FakeTelegram(), {"pr_cards": {"42": 5}, "pr_failures": {"42": 9}}
        handle("pull_request", pr_event("synchronize"), telegram, state)
        self.assertEqual(telegram.calls, [])
        self.assertEqual(state, {"pr_cards": {"42": 5}, "pr_failures": {"42": 9}})


class CatchUpTest(unittest.TestCase):
    def test_announces_only_unannounced_open_pull_requests(self):
        github = FakeGitHub([
            open_pull(43),
            open_pull(41),
            open_pull(42, draft=True),
            open_pull(44, user="dependabot[bot]"),
        ])
        telegram, state = FakeTelegram(), {"pr_cards": {"41": 7}}
        outcome = handle("schedule", {"schedule": "7 * * * *"}, telegram, state, github)
        self.assertEqual(outcome, "Announced #42, #43 missed by webhooks.")
        self.assertEqual(github.urls, ["https://api.github.com/repos/o/r/pulls?state=open&per_page=100"])
        self.assertEqual(telegram.methods(), ["sendMessage", "sendMessage"])
        self.assertTrue(all(p["disable_notification"] for _, p in telegram.calls))
        self.assertIn(">PR #42</a>", telegram.sent()[0])
        self.assertIn("<b>Status</b>  draft", telegram.sent()[0])
        self.assertIn(">PR #43</a>", telegram.sent()[1])
        self.assertIn("<b>Status</b>  ready for review", telegram.sent()[1])
        self.assertEqual(state["pr_cards"], {"41": 7, "42": 101, "43": 102})

    def test_second_pass_and_later_webhooks_do_not_duplicate(self):
        github = FakeGitHub([open_pull(42)])
        telegram, state = FakeTelegram(), {}
        handle("workflow_dispatch", {"inputs": {}}, telegram, state, github)
        self.assertEqual(telegram.methods(), ["sendMessage"])

        outcome = handle("schedule", {}, telegram, state, github)
        self.assertEqual(outcome, "Every open pull request is announced.")
        handle("pull_request", pr_event("opened"), telegram, state)
        handle("pull_request", pr_event("synchronize"), telegram, state)
        handle("pull_request", pr_event("ready_for_review"), telegram, state)
        self.assertEqual(telegram.methods(), ["sendMessage", "editMessageText", "editMessageText"])
        self.assertEqual(state["pr_cards"], {"42": 101})

    def test_nothing_open_leaves_state_untouched(self):
        telegram, state = FakeTelegram(), {}
        handle("schedule", {}, telegram, state, FakeGitHub([]))
        self.assertEqual(telegram.calls, [])
        self.assertEqual(state, {})

    def test_follows_pagination(self):
        first = f"https://api.github.com/repos/o/r/{OPEN_PULLS}&per_page=100"
        second = f"{first}&page=2"
        github = FakeGitHub([], link={first: ([open_pull(1)], second), second: ([open_pull(2)], None)})
        telegram, state = FakeTelegram(), {}
        handle("schedule", {}, telegram, state, github)
        self.assertEqual(github.urls, [first, second])
        self.assertEqual(sorted(state["pr_cards"]), ["1", "2"])


class StateTest(unittest.TestCase):
    def fail_closed(self, store, event_name, event):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as raised:
            load_state(store, event_name, event)
        self.assertEqual(raised.exception.code, 1)
        return output.getvalue()

    def test_malformed_json_fails_every_event(self):
        store = FakeStore(json.JSONDecodeError("Expecting value", "{", 1))
        for event_name, event in (("pull_request", pr_event("opened")), ("schedule", {}), ("release", {})):
            with self.subTest(event=event_name):
                self.assertIn("::error::TELEGRAM_NOTIFY_STATE is malformed", self.fail_closed(store, event_name, event))

    def test_unexpected_shape_fails_closed(self):
        for value in ([], {"pr_cards": [42]}, {"pr_cards": {"42": "101"}}, {"pr_failures": {42: 1}}):
            with self.subTest(value=value):
                self.fail_closed(FakeStore(value), "pull_request", pr_event("synchronize"))

    def test_missing_state_fails_closed_only_where_dedupe_depends_on_it(self):
        missing = FakeStore({}, exists=False)
        for event_name, event in (
            ("pull_request", pr_event("synchronize")),
            ("pull_request", pr_event("reopened")),
            ("schedule", {}),
            ("workflow_dispatch", {}),
        ):
            with self.subTest(event=event_name, action=event.get("action")):
                self.assertIn("is missing", self.fail_closed(missing, event_name, event))
        for event_name, event in (
            ("pull_request", pr_event("opened")),
            ("pull_request", pr_event("closed", state="closed")),
            ("workflow_run", run_event()),
            ("release", {}),
        ):
            with self.subTest(event=event_name, action=event.get("action")):
                self.assertEqual(load_state(missing, event_name, event), {})

    def test_valid_state_is_returned(self):
        state = {"pr_cards": {"42": 5}, "pr_failures": {}}
        self.assertIs(load_state(FakeStore(state), "schedule", {}), state)
        self.assertEqual(load_state(FakeStore({}), "pull_request", pr_event("synchronize")), {})


class OtherEventsTest(unittest.TestCase):
    def test_release_is_loud(self):
        telegram = FakeTelegram()
        event = {"repository": REPO, "release": {
            "tag_name": "v1", "name": "v1", "prerelease": False, "body": "",
            "html_url": "u", "author": {"login": "owner"}}}
        handle("release", event, telegram, {})
        self.assertFalse(telegram.calls[0][1]["disable_notification"])

    def test_unknown_event(self):
        self.assertIn("No handler", handle("issues", {}, FakeTelegram(), {}))


if __name__ == "__main__":
    unittest.main()
