"""Plain data records returned by the client. Kept flat so callers (a monitor, a
CSV export, a DB row) don't have to know about x.com's nested GraphQL shapes."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class XUser:
    rest_id: str            # numeric user id (the stable key; screen_names change)
    screen_name: str = ""   # the @handle
    name: str = ""          # display name
    description: str = ""    # bio
    followers_count: int = 0
    friends_count: int = 0   # "following" count
    statuses_count: int = 0
    verified: bool = False
    created_at: str = ""
    location: str = ""
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def url(self) -> str:
        return f"https://x.com/{self.screen_name}" if self.screen_name else ""


@dataclass
class Tweet:
    rest_id: str
    user_screen_name: str = ""
    user_name: str = ""
    created_at: str = ""
    text: str = ""
    lang: str = ""
    reply_count: int = 0
    retweet_count: int = 0
    like_count: int = 0
    quote_count: int = 0
    view_count: int = 0
    is_retweet: bool = False
    is_quote: bool = False
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def url(self) -> str:
        if self.user_screen_name and self.rest_id:
            return f"https://x.com/{self.user_screen_name}/status/{self.rest_id}"
        return f"https://x.com/i/status/{self.rest_id}" if self.rest_id else ""
