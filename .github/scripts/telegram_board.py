"""Mirror the GitHub Project board into a pinned Telegram message and post changes."""

import os
import time

from telegram_api import (
    TelegramClient, TelegramError, VariableState, escape, http_json, require_env, truncate,
)

STATE_VARIABLE = "TELEGRAM_BOARD_STATE"
GRAPHQL = "https://api.github.com/graphql"
FIELDS = ("status", "priority", "area")
FIELD_LABELS = {"status": "Status", "priority": "Priority", "area": "Area"}
DEFAULT_COLUMNS = ("Ready", "In progress", "In review")
BLOCKED_LABEL = "blocked"
TITLE_LIMIT = 48
INLINE_LIMIT = 8

QUERY = """
query($owner: String!, $number: Int!, $after: String) {
  user(login: $owner) {
    projectV2(number: $number) {
      title
      url
      items(first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          content {
            __typename
            ... on Issue { number title url labels(first: 30) { nodes { name } } assignees(first: 10) { nodes { login } } }
            ... on PullRequest { number title url labels(first: 30) { nodes { name } } assignees(first: 10) { nodes { login } } }
          }
          status: fieldValueByName(name: "Status") {
            ... on ProjectV2ItemFieldSingleSelectValue { name }
          }
          priority: fieldValueByName(name: "Priority") {
            ... on ProjectV2ItemFieldSingleSelectValue { name }
          }
          area: fieldValueByName(name: "Area") {
            ... on ProjectV2ItemFieldSingleSelectValue { name }
          }
        }
      }
    }
  }
}
"""


def fetch_project(token, owner, number, transport=http_json):
    headers = {"Authorization": f"Bearer {token}"}
    project, nodes, after = None, [], None
    while True:
        status, response = transport(
            GRAPHQL, "POST",
            {"query": QUERY, "variables": {"owner": owner, "number": number, "after": after}},
            headers,
        )
        if status != 200 or response.get("errors"):
            detail = response.get("errors") or response.get("message") or f"HTTP {status}"
            raise RuntimeError(f"Project query failed: {detail}")
        project = ((response.get("data") or {}).get("user") or {}).get("projectV2")
        if project is None:
            raise RuntimeError(f"Project {number} owned by {owner} is not accessible")
        page = project["items"]
        nodes.extend(page["nodes"])
        if not page["pageInfo"]["hasNextPage"]:
            break
        after = page["pageInfo"]["endCursor"]
    return {"title": project["title"], "url": project["url"]}, nodes


def snapshot(nodes):
    """Reduce project items to the tracked fields, skipping drafts and redacted items."""
    items = {}
    for node in nodes:
        content = node.get("content") or {}
        if content.get("__typename") not in ("Issue", "PullRequest"):
            continue
        labels = [label["name"] for label in (content.get("labels") or {}).get("nodes") or []]
        assignees = [a["login"] for a in (content.get("assignees") or {}).get("nodes") or []]
        item = {
            "number": content["number"],
            "title": content["title"],
            "url": content["url"],
            "blocked": BLOCKED_LABEL in labels,
            "assignees": sorted(assignees),
        }
        for field in FIELDS:
            item[field] = (node.get(field) or {}).get("name")
        items[node["id"]] = item
    return items


def diff(before, after):
    """Return per-item changes as (item, kind, field_changes) in a stable order."""
    changes = []
    for item_id, item in sorted(after.items(), key=lambda pair: pair[1]["number"]):
        previous = before.get(item_id)
        if previous is None:
            changes.append((item, "added", []))
            continue
        fields = [
            (field, previous.get(field), item.get(field))
            for field in FIELDS
            if previous.get(field) != item.get(field)
        ]
        if fields:
            changes.append((item, "changed", fields))
    for item_id, item in sorted(before.items(), key=lambda pair: pair[1]["number"]):
        if item_id not in after:
            changes.append((item, "removed", []))
    return changes


# --- Rendering -----------------------------------------------------------------

RULE = "╌" * 12


def number_link(item):
    """Linked issue number; unassigned items are struck through in the compact view."""
    text = f'<a href="{escape(item["url"])}">#{item["number"]}</a>'
    return text if item["assignees"] else f"<s>{text}</s>"


def item_link(item):
    title = escape(truncate(item["title"], TITLE_LIMIT))
    return f'<a href="{escape(item["url"])}">#{item["number"]}</a> {title}'


def value(name):
    return f"<code>{escape(name)}</code>" if name else "—"


def render_board(project, items, now, columns=DEFAULT_COLUMNS):
    ordered = sorted(items.values(), key=lambda item: item["number"])
    sections = [
        (column, [i for i in ordered if i["status"] == column and not i["blocked"]])
        for column in columns
    ]
    sections.append(("Blocked", [i for i in ordered if i["blocked"]]))
    populated = [(name, rows) for name, rows in sections if rows]

    lines = [f'<b><a href="{escape(project["url"])}">Board</a></b>', RULE]
    if not populated:
        lines.append("Nothing in progress.")
    for name, rows in populated:
        lines.append(f"<b>{escape(name)}</b>  " + "  ".join(number_link(i) for i in rows))
    lines.append(details_block([item for _, rows in populated for item in rows]))
    lines.append(f'<tg-time unix="{int(now)}" format="r">just now</tg-time>')
    return "\n".join(line for line in lines if line)


def details_block(items):
    rows = []
    for item in items:
        who = ", ".join(escape(a) for a in item["assignees"]) or "unassigned"
        rows.append(f"{item_link(item)} · <i>{who}</i>")
    return f"<blockquote expandable>{chr(10).join(rows)}</blockquote>" if rows else ""


def plain_link(item):
    return f'<a href="{escape(item["url"])}">#{item["number"]}</a>'


def render_changes(changes):
    """Group changes by transition; each group lists its items as quoted title/assignee rows."""
    groups = {}
    for item, kind, fields in changes:
        if kind == "added":
            keys = [f"<b>Added</b> {value(item['status'])}"]
        elif kind == "removed":
            keys = ["<b>Removed</b>"]
        else:
            keys = []
            for field, old, new in fields:
                prefix = "" if field == "status" else f"<b>{FIELD_LABELS[field]}</b> "
                keys.append(f"{prefix}{value(old)} <b>→</b> {value(new)}")
        for key in keys:
            groups.setdefault(key, []).append(item)

    count = len(changes)
    lines = [f"<b>Board</b> · {count} change{'' if count == 1 else 's'}", RULE]
    for index, (key, items) in enumerate(groups.items()):
        if index:
            lines.append("")
        lines.append(f"{key}  " + "  ".join(plain_link(i) for i in items))
        for item in items:
            title = escape(truncate(item["title"], TITLE_LIMIT))
            if key == "<b>Removed</b>":
                lines.append(f"<blockquote>{title}</blockquote>")
            else:
                who = ", ".join(escape(a) for a in item["assignees"]) or "unassigned"
                lines.append(f"<blockquote>{title} · <i>{who}</i></blockquote>")
    return "\n".join(lines)


# --- Run -----------------------------------------------------------------------

def publish_board(telegram, state, text):
    message_id = state.get("message_id")
    if message_id is not None and telegram.edit(message_id, text):
        return
    state["message_id"] = telegram.send(text, silent=True)
    try:
        telegram.pin(state["message_id"])
    except TelegramError as error:
        # The board is still useful unpinned; the bot just needs the pin right.
        print(f"::warning::Could not pin the board message: {error}")


def run(telegram, state, project, nodes, now, columns=DEFAULT_COLUMNS):
    current = snapshot(nodes)
    board = render_board(project, current, now, columns)
    if "items" not in state:
        state["items"] = current
        publish_board(telegram, state, board)
        telegram.send(
            f'Board tracking started · <a href="{escape(project["url"])}">'
            f'{escape(project["title"])}</a>',
            silent=True,
        )
        return f"Baseline recorded for {len(current)} items."
    if state["items"] == current:
        return "No board changes."
    changes = diff(state["items"], current)
    if changes:
        telegram.send(render_changes(changes), silent=True)
    publish_board(telegram, state, board)
    state["items"] = current
    return f"Posted {len(changes)} board changes."


def main():
    bot_token, chat_id, state_token, repository, project_token, owner, number = require_env(
        "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "STATE_TOKEN", "GITHUB_REPOSITORY",
        "PROJECTS_READ_TOKEN", "PROJECT_OWNER", "PROJECT_NUMBER",
    )
    columns = tuple(
        column.strip()
        for column in os.environ.get("BOARD_COLUMNS", ",".join(DEFAULT_COLUMNS)).split(",")
        if column.strip()
    )
    project, nodes = fetch_project(project_token, owner, int(number))
    telegram = TelegramClient(bot_token, chat_id)
    store = VariableState(repository, state_token, STATE_VARIABLE)
    state = store.load()
    outcome = run(telegram, state, project, nodes, time.time(), columns)
    store.save(state)
    print(outcome)


if __name__ == "__main__":
    main()
