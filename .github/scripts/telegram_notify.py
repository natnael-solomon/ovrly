"""Post GitHub CI, pull request and release events to the team Telegram group."""

import json
import os
import re
import sys
from pathlib import Path

from telegram_api import (
    GitHubClient, TelegramClient, VariableState, display_name, escape, first_line, require_env,
    truncate,
)

STATE_VARIABLE = "TELEGRAM_NOTIFY_STATE"
FAILURE_CONCLUSIONS = {"failure", "timed_out"}
BOT_ACTORS = {"dependabot[bot]"}
# Later events that stand in for a delayed or dropped "opened" webhook.
RESILIENCE_ACTIONS = {"synchronize", "reopened"}
CATCH_UP_EVENTS = {"schedule", "workflow_dispatch"}
OPEN_PULLS = "pulls?state=open"
CLOSING_KEYWORDS = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s+#(\d+)", re.IGNORECASE
)


def link(url, text):
    return f'<a href="{escape(url)}">{escape(text)}</a>'


def author(login):
    return f"<i>{display_name(login)}</i>"


RULE = "╌" * 12
QUOTE_LIMIT = 20
GAP = object()


def compose(headline, *body):
    """Shared skeleton: bold linked headline over a sleek rule, then body lines; GAP inserts a blank line."""
    lines = [f"<b>{headline}</b>", RULE]
    lines.extend("" if line is GAP else line for line in body if line)
    return "\n".join(lines)


def field(label, value):
    return f"<b>{label}</b>  {value}"


def quote(text):
    return f"<blockquote>{escape(text)}</blockquote>"


def closing_issues(body):
    seen = []
    for number in CLOSING_KEYWORDS.findall(body or ""):
        if number not in seen:
            seen.append(number)
    return seen


# --- Rendering -----------------------------------------------------------------

def render_failure(run, repository_url):
    target = f"on {run['head_branch']}"
    numbers = [pr["number"] for pr in run.get("pull_requests") or []]
    if run.get("event") == "pull_request" and numbers:
        target = f"on PR #{numbers[0]}"
    verb = "timed out" if run["conclusion"] == "timed_out" else "failed"
    sha = run["head_sha"]
    message = truncate(first_line((run.get("head_commit") or {}).get("message")), QUOTE_LIMIT)
    return compose(
        link(run["html_url"], f"{run['name']} {verb} {target}"),
        quote(message) if message else "",
        GAP,
        field("Commit", link(f"{repository_url}/commit/{sha}", sha[:7])),
        field("By", author(run["actor"]["login"])),
    )


def pr_status(pr):
    if pr.get("merged"):
        text = f"merged into <code>{escape(pr['base']['ref'])}</code>"
        closes = closing_issues(pr.get("body"))
        if closes:
            repository_url = pr["base"]["repo"]["html_url"]
            links = ", ".join(link(f"{repository_url}/issues/{n}", f"#{n}") for n in closes)
            text += f", closes {links}"
        return text
    if pr.get("state") == "closed":
        return "closed without merge"
    return "draft" if pr.get("draft") else "ready for review"


def render_card(pr):
    return compose(
        link(pr["html_url"], f"PR #{pr['number']}"),
        quote(truncate(pr["title"], QUOTE_LIMIT)),
        GAP,
        field("Status", pr_status(pr)),
        field("By", author(pr["user"]["login"])),
    )


def render_release(release, repository_name):
    title = release.get("name") or release["tag_name"]
    kind = "pre-release" if release.get("prerelease") else "release"
    summary = truncate(first_line(release.get("body")), QUOTE_LIMIT)
    return compose(
        link(release["html_url"], f"{repository_name} {title}"),
        quote(summary) if summary else "",
        GAP,
        field("Type", kind),
        field("By", author(release["author"]["login"])),
    )


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


def announced(state, pr):
    """The card entry is the announcement record: written on first post, removed on close."""
    return str(pr["number"]) in state.get("pr_cards", {})


def handle_pull_request(telegram, state, event):
    pr = event["pull_request"]
    if pr["user"]["login"] in BOT_ACTORS:
        return "Skipped bot-authored pull request."
    action = event["action"]
    if action in ("opened", "ready_for_review"):
        upsert_card(telegram, state, pr)
        return f"Updated card for PR #{pr['number']}."
    if action in RESILIENCE_ACTIONS:
        if announced(state, pr):
            return f"PR #{pr['number']} already announced; ignored {action}."
        upsert_card(telegram, state, pr)
        return f"Announced PR #{pr['number']} on {action}."
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


def catch_up(telegram, state, github):
    """Announce open PRs that have no card recorded, so a PR whose webhooks were all dropped still appears."""
    numbers = []
    for pr in sorted(github.paginate(OPEN_PULLS), key=lambda pr: pr["number"]):
        if pr["user"]["login"] in BOT_ACTORS or announced(state, pr):
            continue
        upsert_card(telegram, state, pr)
        numbers.append(f"#{pr['number']}")
    if not numbers:
        return "Every open pull request is announced."
    return f"Announced {', '.join(numbers)} missed by webhooks."


HANDLERS = {
    "workflow_run": handle_workflow_run,
    "pull_request": handle_pull_request,
    "release": handle_release,
}


def handle(event_name, event, telegram, state, github=None):
    if event_name in CATCH_UP_EVENTS:
        return catch_up(telegram, state, github)
    handler = HANDLERS.get(event_name)
    if handler is None:
        return f"No handler for {event_name}."
    return handler(telegram, state, event)


# --- State ---------------------------------------------------------------------

def validate_state(state):
    """Reject state this notifier did not write; posting on a guess risks duplicate cards."""
    if not isinstance(state, dict):
        raise ValueError("state is not a JSON object")
    for name in ("pr_cards", "pr_failures"):
        table = state.get(name, {})
        if not isinstance(table, dict) or not all(
            isinstance(key, str) and isinstance(value, int) for key, value in table.items()
        ):
            raise ValueError(f"{name} is not a map of PR numbers to message ids")


def needs_state(event_name, event):
    """Events whose only duplicate guard is the recorded state."""
    return event_name in CATCH_UP_EVENTS or (
        event_name == "pull_request" and event.get("action") in RESILIENCE_ACTIONS
    )


def load_state(store, event_name, event):
    """Load state, failing closed (nothing posted, non-zero exit) when it cannot support dedupe."""
    try:
        state = store.load()
        validate_state(state)
    except ValueError as error:
        print(f"::error::{STATE_VARIABLE} is malformed ({error}); skipping to avoid duplicate posts.")
        sys.exit(1)
    if not store.exists and needs_state(event_name, event):
        print(f"::error::{STATE_VARIABLE} is missing; skipping {event_name} to avoid duplicate posts.")
        sys.exit(1)
    return state


def main():
    bot_token, chat_id, state_token, repository = require_env(
        "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "STATE_TOKEN", "GITHUB_REPOSITORY"
    )
    event_name = os.environ["GITHUB_EVENT_NAME"]
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    github = None
    if event_name in CATCH_UP_EVENTS:
        (github_token,) = require_env("GITHUB_TOKEN")
        github = GitHubClient(github_token, repository)
    telegram = TelegramClient(bot_token, chat_id)
    store = VariableState(repository, state_token, STATE_VARIABLE)
    state = load_state(store, event_name, event)
    before = json.dumps(state, sort_keys=True)
    outcome = handle(event_name, event, telegram, state, github)
    if json.dumps(state, sort_keys=True) != before:
        store.save(state)
    print(outcome)


if __name__ == "__main__":
    main()
