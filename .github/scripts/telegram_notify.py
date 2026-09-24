"""Post GitHub CI, pull request and release events to the team Telegram group."""

import json
import os
import re
import time
from pathlib import Path

from telegram_api import (
    TelegramClient, VariableState, escape, first_line, require_env, truncate,
)

STATE_VARIABLE = "TELEGRAM_NOTIFY_STATE"
FAILURE_CONCLUSIONS = {"failure", "timed_out"}
BOT_ACTORS = {"dependabot[bot]"}
CLOSING_KEYWORDS = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s+#(\d+)", re.IGNORECASE
)


def relative_time(unix=None):
    unix = int(unix or time.time())
    return f'<tg-time unix="{unix}" format="r">just now</tg-time>'


def link(url, text):
    return f'<a href="{escape(url)}">{escape(text)}</a>'


def author(login):
    return f"<i>{escape(login)}</i>"


def closing_issues(body):
    seen = []
    for number in CLOSING_KEYWORDS.findall(body or ""):
        if number not in seen:
            seen.append(number)
    return seen


# --- Rendering -----------------------------------------------------------------

def render_failure(run, repository_url):
    target = f"on <code>{escape(run['head_branch'])}</code>"
    numbers = [pr["number"] for pr in run.get("pull_requests") or []]
    if run.get("event") == "pull_request" and numbers:
        target = "on PR " + link(f"{repository_url}/pull/{numbers[0]}", f"#{numbers[0]}")
    verb = "timed out" if run["conclusion"] == "timed_out" else "failed"
    header = f"<b>{link(run['html_url'], run['name'] + ' ' + verb)} {target}</b>"
    sha = run["head_sha"]
    message = truncate(first_line((run.get("head_commit") or {}).get("message")), 120)
    commit = link(f"{repository_url}/commit/{sha}", sha[:7])
    return "\n".join([
        header,
        f"{escape(message)} · {commit}" if message else commit,
        f"{author(run['actor']['login'])} · {relative_time()}",
    ])


def pr_status(pr):
    if pr.get("merged"):
        text = f"merged into <code>{escape(pr['base']['ref'])}</code>"
        closes = closing_issues(pr.get("body"))
        if closes:
            repository_url = pr["base"]["repo"]["html_url"]
            links = ", ".join(link(f"{repository_url}/issues/{n}", f"#{n}") for n in closes)
            text += f" · Closes {links}"
        return text
    if pr.get("state") == "closed":
        return "closed without merge"
    return "draft" if pr.get("draft") else "ready for review"


def render_card(pr):
    number = link(pr["html_url"], f"#{pr['number']}")
    header = f"<b>PR {number} · {escape(truncate(pr['title'], 100))}</b>"
    return f"{header}\n{author(pr['user']['login'])} · {pr_status(pr)}"


def render_ready_ping(pr):
    number = link(pr["html_url"], f"#{pr['number']}")
    return f"PR {number} ready for review · {author(pr['user']['login'])}"


def render_release(release, repository_name):
    title = release.get("name") or release["tag_name"]
    kind = "pre-release published" if release.get("prerelease") else "published"
    lines = [
        f"<b>{link(release['html_url'], repository_name + ' ' + title)} {kind}</b>",
        f"{author(release['author']['login'])} · {relative_time()}",
    ]
    body = (release.get("body") or "").strip()
    if body:
        lines.append(f"<blockquote expandable>{escape(truncate(body, 1500))}</blockquote>")
    return "\n".join(lines)


# --- Event handling ------------------------------------------------------------

def upsert_card(telegram, state, pr):
    cards = state.setdefault("pr_cards", {})
    key = str(pr["number"])
    text = render_card(pr)
    if key in cards and telegram.edit(cards[key], text):
        return
    cards[key] = telegram.send(text, silent=True)


def drop_failure(telegram, state, key):
    message_id = state.setdefault("pr_failures", {}).pop(key, None)
    if message_id is not None:
        telegram.delete(message_id)


def handle_workflow_run(telegram, state, event):
    run = event["workflow_run"]
    if run["actor"]["login"] in BOT_ACTORS:
        return "Skipped bot-authored run."
    numbers = [pr["number"] for pr in run.get("pull_requests") or []]
    pr_key = str(numbers[0]) if run.get("event") == "pull_request" and numbers else None
    if run["conclusion"] in FAILURE_CONCLUSIONS:
        text = render_failure(run, event["repository"]["html_url"])
        if pr_key:
            # Keep only the latest failure per PR so the group shows current breakage.
            drop_failure(telegram, state, pr_key)
            state["pr_failures"][pr_key] = telegram.send(text, silent=False)
        else:
            telegram.send(text, silent=False)
        return f"Posted {run['conclusion']} for run {run['id']}."
    if run["conclusion"] == "success" and pr_key:
        drop_failure(telegram, state, pr_key)
        return f"Cleared failure for PR #{pr_key}."
    return f"Ignored conclusion {run['conclusion']}."


def handle_pull_request(telegram, state, event):
    pr = event["pull_request"]
    if pr["user"]["login"] in BOT_ACTORS:
        return "Skipped bot-authored pull request."
    action = event["action"]
    if action == "opened":
        upsert_card(telegram, state, pr)
        return f"Posted card for PR #{pr['number']}."
    if action == "ready_for_review":
        upsert_card(telegram, state, pr)
        telegram.send(render_ready_ping(pr), silent=True)
        return f"Updated card and pinged for PR #{pr['number']}."
    if action == "closed":
        key = str(pr["number"])
        if key in state.get("pr_cards", {}):
            upsert_card(telegram, state, pr)
            state["pr_cards"].pop(key, None)
        drop_failure(telegram, state, key)
        return f"Finalised PR #{pr['number']}."
    return f"Ignored pull request action {action}."


def handle_release(telegram, state, event):
    telegram.send(render_release(event["release"], event["repository"]["name"]), silent=False)
    return f"Posted release {event['release']['tag_name']}."


def handle_dispatch(telegram, state, event):
    message = (event.get("inputs") or {}).get("message") or "Telegram notifications test"
    telegram.send(f"{escape(message)} · {relative_time()}", silent=True)
    return "Posted test message."


HANDLERS = {
    "workflow_run": handle_workflow_run,
    "pull_request": handle_pull_request,
    "release": handle_release,
    "workflow_dispatch": handle_dispatch,
}


def handle(event_name, event, telegram, state):
    handler = HANDLERS.get(event_name)
    if handler is None:
        return f"No handler for {event_name}."
    return handler(telegram, state, event)


def main():
    bot_token, chat_id, state_token, repository = require_env(
        "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "STATE_TOKEN", "GITHUB_REPOSITORY"
    )
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    telegram = TelegramClient(bot_token, chat_id)
    store = VariableState(repository, state_token, STATE_VARIABLE)
    state = store.load()
    before = json.dumps(state, sort_keys=True)
    outcome = handle(os.environ["GITHUB_EVENT_NAME"], event, telegram, state)
    if json.dumps(state, sort_keys=True) != before:
        store.save(state)
    print(outcome)


if __name__ == "__main__":
    main()
