"""
MAM IRC announce parser.

Turns one raw announce line from MouseBot in `#announce` on
`irc.myanonamouse.net` into a fully populated `Announce` object that the
filter can evaluate.

The regex is the exact one Autobrr uses in its `myanonamouse.yaml`
indexer definition. Lifted verbatim with no modifications because it's
been battle-tested against years of real MAM IRC traffic — divergence
would be a footgun. The capture groups are:

    1. title              "The Demon King"
    2. author_blob        "Peter V Brett"
    3. category           "Audiobooks - Fantasy"
    4. size               "921.91 MiB"
    5. filetype           "m4b"
    6. language           "English"
    7. base_url           "https://www.myanonamouse.net/"
    8. torrent_id         "1233592"
    9. vip                "VIP" or None

Real format observed in production (from autobrr.log fixtures):

    New Torrent: The Demon King By: Peter V Brett Category: ( Audiobooks - Fantasy ) Size: ( 921.91 MiB ) Filetype: ( m4b ) Language: ( English ) Link: ( https://www.myanonamouse.net/t/1233592 ) VIP

This module is intentionally pure — no I/O, no logging, no state.
Database persistence and side effects are the caller's job.
"""
from __future__ import annotations

import re
from typing import Optional

from app.filter.gate import Announce


# The Autobrr MAM regex, ported verbatim. See module docstring for the
# capture-group breakdown. The leading `New Torrent: ` literal is what
# distinguishes a real announce from any other PRIVMSG MouseBot might
# emit (status messages, errors, etc.) — anything that doesn't start
# with that prefix is silently ignored.
_ANNOUNCE_RX = re.compile(
    r"New Torrent: (.*) By: (.*) Category: \( (.*) \) "
    r"Size: \( (.*) \) Filetype: \( (.*) \) Language: \( (.*) \) "
    r"Link: \( (https?://[^/]+/).*?(\d+)\s*\)\s*(VIP)?"
)

# MAM truncates the author list when there are too many co-authors,
# appending "and N more" (or just ", N more"). Without stripping this
# the splitter would happily produce a phantom author named "1 more"
# and that author would never match anything in the allow/ignore lists.
# Real example from autobrr.log:
#   "Stephen Marlowe, John Roeburt, Ed Lacy, and 1 more"
# Strips trailing ", and N more" / ", N more" / "and N more".
_AND_N_MORE_RX = re.compile(
    r"\s*,?\s*(?:and\s+)?\d+\s+more\s*$",
    re.IGNORECASE,
)


def _strip_and_n_more(blob: str) -> str:
    """Remove a trailing 'and N more' truncation marker from an author blob."""
    cleaned = _AND_N_MORE_RX.sub("", blob).rstrip().rstrip(",").rstrip()
    return cleaned


def parse_announce(line: str) -> Optional[Announce]:
    """Parse one IRC announce line into an `Announce`, or None if it doesn't match.

    The MAM IRC channel emits a steady stream of `New Torrent:` PRIVMSGs
    plus the occasional unrelated bot message. Anything that doesn't
    match the announce regex returns None — the caller treats None as
    "ignore this line, not for us."

    No exceptions are raised for malformed input. The contract is
    Optional[Announce], not "raises on bad input" — the IRC listener
    runs in a tight loop and exception handling per line would just be
    silently absorbed `try/except` boilerplate at the call site.
    """
    if not line:
        return None

    m = _ANNOUNCE_RX.search(line)
    if not m:
        return None

    title = m.group(1).strip()
    raw_author = m.group(2).strip()
    category = m.group(3).strip()
    size = m.group(4).strip()
    filetype = m.group(5).strip()
    language = m.group(6).strip()
    base_url = m.group(7)
    torrent_id = m.group(8)
    vip = bool(m.group(9))

    author_blob = _strip_and_n_more(raw_author)

    # Reconstruct the canonical info URL from the captured base + ID.
    # MAM's torrent landing page URL is `<base>t/<id>`. Building it from
    # captures rather than copying the raw match guarantees the URL we
    # store is well-formed even if MAM ever changes the path slightly.
    info_url = f"{base_url}t/{torrent_id}"

    return Announce(
        torrent_id=torrent_id,
        torrent_name=title,
        category=category,
        author_blob=author_blob,
        title=title,
        info_url=info_url,
        size=size,
        filetype=filetype,
        language=language,
        vip=vip,
    )


def build_download_url(torrent_id: str) -> str:
    """Construct the .torrent file download URL for a given MAM torrent ID.

    Used by the grab path. Kept here next to the parser because the
    URL shape is part of the same MAM-specific API surface, and the
    caller already has the parsed `torrent_id` field handy.

    The URL is the same one Autobrr uses (confirmed from its
    `myanonamouse.yaml`) — `/tor/download.php?tid=<id>`. Authentication
    is via the `mam_id` cookie attached as an HTTP header at fetch time;
    the URL itself carries no token.
    """
    return f"https://www.myanonamouse.net/tor/download.php?tid={torrent_id}"
