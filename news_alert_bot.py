bash

mkdir -p /mnt/user-data/outputs/news-alert-github && cd /mnt/user-data/outputs/news-alert-github

cat > news_alert_bot.py << 'EOF'
"""
News alert bot (GitHub Actions version).
Runs ONCE per trigger: checks the feed, sends any new articles to Telegram,
and records them in seen_articles.json (which the workflow commits back to the repo).

Secrets come from environment variables (set as GitHub repository secrets):
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
Never paste your token into this file if the repo is public.
"""

import html
import json
import os
import re
import sys
import time

import feedparser
import requests

try:
    # Makes requests look like a real Chrome browser, which avoids many 403 blocks.
    from curl_cffi import requests as browser_requests
except ImportError:
    browser_requests = None

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# Comma-separated feed URLs can be overridden with the FEED_URLS env var.
DEFAULT_FEEDS = [
    "https://michaelwest.com.au/category/latest-posts/feed/",
    "https://michaelwest.com.au/feed/",
]
FEEDS = [u.strip() for u in os.getenv("FEED_URLS", "").split(",") if u.strip()] or DEFAULT_FEEDS

SEEN_FILE = "seen_articles.json"
SUMMARY_CHARS = 300
TEST_RESEND = os.getenv("TEST_RESEND") == "1"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept": "application/rss+xml,application/xml,text/xml,*/*"}


def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(seen), f, indent=0)


def clean(text):
    text = re.sub(r"<[^>]+>", "", text or "")
    return html.unescape(text).strip()


def fetch(url):
    if browser_requests is not None:
        resp = browser_requests.get(url, headers=HEADERS, impersonate="chrome", timeout=20)
    else:
        resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.content


def send_telegram(title, summary, link):
    message = f"<b>{html.escape(title)}</b>\n\n{html.escape(summary)}\n\n{link}"
    resp = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"},
        timeout=15,
    )
    resp.raise_for_status()


def main():
    seen = load_seen()
    first_run = len(seen) == 0  # nothing recorded yet -> record quietly, send nothing

    for url in FEEDS:
        try:
            feed = feedparser.parse(fetch(url))
        except Exception as e:
            print(f"Could not fetch {url}: {e}")
            continue
        if not feed.entries:
            print(f"{url}: fetched but no articles found, trying next")
            continue

        print(f"Checked {url}: {len(feed.entries)} articles found")

        if TEST_RESEND and not first_run:
            newest = feed.entries[0].get("id") or feed.entries[0].get("link")
            seen.discard(newest)
            print("Test mode: the newest article will be sent again.")

        sent = 0
        for entry in reversed(feed.entries):  # oldest first
            uid = entry.get("id") or entry.get("link")
            if not uid or uid in seen:
                continue
            seen.add(uid)
            if first_run:
                continue
            summary = clean(entry.get("summary", ""))[:SUMMARY_CHARS]
            try:
                send_telegram(clean(entry.get("title", "(no title)")), summary, entry.get("link", ""))
                sent += 1
                time.sleep(1)
            except requests.RequestException as e:
                print("Telegram error:", e)
                seen.discard(uid)  # retry on the next run

        save_seen(seen)
        if first_run:
            print(f"First run: recorded {len(seen)} existing articles, sent none.")
        else:
            print(f"Sent {sent} new article(s).")
        return

    print("All feeds failed.")
    sys.exit(1)  # makes the run show as failed in GitHub so you notice


if __name__ == "__main__":
    main()
EOF

cat > news-alert.yml << 'EOF'
name: News alert

on:
  schedule:
    - cron: "23 * * * *"   # every hour at :23 (UTC); off the top of the hour to avoid delays
  workflow_dispatch:
    inputs:
      test_resend:
        description: "Resend the newest article as a delivery test"
        type: boolean
        default: false

permissions:
  contents: write

concurrency:
  group: news-alert
  cancel-in-progress: false

jobs:
  check:
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip

      - name: Install dependencies
        run: pip install feedparser requests curl_cffi

      - name: Check feed and send new articles
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
          TEST_RESEND: ${{ inputs.test_resend && '1' || '0' }}
        run: python news_alert_bot.py

      - name: Save record of seen articles
        if: always()
        run: |
          git config user.name "news-bot"
          git config user.email "news-bot@users.noreply.github.com"
          git add seen_articles.json || true
          git diff --staged --quiet || (git commit -m "Update seen articles" && git push)
EOF

python3 -c "import ast;ast.parse(open('news_alert_bot.py').read());print('py ok')"
python3 -c "import yaml;d=yaml.safe_load(open('news-alert.yml'));print('yaml ok', list(d.keys()))" 2>&1 | head -3
Output

py ok
yaml ok ['name', True, 'permissions', 'concurrency', 'jobs']
