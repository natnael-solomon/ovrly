"""Send a freshly built APK to the team Telegram chat with a release-style caption."""

import os
import sys
from pathlib import Path

from telegram_api import TelegramClient, display_name, escape, first_line, require_env, truncate
from telegram_notify import GAP, QUOTE_LIMIT, compose, field, link, quote

MAX_UPLOAD_BYTES = 50 * 1024 * 1024


def render_caption(app, version, sha, commit_subject, actor, repository_url, build_type):
    return compose(
        link(f"{repository_url}/commit/{sha}", f"{app} {version} ({sha[:7]})"),
        quote(truncate(first_line(commit_subject), QUOTE_LIMIT)) if commit_subject else "",
        GAP,
        field("Type", escape(build_type)),
        field("By", f"<i>{display_name(actor)}</i>"),
    )


def main():
    bot_token, chat_id, apk_path, version, sha, actor, repository_url = require_env(
        "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "APK_PATH", "APP_VERSION",
        "GITHUB_SHA", "GITHUB_ACTOR", "REPOSITORY_URL",
    )
    apk = Path(apk_path)
    if not apk.is_file():
        print(f"::error::APK not found at {apk}")
        sys.exit(1)
    size = apk.stat().st_size
    if size > MAX_UPLOAD_BYTES:
        print(f"::error::APK is {size / 1_048_576:.1f} MB; Telegram bots can upload at most 50 MB")
        sys.exit(1)

    caption = render_caption(
        os.environ.get("APP_NAME", "ovrly"), version, sha,
        os.environ.get("COMMIT_SUBJECT", ""), actor, repository_url,
        os.environ.get("BUILD_TYPE", "unsigned release build"),
    )
    telegram = TelegramClient(bot_token, chat_id)
    telegram.send_document(apk, caption, silent=False)
    print(f"Sent {apk.name} ({size / 1_048_576:.1f} MB).")


if __name__ == "__main__":
    main()
