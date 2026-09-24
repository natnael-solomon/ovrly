import unittest

from telegram_board import RULE, diff, fetch_project, render_board, render_changes, run, snapshot
from test_telegram_api import FakeTelegram

PROJECT = {"title": "ovrly development", "url": "https://github.com/users/o/projects/3"}


def node(item_id, number, status, priority=None, area=None, labels=(), kind="Issue", title=None, assignees=("dev",)):
    return {
        "id": item_id,
        "content": {
            "__typename": kind, "number": number, "title": title or f"Task {number}",
            "url": f"https://github.com/o/r/issues/{number}",
            "labels": {"nodes": [{"name": name} for name in labels]},
            "assignees": {"nodes": [{"login": login} for login in assignees]},
        },
        "status": {"name": status} if status else None,
        "priority": {"name": priority} if priority else None,
        "area": {"name": area} if area else None,
    }


class SnapshotTest(unittest.TestCase):
    def test_skips_drafts_and_redacted_items(self):
        nodes = [
            node("a", 1, "Ready"),
            {"id": "b", "content": {"__typename": "DraftIssue", "title": "idea"}},
            {"id": "c", "content": None},
        ]
        self.assertEqual(list(snapshot(nodes)), ["a"])

    def test_tracks_fields_and_blocked_label(self):
        item = snapshot([node("a", 1, "Ready", "Now", "Android", labels=["bug", "blocked"])])["a"]
        self.assertEqual(item["status"], "Ready")
        self.assertEqual(item["priority"], "Now")
        self.assertEqual(item["area"], "Android")
        self.assertTrue(item["blocked"])
        self.assertEqual(item["assignees"], ["dev"])
        self.assertEqual(snapshot([node("b", 2, "Ready", assignees=())])["b"]["assignees"], [])


class DiffTest(unittest.TestCase):
    def test_added_changed_removed_in_number_order(self):
        before = snapshot([node("a", 5, "Ready", "Next"), node("b", 2, "Backlog")])
        after = snapshot([node("a", 5, "In progress", "Now"), node("c", 9, "Backlog")])
        changes = diff(before, after)
        self.assertEqual([(c[0]["number"], c[1]) for c in changes], [(5, "changed"), (9, "added"), (2, "removed")])
        self.assertEqual(changes[0][2], [("status", "Ready", "In progress"), ("priority", "Next", "Now")])

    def test_title_only_change_is_not_a_delta(self):
        before = snapshot([node("a", 1, "Ready", title="old")])
        after = snapshot([node("a", 1, "Ready", title="new")])
        self.assertEqual(diff(before, after), [])
        self.assertNotEqual(before, after)


class RenderTest(unittest.TestCase):
    def test_board_sections_and_blocked(self):
        items = snapshot([
            node("a", 42, "In progress", title="share intake"),
            node("b", 17, "Ready", assignees=()),
            node("c", 33, "In progress", labels=["blocked"], title="old <spike>"),
            node("d", 60, "Done"),
            node("e", 61, "Backlog"),
        ])
        text = render_board(PROJECT, items, now=1700000000)
        lines = text.split("\n")
        self.assertEqual(lines[0], '<b><a href="https://github.com/users/o/projects/3">Board</a></b>')
        self.assertEqual(lines[1], RULE)
        self.assertEqual(lines[2], "<b>Ready</b>")
        self.assertEqual(lines[3], '<blockquote><a href="https://github.com/o/r/issues/17">#17 Task 17</a> · unassigned</blockquote>')
        self.assertEqual(lines[4], "<b>In progress</b>")
        self.assertEqual(lines[5], '<blockquote><a href="https://github.com/o/r/issues/42">#42 share intake</a> · dev</blockquote>')
        self.assertEqual(lines[6], "<b>Blocked</b>")
        self.assertEqual(lines[7], '<blockquote><a href="https://github.com/o/r/issues/33">#33 old &lt;spike&gt;</a> · dev</blockquote>')
        self.assertEqual(lines[8], "")
        self.assertTrue(lines[9].startswith('<tg-time unix="1700000000" format="r">'))
        self.assertEqual(len(lines), 10)
        self.assertNotIn("In review", text)
        self.assertNotIn("#60", text)
        self.assertNotIn("#61", text)
        self.assertNotIn("<s>", text)
        # Blocked items appear only under Blocked, not also under their status column.
        self.assertEqual(text.count("#33"), 1)

    def test_empty_board(self):
        text = render_board(PROJECT, {}, now=0)
        self.assertIn("Nothing in progress.", text)
        self.assertNotIn("blockquote", text)

    def test_changes_group_by_transition(self):
        before = snapshot([node("a", 1, "Ready", "Next"), node("c", 3, "Ready"), node("d", 4, "Backlog")])
        after = snapshot([
            node("a", 1, "In review", "Now", "Android"),
            node("c", 3, "In review"),
            node("b", 2, None, assignees=()),
        ])
        text = render_changes(diff(before, after))
        lines = text.split("\n")
        self.assertEqual(lines[:2], ["<b>Board</b> · 4 changes", RULE])
        self.assertEqual(lines[2], '<code>Ready</code> <b>→</b> <code>In review</code><blockquote><a href="https://github.com/o/r/issues/1">#1 Task 1</a> · dev')
        self.assertEqual(lines[3], '<a href="https://github.com/o/r/issues/3">#3 Task 3</a> · dev</blockquote>')
        self.assertEqual(lines[4], '<b>Priority</b> <code>Next</code> <b>→</b> <code>Now</code><blockquote><a href="https://github.com/o/r/issues/1">#1 Task 1</a> · dev</blockquote>')
        self.assertEqual(lines[5], '<b>Area</b> — <b>→</b> <code>Android</code><blockquote><a href="https://github.com/o/r/issues/1">#1 Task 1</a> · dev</blockquote>')
        self.assertEqual(lines[6], '<b>Added</b> —<blockquote><a href="https://github.com/o/r/issues/2">#2 Task 2</a> · unassigned</blockquote>')
        self.assertEqual(lines[7], '<b>Removed</b><blockquote><a href="https://github.com/o/r/issues/4">#4 Task 4</a></blockquote>')
        self.assertEqual(len(lines), 8)
        self.assertNotIn("<s>", text)

    def test_single_change_grammar(self):
        text = render_changes(diff(snapshot([node("a", 1, "Ready")]), {}))
        self.assertIn("· 1 change\n", text)
        self.assertIn("\n<b>Removed</b><blockquote><a", text)


class RunTest(unittest.TestCase):
    def test_baseline_creates_and_pins_board(self):
        telegram, state = FakeTelegram(), {}
        outcome = run(telegram, state, PROJECT, [node("a", 1, "Ready")], now=0)
        self.assertEqual(telegram.methods(), ["sendMessage", "pinChatMessage", "sendMessage"])
        self.assertTrue(all(p["disable_notification"] for m, p in telegram.calls if m != "pinChatMessage"))
        self.assertEqual(state["message_id"], 101)
        self.assertIn("Board tracking started", telegram.sent()[1])
        self.assertIn("1 items", outcome)

    def test_no_change_is_silent(self):
        telegram, state = FakeTelegram(), {}
        nodes = [node("a", 1, "Ready")]
        run(telegram, state, PROJECT, nodes, now=0)
        telegram.calls.clear()
        self.assertEqual(run(telegram, state, PROJECT, nodes, now=1), "No board changes.")
        self.assertEqual(telegram.calls, [])

    def test_change_posts_delta_and_edits_board(self):
        telegram, state = FakeTelegram(), {}
        run(telegram, state, PROJECT, [node("a", 1, "Ready")], now=0)
        telegram.calls.clear()
        run(telegram, state, PROJECT, [node("a", 1, "In progress")], now=1)
        self.assertEqual(telegram.methods(), ["sendMessage", "editMessageText"])
        self.assertEqual(telegram.calls[1][1]["message_id"], 101)
        self.assertEqual(state["items"]["a"]["status"], "In progress")

    def test_deleted_pinned_message_is_recreated(self):
        telegram, state = FakeTelegram(missing={101}), {}
        run(telegram, state, PROJECT, [node("a", 1, "Ready")], now=0)
        telegram.calls.clear()
        run(telegram, state, PROJECT, [node("a", 1, "Ready", labels=["blocked"])], now=1)
        self.assertEqual(telegram.methods(), ["editMessageText", "sendMessage", "pinChatMessage"])
        self.assertEqual(state["message_id"], 103)

    def test_pin_failure_is_tolerated(self):
        from telegram_api import TelegramError

        class NoPin(FakeTelegram):
            def call(self, method, **params):
                if method == "pinChatMessage":
                    raise TelegramError("pinChatMessage: not enough rights")
                return super().call(method, **params)

        telegram, state = NoPin(), {}
        outcome = run(telegram, state, PROJECT, [node("a", 1, "Ready")], now=0)
        self.assertEqual(state["message_id"], 101)
        self.assertIn("Baseline", outcome)


class FetchTest(unittest.TestCase):
    def test_paginates_and_reports_errors(self):
        pages = [
            (200, {"data": {"user": {"projectV2": {"title": "P", "url": "u", "items": {
                "pageInfo": {"hasNextPage": True, "endCursor": "c1"}, "nodes": [node("a", 1, "Ready")]}}}}}),
            (200, {"data": {"user": {"projectV2": {"title": "P", "url": "u", "items": {
                "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": [node("b", 2, "Ready")]}}}}}),
        ]
        cursors = []

        def fake(url, method, payload, headers):
            cursors.append(payload["variables"]["after"])
            return pages.pop(0)

        project, nodes = fetch_project("pat", "o", 3, fake)
        self.assertEqual(cursors, [None, "c1"])
        self.assertEqual([n["id"] for n in nodes], ["a", "b"])
        self.assertEqual(project, {"title": "P", "url": "u"})

        with self.assertRaises(RuntimeError):
            fetch_project("pat", "o", 3, lambda *a: (200, {"errors": [{"message": "denied"}]}))
        with self.assertRaises(RuntimeError):
            fetch_project("pat", "o", 3, lambda *a: (200, {"data": {"user": {"projectV2": None}}}))


if __name__ == "__main__":
    unittest.main()
