"""Offline tests: parse the captured HARs (no network) and assert the pure parsers
turn real x.com GraphQL bodies into records. Run: `python3.12 -m tests.test_offline`.

The HARs live in research/ (gitignored — they carry session cookies). If they're
absent the relevant checks skip rather than fail, so CI without the captures is green.
"""
from __future__ import annotations

import base64
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from x_api import timelines as T  # noqa: E402

RESEARCH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research")
HARS = [os.path.join(RESEARCH, f) for f in ("x1.har", "x2.har", "x3.har", "x4.har")]


def _first_body(op: str):
    for fn in HARS:
        if not os.path.exists(fn):
            continue
        har = json.load(open(fn))
        for e in har["log"]["entries"]:
            u = e["request"]["url"]
            if "graphql" in u and f"/{op}" in u:
                c = e["response"].get("content", {})
                txt = c.get("text")
                if not txt:
                    continue
                if c.get("encoding") == "base64":
                    try:
                        txt = base64.b64decode(txt).decode("utf-8", "ignore")
                    except Exception:
                        continue
                try:
                    return json.loads(txt)
                except Exception:
                    continue
    return None


def check(name, cond, detail=""):
    status = "ok  " if cond else "FAIL"
    print(f"  [{status}] {name} {detail}")
    return cond


def main() -> int:
    if not any(os.path.exists(h) for h in HARS):
        print("no HARs in research/ — skipping (captures are gitignored).")
        return 0

    ok = True
    print("parsers vs captured bodies:")

    b = _first_body("UserByScreenName")
    if b is not None:
        u = T.parse_user_by_screenname(b)
        ok &= check("UserByScreenName", bool(u and u.rest_id and u.screen_name),
                    f"→ @{u.screen_name} ({u.followers_count:,})" if u else "")

    for op in ("UserTweets", "UserTweetsAndReplies", "UserMedia", "TweetDetail",
               "ListLatestTweetsTimeline"):
        b = _first_body(op)
        if b is None:
            continue
        tweets, cur = T.parse_timeline_tweets(b)
        # a tweet must have an id and its author resolved
        good = bool(tweets) and all(t.rest_id for t in tweets) and any(t.user_screen_name for t in tweets)
        ok &= check(op, good, f"→ {len(tweets)} tweets")

    for op in ("Retweeters", "Followers", "Following"):
        b = _first_body(op)
        if b is None:
            continue
        users, cur = T.parse_timeline_users(b)
        ok &= check(op, bool(users) and all(u.rest_id for u in users), f"→ {len(users)} users")

    print("\nRESULT:", "PASS ✅" if ok else "FAIL ❌")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
