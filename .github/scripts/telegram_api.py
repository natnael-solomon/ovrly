"""Shared Telegram Bot API client and GitHub-variable-backed state for notifications."""

import html
import json
import os
import sys
import urllib.error
import urllib.request

TELEGRAM_API = "https://api.telegram.org"
GITHUB_API = "https://api.github.com"

# Telegram rejects edits that leave the text unchanged and deletions of missing messages.
# Neither indicates a problem for a notifier, so they are treated as no-ops.
BENIGN_ERRORS = (
    "message is not modified",
    "message to delete not found",
    "message can't be deleted",
    "message to edit not found",
)


class TelegramError(RuntimeError):
    pass


def escape(text):
    """Escape text for Telegram's HTML parse mode."""
    return html.escape(str(text), quote=False)


def truncate(text, limit):
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)].rstrip() + "…"


def first_line(text):
    return (text or "").strip().splitlines()[0] if (text or "").strip() else ""


def http_json(url, method="GET", payload=None, headers=None):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    if body is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status = response.status
            raw = response.read()
    except urllib.error.HTTPError as error:
        raw = error.read()
        try:
            return error.code, json.loads(raw)
        except ValueError:
            return error.code, {"description": raw.decode("utf-8", "replace")}
    return status, (json.loads(raw) if raw else {})


class TelegramClient:
    def __init__(self, token, chat_id, transport=http_json):
        self._token = token
        self.chat_id = chat_id
        self._transport = transport

    def call(self, method, **params):
        url = f"{TELEGRAM_API}/bot{self._token}/{method}"
        params.setdefault("chat_id", self.chat_id)
        try:
            status, response = self._transport(url, "POST", params)
        except urllib.error.URLError as error:
            # Never echo the URL: it embeds the bot token.
            raise TelegramError(f"{method}: network error: {error.reason}") from None
        if response.get("ok"):
            return response.get("result")
        description = response.get("description", f"HTTP {status}")
        if any(marker in description.lower() for marker in BENIGN_ERRORS):
            print(f"::notice::Telegram {method}: {description}")
            return None
        raise TelegramError(f"{method}: {description}")

    def send(self, text, silent=True):
        result = self.call(
            "sendMessage",
            text=text,
            parse_mode="HTML",
            link_preview_options={"is_disabled": True},
            disable_notification=silent,
        )
        return result["message_id"]

    def edit(self, message_id, text):
        """Edit a message; returns False when the message no longer exists."""
        result = self.call(
            "editMessageText",
            message_id=message_id,
            text=text,
            parse_mode="HTML",
            link_preview_options={"is_disabled": True},
        )
        return result is not None

    def delete(self, message_id):
        self.call("deleteMessage", message_id=message_id)

    def pin(self, message_id):
        self.call("pinChatMessage", message_id=message_id, disable_notification=True)


class VariableState:
    """JSON state persisted in a GitHub Actions repository variable."""

    def __init__(self, repository, token, name, transport=http_json):
        self._url = f"{GITHUB_API}/repos/{repository}/actions/variables"
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        self._name = name
        self._transport = transport
        self._exists = False

    def load(self):
        status, response = self._transport(f"{self._url}/{self._name}", headers=self._headers)
        if status == 404:
            return {}
        if status != 200:
            raise RuntimeError(f"Could not read variable {self._name}: HTTP {status}")
        self._exists = True
        value = (response.get("value") or "").strip()
        return json.loads(value) if value else {}

    def save(self, state):
        value = json.dumps(state, separators=(",", ":"), sort_keys=True)
        if len(value.encode("utf-8")) > 45_000:
            raise RuntimeError(f"State for {self._name} is too large for a repository variable")
        payload = {"name": self._name, "value": value}
        if self._exists:
            status, _ = self._transport(
                f"{self._url}/{self._name}", "PATCH", payload, self._headers
            )
        else:
            status, _ = self._transport(self._url, "POST", payload, self._headers)
        if status not in (201, 204):
            raise RuntimeError(f"Could not write variable {self._name}: HTTP {status}")
        self._exists = True


def require_env(*names):
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        print(f"::error::Missing configuration: {', '.join(missing)}")
        sys.exit(1)
    return [os.environ[name] for name in names]
