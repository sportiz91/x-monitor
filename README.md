# x-monitor

Reverse-engineered read-only client for **x.com (Twitter)**'s private GraphQL API,
authenticated with a member session cookie (`auth_token` + `ct0`). Designed to
optionally feed a 24/7 monitor later on.

The interesting part: x.com serves almost everything through one GraphQL surface
(`/i/api/graphql/{queryId}/{Operation}`), and nearly every list endpoint returns the
**same response envelope** (`instructions → entries → tweet_results | user_results`).
So one pair of parsers (`parse_timeline_tweets` / `parse_timeline_users`) covers most ops.

## What it does — 14 read ops (validated live)

| Surface | Command | Op |
|---|---|---|
| Profile | `user <handle>` | UserByScreenName |
| A user's posts | `tweets <handle>` | UserTweets |
| Posts + replies | `replies <handle>` | UserTweetsAndReplies |
| Media posts | `media <handle>` | UserMedia |
| Highlights | `highlights <handle>` | UserHighlightsTweets |
| Tweet + reply thread | `tweet <id>` | TweetDetail |
| Keyword search | `search "<query>"` | SearchTimeline † |
| Who reposted | `retweeters <id>` | Retweeters |
| Followers / Following | `followers` / `following <handle>` | Followers / Following |
| List timeline | `list <id>` | ListLatestTweetsTimeline |
| Community timeline | `community <id>` | CommunityTweetsTimeline |
| Bookmarks | `bookmarks` | Bookmarks |
| Home feed | `home` | HomeTimeline |

† `search` is the one hardened endpoint — it needs a valid `x-client-transaction-id`
(`X_TX_ID`) and is rate-limited. See below.

## Setup

```bash
python3.12 -m pip install "curl_cffi>=0.15"
```

Create `.env` (gitignored) with your session cookies — DevTools → Application → Cookies → x.com:

```
X_AUTH_TOKEN=...        # the auth_token cookie (the session bearer)
X_CT0=...              # the ct0 cookie (also sent as the x-csrf-token header)
X_TX_ID=...            # optional; only search needs it (x-client-transaction-id)
```

A logout rotates these. Keep `.env` private.

## Usage

```python
from x_api import XClient

c = XClient()                                  # reads .env
u = c.get_user("naval")                        # profile
print(u.name, u.followers_count)

for t in c.user_tweets("naval", count=40):     # ranked-able by engagement
    print(t.like_count, t.text)

for t in c.search("monaco gp tickets", count=20, product="Latest"):
    print(t.user_screen_name, t.text)          # needs X_TX_ID; rate-limited

for f in c.followers("naval", count=40):
    print(f.screen_name, f.followers_count)
```

Or via the CLI (what the `twitter-api` skill drives):

```bash
python3.12 ~/.claude/skills/twitter-api/scripts/x.py user naval
python3.12 ~/.claude/skills/twitter-api/scripts/x.py tweets naval --count 20
```

## Testing

```bash
python3.12 -m tests.test_offline     # parses the captured HARs, no network
```

## Layout

```
x_api/
  auth.py       Auth: cookies (auth_token/ct0), curl_cffi(chrome124) session, headers, tx-id
  client.py     XClient — one method per surface; pagination via the bottom cursor
  timelines.py  pure parsers: instructions→entries→tweet_results|user_results (+ tweet/user)
  models.py     Tweet / XUser (flat dataclasses)
  ops.json      per-op queryId + the ~39 features x.com validates + variables template
docs/SURFACES.md  how each surface was reverse-engineered (+ the search endpoint in full)
research/       captured HARs (gitignored; contain session cookies)
```

## The `x-client-transaction-id` finding

x.com sends `x-client-transaction-id` (a client-JS-computed header) on every GraphQL
call but only **enforces** it on a few hardened endpoints — of our 15, only `SearchTimeline`.
The 14 others return 200 without it (validated: a bare curl_cffi replay from a residential
IP works). X validates the header's *content* (a random one 404s) but not its freshness
(a 2-hour-old captured one still works), so a captured `X_TX_ID` is reusable until it rots.
A proper runtime generator (from the JS bundle + animation SVG) is a future addition if
search becomes central.

**Full reversing notes** — the controlled tests that isolated each wall, the shared
timeline envelope, and op discovery — are in [`docs/SURFACES.md`](docs/SURFACES.md).

## Responsible use

Only against your own account / authorized targets. Read-only — never posts/likes/reposts.
Rate-limit, add jitter, keep counts modest. Datacenter IPs (a VPS) are the #1 flag signal —
run from a residential IP that matches where the cookie was minted. A future 24/7 monitor
would egress through a home residential tunnel before ever running on a VPS.
