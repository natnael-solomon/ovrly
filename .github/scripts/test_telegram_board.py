import unittest

from telegram_api import TelegramError
from telegram_board import diff, fetch_project, render_board, render_changes, run, snapshot
from test_telegram_api import FakeTelegram

PROJECT = {"title": "ovrly development", "url": "https://github.com/users/o/projects/3"}


def node(item_id, number, status, priority=None, area=None, labels=(), kind="Issue", title=None):
    return {
        "id": item_id,
        "content": {
            "__typename": kind, "number": number, "title": title or f"Task {number}",
            "url": f"https://github.com/o/r/issues/{number}",
            "labels": {"nodes": [{"name": name} for name in labels]},
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
            node("b", 17, "Ready"),
            node("c", 33, "In progress", labels=["blocked"], title="old <spike>"),
            node("d", 60, "Done"),
            node("e", 61, "Backlog"),
        ])
        text = render_board(PROJECT, items, now=1700000000)
        self.assertIn('<b>Board</b> · <a href="https://github.com/users/o/projects/3">ovrly development</a>', text)
        self.assertIn("<b>Ready</b>\n• <a href=\"https://github.com/o/r/issues/17\">#17</a> Task 17", text)
        self.assertIn("<b>In progress</b>\n• <a", text)
        self.assertIn("<b>Blocked</b>\n• <a href=\"https://github.com/o/r/issues/33\">#33</a> old &lt;spike&gt;", text)
        self.assertNotIn("In review", text)
        self.assertNotIn("#60", text)
        self.assertNotIn("#61", text)
        self.assertIn('<tg-time unix="1700000000" format="r">', text)
        # Blocked items appear once, not also under their status column.
        self.assertEqual(text.count("#33"), 1)

    def test_empty_board(self):
        self.assertIn("Nothing in progress.", render_board(PROJECT, {}, now=0))

    def test_changes_inline_and_expandable(self):
        before = snapshot([node("a", 1, "Ready", "Next")])
        after = snapshot([node("a", 1, "In review", "Now", "Android"), node("b", 2, None)])
        text = render_changes(diff(before, after))
        self.assertTrue(text.startswith("<b>Board</b> · 2 changes\n"))
        self.assertIn("— <code>Ready</code> → <code>In review</code> · Priority <code>Next</code> → <code>Now</code> · Area — → <code>Android</code>", text)
        self.assertIn("#2</a> Task 2 — added to —", text)
        self.assertNotIn("blockquote", text)

        many = diff({}, snapshot([node(str(i), i, "Backlog") for i in range(9)]))
        self.assertIn("<blockquote expandable>", render_changes(many))

    def test_single_change_grammar(self):
        text = render_changes(diff(snapshot([node("a", 1, "Ready")]), {}))
        self.assertIn("1 change\n", text)
        self.assertIn("— removed", text)


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

    def test_manual_dispatch_refreshes_unchanged_board(self):
        telegram, state = FakeTelegram(), {}
        nodes = [node("a", 1, "Ready")]
        run(telegram, state, PROJECT, nodes, now=0)
        telegram.calls.clear()
        self.assertEqual(run(telegram, state, PROJECT, nodes, now=1, force_refresh=True), "Board refreshed.")
        self.assertEqual(telegram.methods(), ["editMessageText"])

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

    def test_pin_failure_does_not_abort_baseline(self):
        class PinFailingTelegram(FakeTelegram):
            def call(self, method, **params):
                if method == "pinChatMessage":
                    self.calls.append((method, params))
                    raise TelegramError("pinChatMessage: Forbidden")
                return super().call(method, **params)

        telegram, state = PinFailingTelegram(), {}
        outcome = run(telegram, state, PROJECT, [node("a", 1, "Ready")], now=0)
        self.assertEqual(telegram.methods(), ["sendMessage", "pinChatMessage", "sendMessage"])
        self.assertEqual(state["message_id"], 101)
        self.assertIn("1 items", outcome)


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
