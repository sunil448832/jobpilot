#!/usr/bin/env python3
"""
telegram_setup.py — one-shot Telegram bot setup for the approval pipeline.

Verifies the token, waits for you to message the bot, captures your chat id,
writes ~/.config/jobbot/env (mode 600), and sends a confirmation message.

Usage:
    python jobs/telegram_setup.py                 # prompts for the token
    python jobs/telegram_setup.py --token 123:ABC
    python jobs/telegram_setup.py --test          # just send a test message
"""
import argparse
import getpass
import json
import os
import sys
import time
import urllib.request

ENV_PATH = os.path.expanduser("~/.config/jobbot/env")
API = "https://api.telegram.org/bot{token}/{method}"


def call(token, method, **params):
    url = API.format(token=token, method=method)
    if params:
        import urllib.parse
        url += "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=40) as r:
        return json.load(r)


def load_env():
    env = {}
    if os.path.isfile(ENV_PATH):
        for line in open(ENV_PATH):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def write_env(token, chat_id):
    os.makedirs(os.path.dirname(ENV_PATH), exist_ok=True)
    with open(ENV_PATH, "w") as f:
        f.write(f"TELEGRAM_BOT_TOKEN={token}\nTELEGRAM_CHAT_ID={chat_id}\n")
    os.chmod(ENV_PATH, 0o600)
    print(f"  [saved] {ENV_PATH} (mode 600)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--token")
    ap.add_argument("--test", action="store_true", help="send a test message using saved env")
    ap.add_argument("--wait", type=int, default=120, help="seconds to wait for your message")
    args = ap.parse_args()

    env = load_env()

    if args.test:
        token, chat_id = env.get("TELEGRAM_BOT_TOKEN"), env.get("TELEGRAM_CHAT_ID")
        if not (token and chat_id):
            sys.exit(f"No saved credentials in {ENV_PATH}. Run without --test first.")
        r = call(token, "sendMessage", chat_id=chat_id, text="jobbot: test message OK")
        print("  [send]", "delivered" if r.get("ok") else r)
        return

    token = args.token or env.get("TELEGRAM_BOT_TOKEN") or getpass.getpass("Bot token: ").strip()

    me = call(token, "getMe")
    if not me.get("ok"):
        sys.exit(f"Token rejected: {me}")
    bot = me["result"]["username"]
    print(f"  [bot] @{bot} — token valid")

    # Drain nothing: just poll until a private message shows up.
    print(f"  [wait] open Telegram, message @{bot} (tap START), waiting up to {args.wait}s ...")
    deadline = time.time() + args.wait
    chat_id = None
    offset = None
    while time.time() < deadline and chat_id is None:
        params = {"timeout": 10}
        if offset is not None:
            params["offset"] = offset
        r = call(token, "getUpdates", **params)
        for upd in r.get("result", []):
            offset = upd["update_id"] + 1
            msg = upd.get("message") or upd.get("edited_message") or {}
            chat = msg.get("chat") or {}
            if chat.get("type") == "private":
                chat_id = chat["id"]
                who = chat.get("username") or chat.get("first_name") or "you"
                print(f"  [found] chat_id={chat_id} (from @{who})")
                break
        if chat_id is None:
            time.sleep(2)

    if chat_id is None:
        print(f"\n  No message received in {args.wait}s.")
        print(f"  Search @{bot} in Telegram, tap the blue START button, send any text, rerun.")
        print("  Fallback: message @userinfobot for your user id — in a private chat that")
        print("  IS your chat_id. Then: python jobs/telegram_setup.py --token <t> and paste it.")
        sys.exit(1)

    write_env(token, chat_id)
    r = call(token, "sendMessage", chat_id=chat_id,
             text="jobbot connected. Approval cards will arrive here.")
    print("  [send]", "confirmation delivered — check your phone" if r.get("ok") else r)


if __name__ == "__main__":
    main()
