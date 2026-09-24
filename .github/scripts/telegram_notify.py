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


GAP = object()


def compose(headline, *body):
    """Shared skeleton: bold linked headline, then body lines; GAP inserts a blank line."""
    lines = [f"<b>{headline}</b>"]
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
        target = f"on PR#{numbers[0]}"
    verb = "timed out" if run["conclusion"] == "timed_out" else "failed"
    sha = run["head_sha"]
    message = truncate(first_line((run.get("head_commit") or {}).get("message")), 120)
    return compose(
        link(run["html_url"], f"{run['name']} {verb} {target}"),
        quote(message) if message else "",
        GAP,
        field("Commit", link(f"{repository_url}/commit/{sha}", sha[:7])),
        field("By", author(run["actor"]["login"])),
        field("When", relative_time()),
    )


def pr_status(pr):
    if pr.get("merged"):
        text = f"Merged into <code>{escape(pr['base']['ref'])}</code>"
        closes = closing_issues(pr.get("body"))
        if closes:
            repository_url = pr["base"]["repo"]["html_url"]
            links = ", ".join(link(f"{repository_url}/issues/{n}", f"#{n}") for n in closes)
            text += f", closes {links}"
        return text
    if pr.get("state") == "closed":
        return "Closed without merge"
    return "Draft" if pr.get("draft") else "Ready for review"


def render_card(pr):
    return compose(
        link(pr["html_url"], f"PR#{pr['number']}"),
        quote(truncate(pr["title"], 100)),
        GAP,
        field("Status", pr_status(pr)),
        field("By", author(pr["user"]["login"])),
    )


def render_release(release, repository_name):
    title = release.get("name") or release["tag_name"]
    kind = "Pre-release" if release.get("prerelease") else "Release"
    body = (release.get("body") or "").strip()
    if len(body) > 1500:
        body = body[:1499].rstrip() + "…"
    return compose(
        link(release["html_url"], f"{repository_name} {title}"),
        f"<blockquote expandable>{escape(body)}</blockquote>" if body else "",
        GAP,
        field("Type", kind),
        field("By", author(release["author"]["login"])),
        field("When", relative_time()),
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


def handle_pull_request(telegram, state, event):
    pr = event["pull_request"]
    if pr["user"]["login"] in BOT_ACTORS:
        return "Skipped bot-authored pull request."
    action = event["action"]
    if action in ("opened", "ready_for_review"):
        upsert_card(telegram, state, pr)
        return f"Updated card for PR #{pr['number']}."
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


def sample_event(kind, repository):
    """Synthetic payloads so message layouts can be previewed from workflow_dispatch."""
    url = repository["html_url"]
    pr = {
        "number": 0, "title": "feat(sample): preview pull request card", "draft": False,
        "merged": kind == "pr_merged", "state": "closed" if kind == "pr_merged" else "open",
        "body": "Closes #1" if kind == "pr_merged" else "",
        "html_url": f"{url}/pulls", "user": {"login": "sample"},
        "base": {"ref": "main", "repo": {"html_url": url}},
    }
    if kind == "failure":
        return "workflow_run", {"repository": repository, "workflow_run": {
            "id": 0, "name": "Android CI", "conclusion": "failure", "event": "push",
            "head_branch": "sample", "head_sha": "0" * 40, "html_url": f"{url}/actions",
            "head_commit": {"message": "fix(sample): preview failure post"},
            "actor": {"login": "sample"}, "pull_requests": [],
        }}
    if kind == "release":
        return "release", {"repository": repository, "release": {
            "tag_name": "v0.0.0-sample", "name": None, "prerelease": True,
            "body": "Preview of release notes.\n\n- one\n- two",
            "html_url": f"{url}/releases", "author": {"login": "sample"},
        }}
    return "pull_request", {"repository": repository, "pull_request": pr,
                            "action": "closed" if kind == "pr_merged" else "ready_for_review"}


SAMPLES = ("failure", "pr_ready", "pr_merged", "release")


def handle_dispatch(telegram, state, event):
    inputs = event.get("inputs") or {}
    sample = inputs.get("sample") or ""
    if sample in SAMPLES:
        # Preview against a throwaway state so real cards and failures are untouched.
        event_name, payload = sample_event(sample, event["repository"])
        scratch = {}
        if sample == "pr_merged":
            upsert_card(telegram, scratch, dict(payload["pull_request"], merged=False, state="open"))
        outcome = HANDLERS[event_name](telegram, scratch, payload)
        return f"Previewed {sample}: {outcome}"
    message = inputs.get("message") or "Telegram notifications test"
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
