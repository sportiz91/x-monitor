# How x.com serves each surface (reverse-engineering notes)

Captured from real logged-in sessions (4 HARs in `research/`, 2026-07-06). The big
lesson, and the good news: unlike LinkedIn's split surfaces, x.com serves **almost
everything through one private GraphQL API** (`/i/api/graphql/{queryId}/{Operation}`),
and nearly every list endpoint returns the **same response envelope**. So one pair of
parsers covers ~all of it. The one exception — `search` — is documented in full below;
it's the hardened endpoint and the most interesting part of the reversing.

## Auth (all surfaces)

Cookies `auth_token` (the session bearer) + `ct0` (double-submit token). Headers
replayed with curl_cffi impersonating Chrome (`chrome124`) so the TLS/HTTP2
fingerprint matches a browser. See `x_api/auth.py`:

- `authorization: Bearer <public>` — the web-client bearer hardcoded in x.com's
  `main.js`. **Not a secret**; every browser and every client (twscrape, …) sends the
  same one. It identifies the *app*, not the *user*.
- `x-csrf-token` = the `ct0` cookie value (double-submit: header must equal cookie).
- `x-twitter-auth-type: OAuth2Session`, `x-twitter-active-user: yes`.

**Key finding:** a bare curl_cffi replay from a residential IP returns **200** on 14 of
our 15 ops — *without* the `x-client-transaction-id` header that x.com's JS sends on
every call. x.com sends that header everywhere but only **enforces** it on a few
hardened endpoints (of ours, only `SearchTimeline`). So for profiles, timelines and the
social graph, the only thing between you and the data is the cookie. No browser, no JS
challenge, no token minting. Datacenter IPs get flagged fast — run from residential.

## Op discovery (queryId + features + variables)

Each GraphQL op needs three things, all lifted from the captured requests into
`x_api/ops.json`:

- **`queryId`** — a hash in the path (`/graphql/{queryId}/UserTweets`). It rotates on
  x.com web releases (see *queryId rotation* below).
- **`features`** — a ~39-key object of boolean flags x.com **validates**; omit or
  mismatch them and the request 400s. Kept verbatim per op.
- **`variables`** — the query inputs. The client overrides the dynamic ones (`userId`,
  `screen_name`, `rawQuery`, `cursor`, …) and keeps the rest from the captured template.

## The shared timeline envelope (see `x_api/timelines.py`)

Almost every list op — `UserTweets`, `UserTweetsAndReplies`, `UserMedia`,
`SearchTimeline`, `TweetDetail`, `ListLatestTweetsTimeline`, `CommunityTweetsTimeline`,
`HomeTimeline`, `Bookmarks`, `Retweeters`, `Followers`, `Following` — returns the same
shape:

```
data → <root varies> → instructions[] → entries[] → content.itemContent
                                                   → tweet_results.result   (tweets)
                                                   → user_results.result    (users)
                     → a TimelineTimelineCursor entry (cursorType "Bottom") for paging
```

Only the **root** differs per op (`user.result.timeline_v2`, `search_by_raw_query.
search_timeline`, `threaded_conversation_with_injections_v2`, `bookmark_timeline_v2`,
…). Rather than hardcode N brittle paths, `_find_instructions()` does a **recursive walk
to the first `instructions` list** — robust across ops and across releases. Then:

- `parse_timeline_tweets()` → `[Tweet]` + bottom cursor
- `parse_timeline_users()` → `[XUser]` + bottom cursor

A `tweet_results.result` can be `Tweet`, `TweetWithVisibilityResults` (unwrap `.tweet`)
or `TweetTombstone` (skip); long-form "note" tweets carry their text in
`note_tweet.note_tweet_results.result.text` instead of `legacy.full_text`. `parse_tweet`
handles all three.

## Users & handle→id resolution

`UserByScreenName` (GET, `screen_name`) → the profile (`rest_id`, `followers_count`,
`description`, …). The **`rest_id` is the stable key** — screen_names change, ids don't.
Ops that take a `userId` (`UserTweets`, `Followers`, …) resolve a handle through
`get_user()` first (`_resolve_uid`), or accept a numeric id directly.

`TweetDetail` (GET, `focalTweetId`) returns the focal tweet **plus its reply thread** in
one timeline; `get_tweet()` splits the focal from the replies by id.

## The hard endpoint — `search` (`SearchTimeline`) ⚠️

Search is the one endpoint with real anti-scrape defenses. It's the most abused for
scraping, so x.com protects it with **two independent walls** the other 14 ops don't
have. Both were isolated with controlled live tests (holding everything constant,
varying one thing):

### Wall 1 — it enforces `x-client-transaction-id`, and validates its *content*

| Test | `x-client-transaction-id` sent | Result |
|---|---|---|
| control | none | **404** (empty body — obscures the reason) |
| captured token, ~2 h old | real (from a HAR) | **200 + data** |
| random 94-char base64 | well-formed but fake | **404** |
| empty / 1-char | malformed | **404** |

So x.com validates the **content** of the token (a random one fails), but **not its
freshness** — a token captured ~2 hours earlier still worked. The token is computed by
obfuscated client JS and encodes method+path (not the query), so **one captured token is
reusable across queries and over time** until it rots.

The 14 other ops return 200 with **no** token at all — enforcement is per-endpoint, not
global.

### Wall 2 — aggressive rate-limiting (axis-2, IP/volume)

With a valid token, the *first* search returns 200; a burst of follow-ups returns **404s
that recover after a pause**. This is volume-based (IP reputation), not a token problem —
the diagnostic tell from the `scrape-bypass` playbook: *a block that self-clears with
quiet is rate-based; don't blame the token.* Keep counts modest and don't hammer.

### How the client handles it

`TX_REQUIRED = {"SearchTimeline"}` in `client.py`; `auth.headers(tx=True)` injects
`X_TX_ID` (from `.env`) only for those ops. If `X_TX_ID` is missing, search raises a
clear error instead of a mystery 404. Refresh `X_TX_ID` (copy it from a real search
request's headers in DevTools) when search 404s on the **first** call — not merely under
load (that's the rate limit, wait it out).

**TODO:** a proper runtime generator for `x-client-transaction-id` (from the JS bundle +
the `loading-x-anim` SVG frames, à la iSarabjitDhiman/XClientTransaction) would remove
the recapture step if search becomes central.

## queryId rotation

Each op's `queryId` can rotate on an x.com web release; a single op starts 404-ing while
the others work. The live hashes are discoverable at runtime — `main.<hash>.js` on
`abs.twimg.com` embeds them as `queryId:"...",operationName:"..."` (measured: 157 ops in
one bundle). Refresh the stale hash in `ops.json` from there (or recapture). Note:
`SearchTimeline`'s 404 was **not** a rotation — its queryId matched the live bundle; it
was the missing token (Wall 1).

## Pending surfaces (not captured yet)

- **`Favoriters`** — who liked a tweet (`likers <id>`).
- **`Likes`** — a user's liked tweets (the profile "Likes" tab; visible only on your own
  profile). 

Both slot straight into the existing envelope + `parse_timeline_users`/`parse_timeline_tweets`
once captured — add the `queryId`/`features`/`variables` to `ops.json` and wire a method.
