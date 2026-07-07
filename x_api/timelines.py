"""Pure parsers for x.com GraphQL responses — bytes/dict -> records, no network,
so they can be unit-tested against captured HAR bodies.

The trick that keeps this small: nearly every list endpoint (UserTweets, search,
replies, media, list, community, home, bookmarks, retweeters, followers) returns the
same envelope — a nest of `instructions -> entries -> itemContent -> tweet_results |
user_results`. X only varies the *root* it hangs under (user.result.timeline_v2,
search_by_raw_query.search_timeline, threaded_conversation_with_injections_v2, ...),
so a recursive walk to the first `instructions` list is more robust than N hardcoded
paths that rot on every frontend release.
"""
from __future__ import annotations

from .models import Tweet, XUser


def _find_instructions(obj):
    """First `instructions` list anywhere in the response, regardless of root."""
    if isinstance(obj, dict):
        v = obj.get("instructions")
        if isinstance(v, list):
            return v
        for val in obj.values():
            r = _find_instructions(val)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for val in obj:
            r = _find_instructions(val)
            if r is not None:
                return r
    return None


def _entries(payload: dict) -> list:
    """Flatten AddEntries.entries + AddToModule.moduleItems into one entry list."""
    instr = _find_instructions(payload.get("data", payload)) or []
    entries: list = []
    for ins in instr:
        entries.extend(ins.get("entries") or [])
        entries.extend(ins.get("moduleItems") or [])
    return entries


def _item_contents(entries: list):
    """Yield every itemContent, unwrapping module entries (items[].item.itemContent)."""
    for e in entries:
        node = e.get("content") or e.get("item") or {}
        it = node.get("itemContent")
        if it:
            yield it
        for sub in node.get("items", []):
            si = (sub.get("item") or {}).get("itemContent") or sub.get("itemContent")
            if si:
                yield si


def _cursor(entries: list, kind: str = "Bottom"):
    for e in entries:
        node = e.get("content") or e.get("item") or {}
        if node.get("entryType") == "TimelineTimelineCursor" and node.get("cursorType") == kind:
            return node.get("value")
        it = node.get("itemContent") or {}
        if it.get("cursorType") == kind:
            return it.get("value")
    return None


def _unwrap_tweet(result: dict | None) -> dict | None:
    """tweet_results.result is Tweet | TweetWithVisibilityResults | TweetTombstone."""
    if not result:
        return None
    if result.get("__typename") == "TweetWithVisibilityResults":
        result = result.get("tweet", {})
    if result.get("__typename") == "TweetTombstone":
        return None
    return result or None


def parse_tweet(result: dict | None) -> Tweet | None:
    t = _unwrap_tweet(result)
    if not t or "legacy" not in t:
        return None
    leg = t["legacy"]
    user = (((t.get("core") or {}).get("user_results") or {}).get("result")) or {}
    uleg = user.get("legacy") or {}
    ucore = user.get("core") or {}
    # long-form ("note") tweets carry the real text outside legacy.full_text
    text = leg.get("full_text", "")
    note = (((t.get("note_tweet") or {}).get("note_tweet_results") or {}).get("result")) or {}
    if note.get("text"):
        text = note["text"]
    views = (t.get("views") or {}).get("count")
    return Tweet(
        rest_id=str(t.get("rest_id") or leg.get("id_str", "")),
        user_screen_name=uleg.get("screen_name") or ucore.get("screen_name", ""),
        user_name=uleg.get("name") or ucore.get("name", ""),
        created_at=leg.get("created_at", ""),
        text=text,
        lang=leg.get("lang", ""),
        reply_count=leg.get("reply_count", 0) or 0,
        retweet_count=leg.get("retweet_count", 0) or 0,
        like_count=leg.get("favorite_count", 0) or 0,
        quote_count=leg.get("quote_count", 0) or 0,
        view_count=int(views) if views and str(views).isdigit() else 0,
        is_retweet="retweeted_status_result" in leg,
        is_quote=bool(leg.get("is_quote_status", False)),
        raw=t,
    )


def parse_user_result(result: dict | None) -> XUser | None:
    """A `...user_results.result` node (from UserByScreenName or a user timeline)."""
    if not result:
        return None
    leg = result.get("legacy") or {}
    core = result.get("core") or {}
    loc = (result.get("location") or {}).get("location", "") or leg.get("location", "")
    return XUser(
        rest_id=str(result.get("rest_id", "")),
        screen_name=leg.get("screen_name") or core.get("screen_name", ""),
        name=leg.get("name") or core.get("name", ""),
        description=leg.get("description", ""),
        followers_count=leg.get("followers_count", 0) or 0,
        friends_count=leg.get("friends_count", 0) or 0,
        statuses_count=leg.get("statuses_count", 0) or 0,
        verified=bool(leg.get("verified", False) or result.get("is_blue_verified")),
        created_at=leg.get("created_at") or core.get("created_at", ""),
        location=loc,
        raw=result,
    )


def parse_user_by_screenname(payload: dict) -> XUser | None:
    result = (((payload.get("data") or {}).get("user") or {}).get("result")) or {}
    return parse_user_result(result)


def parse_timeline_tweets(payload: dict) -> tuple[list[Tweet], str | None]:
    """Tweets + bottom cursor from any tweet timeline."""
    entries = _entries(payload)
    tweets = []
    for it in _item_contents(entries):
        tw = parse_tweet((it.get("tweet_results") or {}).get("result"))
        if tw:
            tweets.append(tw)
    return tweets, _cursor(entries)


def parse_timeline_users(payload: dict) -> tuple[list[XUser], str | None]:
    """Users + bottom cursor from any user timeline (retweeters, followers, ...)."""
    entries = _entries(payload)
    users = []
    for it in _item_contents(entries):
        u = parse_user_result((it.get("user_results") or {}).get("result"))
        if u and u.rest_id:
            users.append(u)
    return users, _cursor(entries)
