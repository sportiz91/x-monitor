"""XClient — one read-only client over x.com's private GraphQL API.

Every op's queryId + the ~39 `features` flags x.com validates + a variables template
live in ops.json (extracted from real captured sessions). The client just fills the
dynamic variables (userId, query, cursor, ...) and replays the request. Read-only:
it never calls the Create*/Favorite*/Retweet write mutations.
"""
from __future__ import annotations

import json
import os
from urllib.parse import urlencode

from . import timelines as T
from .auth import Auth
from .models import Tweet, XUser

_OPS = json.load(open(os.path.join(os.path.dirname(__file__), "ops.json"), encoding="utf-8"))
BASE = "https://x.com/i/api/graphql"
_PAGE = 100  # x.com caps most timelines around 100/page
# Ops x.com refuses (404) without a valid x-client-transaction-id. Most ops don't
# care; search is the hardened one. Keep this list tight — needless tx headers only
# add a failure mode.
TX_REQUIRED = {"SearchTimeline"}


class XClient:
    def __init__(self, auth: Auth | None = None):
        self.auth = auth or Auth.from_env()

    # ------------------------------ transport ------------------------------- #
    def _spec(self, op: str) -> dict:
        s = _OPS.get(op)
        if not s:
            raise KeyError(f"op {op!r} not in ops.json — needs a fresh capture")
        return s

    def _call(self, op: str, variables: dict) -> dict:
        s = self._spec(op)
        if op in TX_REQUIRED and not self.auth.tx_id:
            raise RuntimeError(
                f"{op} needs a valid x-client-transaction-id — set X_TX_ID in .env "
                "(copy it from a real search request's headers in DevTools). "
                "It's reusable for a while; refresh when search starts 404-ing."
            )
        v = {**(s.get("variables") or {}), **variables}
        feats = s.get("features") or {}
        tx = op in TX_REQUIRED
        if s.get("method") == "POST":
            url = f"{BASE}/{s['queryId']}/{op}"
            body = {"variables": v, "features": feats, "queryId": s["queryId"]}
            r = self.auth.session.post(url, headers=self.auth.headers(tx=tx), json=body, timeout=30)
        else:
            qs = urlencode({
                "variables": json.dumps(v, separators=(",", ":")),
                "features": json.dumps(feats, separators=(",", ":")),
            })
            url = f"{BASE}/{s['queryId']}/{op}?{qs}"
            r = self.auth.session.get(url, headers=self.auth.headers(tx=tx), timeout=30)
        r.raise_for_status()
        return r.json()

    def _paginate(self, op, variables, parse, count):
        """Follow the bottom cursor until we have `count` records (or run dry)."""
        out, cursor, empty = [], None, 0
        while len(out) < count and empty < 2:
            v = {**variables, "count": min(count, _PAGE)}
            if cursor:
                v["cursor"] = cursor
            records, cursor = parse(self._call(op, v))
            if not records:
                empty += 1
            else:
                empty = 0
                out.extend(records)
            if not cursor:
                break
        # dedup preserving order (cursors overlap by one)
        seen, uniq = set(), []
        for x in out:
            if x.rest_id in seen:
                continue
            seen.add(x.rest_id)
            uniq.append(x)
        return uniq[:count]

    # -------------------------------- users --------------------------------- #
    def get_user(self, handle: str) -> XUser | None:
        h = handle.lstrip("@").strip()
        return T.parse_user_by_screenname(self._call("UserByScreenName", {"screen_name": h}))

    def _resolve_uid(self, handle_or_id: str) -> str | None:
        s = str(handle_or_id).lstrip("@").strip()
        if s.isdigit():
            return s
        u = self.get_user(s)
        return u.rest_id if u else None

    # --------------------------- tweet timelines ---------------------------- #
    def _user_timeline(self, op, handle, count):
        uid = self._resolve_uid(handle)
        if not uid:
            return []
        return self._paginate(op, {"userId": uid}, T.parse_timeline_tweets, count)

    def user_tweets(self, handle, count=40):
        return self._user_timeline("UserTweets", handle, count)

    def user_replies(self, handle, count=40):
        return self._user_timeline("UserTweetsAndReplies", handle, count)

    def user_media(self, handle, count=40):
        return self._user_timeline("UserMedia", handle, count)

    def user_highlights(self, handle, count=40):
        return self._user_timeline("UserHighlightsTweets", handle, count)

    def search(self, query, count=40, product="Latest"):
        """product: Latest | Top | People | Media | Lists."""
        base = {"rawQuery": query, "querySource": "typed_query", "product": product}
        return self._paginate("SearchTimeline", base, T.parse_timeline_tweets, count)

    def list_tweets(self, list_id, count=40):
        return self._paginate("ListLatestTweetsTimeline", {"listId": str(list_id)},
                              T.parse_timeline_tweets, count)

    def community_tweets(self, community_id, count=40):
        return self._paginate("CommunityTweetsTimeline", {"communityId": str(community_id)},
                              T.parse_timeline_tweets, count)

    def bookmarks(self, count=40):
        return self._paginate("Bookmarks", {}, T.parse_timeline_tweets, count)

    def home(self, count=40):
        tweets, _ = T.parse_timeline_tweets(self._call("HomeTimeline", {"count": min(count, _PAGE)}))
        return tweets[:count]

    # ------------------------ single tweet + engagement --------------------- #
    def get_tweet(self, tweet_id) -> dict:
        """The focal tweet + its reply thread (TweetDetail)."""
        tid = str(tweet_id)
        tweets, _ = T.parse_timeline_tweets(self._call("TweetDetail", {"focalTweetId": tid}))
        focal = next((t for t in tweets if t.rest_id == tid), tweets[0] if tweets else None)
        replies = [t for t in tweets if t.rest_id != tid]
        return {"tweet": focal, "replies": replies}

    def retweeters(self, tweet_id, count=40) -> list[XUser]:
        return self._paginate("Retweeters", {"tweetId": str(tweet_id)},
                              T.parse_timeline_users, count)

    # ----------------------------- social graph ----------------------------- #
    def followers(self, handle, count=40) -> list[XUser]:
        uid = self._resolve_uid(handle)
        if not uid:
            return []
        return self._paginate("Followers", {"userId": uid}, T.parse_timeline_users, count)

    def following(self, handle, count=40) -> list[XUser]:
        uid = self._resolve_uid(handle)
        if not uid:
            return []
        return self._paginate("Following", {"userId": uid}, T.parse_timeline_users, count)
