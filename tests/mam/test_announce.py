"""
Unit tests for the MAM announce parser.

Two layers of testing:

  1. **Hand-written cases** — pin down specific behaviors (the
     "and N more" stripping, the typographic apostrophe, optional VIP
     suffix, malformed input rejection). These are the regression tests
     that catch deliberate behavior changes.

  2. **Real fixture sweep** — every line in
     `tests/fixtures/real_announces.txt` (18 captures from the user's
     production Autobrr log) MUST parse cleanly. This is the safety net
     that catches divergence between Autobrr's regex (which we cribbed
     verbatim) and what MAM actually emits today. If MAM changes the
     announce format, this sweep is the alarm.
"""
from pathlib import Path

from app.filter.gate import Announce
from app.mam.announce import (
    _strip_and_n_more,
    build_download_url,
    parse_announce,
)


_FIXTURES_PATH = Path(__file__).parent.parent / "fixtures" / "real_announces.txt"


def _load_real_announces() -> list[str]:
    return [
        line.rstrip("\n")
        for line in _FIXTURES_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# ─── Real-fixture sweep ──────────────────────────────────────


class TestRealFixturesParse:
    """Every captured production announce must parse cleanly."""

    def test_all_fixtures_parse(self):
        announces = _load_real_announces()
        assert len(announces) >= 18, "Fixture file shrunk unexpectedly"
        for line in announces:
            result = parse_announce(line)
            assert result is not None, f"Failed to parse: {line!r}"
            assert isinstance(result, Announce)
            assert result.torrent_id, f"Missing torrent_id in: {line!r}"
            assert result.torrent_name, f"Missing torrent_name in: {line!r}"
            assert result.category, f"Missing category in: {line!r}"
            assert result.author_blob, f"Missing author_blob in: {line!r}"

    def test_fixture_torrent_ids_are_unique_and_numeric(self):
        announces = _load_real_announces()
        ids = []
        for line in announces:
            result = parse_announce(line)
            assert result is not None
            assert result.torrent_id.isdigit()
            ids.append(result.torrent_id)
        assert len(set(ids)) == len(ids), "Fixture file has duplicate torrent IDs"

    def test_fixture_info_urls_are_well_formed(self):
        announces = _load_real_announces()
        for line in announces:
            result = parse_announce(line)
            assert result is not None
            assert result.info_url.startswith("https://www.myanonamouse.net/")
            assert result.info_url.endswith(f"/t/{result.torrent_id}")


# ─── Hand-written cases — specific behaviors ─────────────────


class TestParseAnnounce:
    def test_basic_single_author_with_vip(self):
        line = (
            "New Torrent: The Demon King By: Peter V Brett "
            "Category: ( Audiobooks - Fantasy ) Size: ( 921.91 MiB ) "
            "Filetype: ( m4b ) Language: ( English ) "
            "Link: ( https://www.myanonamouse.net/t/1233592 ) VIP"
        )
        result = parse_announce(line)
        assert result is not None
        assert result.torrent_id == "1233592"
        assert result.torrent_name == "The Demon King"
        assert result.title == "The Demon King"
        assert result.author_blob == "Peter V Brett"
        assert result.category == "Audiobooks - Fantasy"
        assert result.size == "921.91 MiB"
        assert result.filetype == "m4b"
        assert result.language == "English"
        assert result.vip is True
        assert result.info_url == "https://www.myanonamouse.net/t/1233592"

    def test_basic_without_vip(self):
        line = (
            "New Torrent: The Path of Ascension 11 By: C Mantis "
            "Category: ( Audiobooks - Fantasy ) Size: ( 761.20 MiB ) "
            "Filetype: ( m4b ) Language: ( English ) "
            "Link: ( https://www.myanonamouse.net/t/1233620 )"
        )
        result = parse_announce(line)
        assert result is not None
        assert result.vip is False
        assert result.torrent_id == "1233620"

    def test_and_n_more_stripped(self):
        # Real-world MAM truncation when there are too many co-authors.
        line = (
            "New Torrent: The Hardboiled Mystery MEGAPACK "
            "By: Stephen Marlowe, John Roeburt, Ed Lacy, and 1 more "
            "Category: ( Ebooks - Mystery ) Size: ( 743.58 KiB ) "
            "Filetype: ( epub ) Language: ( English ) "
            "Link: ( https://www.myanonamouse.net/t/1233596 ) VIP"
        )
        result = parse_announce(line)
        assert result is not None
        # The "and 1 more" suffix must be removed so the splitter
        # produces 3 authors, not 4 with a phantom "1 more".
        assert "more" not in result.author_blob.lower()
        assert result.author_blob == "Stephen Marlowe, John Roeburt, Ed Lacy"

    def test_typographic_apostrophe_in_title_preserved(self):
        # The parser preserves the title verbatim — apostrophe
        # normalization happens in the filter layer when comparing
        # against author lists, not in the parser.
        line = (
            "New Torrent: I Won\u2019t Let Mistress Suck My Blood, Vol. 1 "
            "By: Paderapollonorio Category: ( Ebooks - Comics/Graphic novels ) "
            "Size: ( 62.93 MiB ) Filetype: ( cbz ) Language: ( English ) "
            "Link: ( https://www.myanonamouse.net/t/1233619 ) VIP"
        )
        result = parse_announce(line)
        assert result is not None
        assert "\u2019" in result.title

    def test_title_with_colon(self):
        # "Classroom of the Elite: Year 2, Vol. 12.5" — the colon in the
        # title shouldn't confuse the regex (it's a real fixture).
        line = (
            "New Torrent: Classroom of the Elite: Year 2, Vol. 12.5 "
            "By: Syougo Kinugasa Category: ( Audiobooks - Young Adult ) "
            "Size: ( 472.16 MiB ) Filetype: ( m4b ) Language: ( English ) "
            "Link: ( https://www.myanonamouse.net/t/1233608 ) VIP"
        )
        result = parse_announce(line)
        assert result is not None
        assert result.title == "Classroom of the Elite: Year 2, Vol. 12.5"
        assert result.author_blob == "Syougo Kinugasa"

    def test_title_with_comma(self):
        # "Sea of Wind, Shore of the Labyrinth" — comma in title
        line = (
            "New Torrent: Sea of Wind, Shore of the Labyrinth "
            "By: Fuyumi Ono Category: ( Audiobooks - Fantasy ) "
            "Size: ( 401.33 MiB ) Filetype: ( m4b ) Language: ( English ) "
            "Link: ( https://www.myanonamouse.net/t/1233605 ) VIP"
        )
        result = parse_announce(line)
        assert result is not None
        assert result.title == "Sea of Wind, Shore of the Labyrinth"

    def test_category_with_slash(self):
        # "Ebooks - Action/Adventure", "Ebooks - Crime/Thriller" — slashes
        # in the category are common and shouldn't be eaten by the regex.
        line = (
            "New Torrent: God's Eye By: Robert Rapoza "
            "Category: ( Ebooks - Action/Adventure ) Size: ( 1.49 MiB ) "
            "Filetype: ( epub ) Language: ( English ) "
            "Link: ( https://www.myanonamouse.net/t/1233601 ) VIP"
        )
        result = parse_announce(line)
        assert result is not None
        assert result.category == "Ebooks - Action/Adventure"

    def test_returns_none_on_unrelated_line(self):
        # The IRC channel emits other PRIVMSGs (status, errors, etc).
        # Anything that doesn't match returns None — never raises.
        assert parse_announce("MouseBot: server restart in 5 minutes") is None
        assert parse_announce("") is None
        assert parse_announce("just some random text") is None

    def test_returns_none_on_partial_match(self):
        # Truncated / malformed announce — must NOT half-fill an Announce.
        line = "New Torrent: The Demon King By: Peter V Brett Category: ("
        assert parse_announce(line) is None


# ─── _strip_and_n_more directly ──────────────────────────────


class TestStripAndNMore:
    def test_no_marker(self):
        assert _strip_and_n_more("A, B, C") == "A, B, C"

    def test_and_n_more(self):
        assert (
            _strip_and_n_more("Stephen Marlowe, John Roeburt, Ed Lacy, and 1 more")
            == "Stephen Marlowe, John Roeburt, Ed Lacy"
        )

    def test_and_2_more(self):
        assert (
            _strip_and_n_more("Author A, Author B, and 2 more")
            == "Author A, Author B"
        )

    def test_n_more_no_and(self):
        # Defensive — handle ", 3 more" without the "and" connector too.
        assert _strip_and_n_more("Author A, Author B, 3 more") == "Author A, Author B"

    def test_case_insensitive(self):
        assert _strip_and_n_more("Author A, AND 5 MORE") == "Author A"


# ─── build_download_url ──────────────────────────────────────


class TestBuildDownloadUrl:
    def test_format(self):
        assert (
            build_download_url("1233592")
            == "https://www.myanonamouse.net/tor/download.php?tid=1233592"
        )

    def test_with_announce_roundtrip(self):
        # The torrent_id captured from a real announce should produce
        # a valid download URL when passed back to build_download_url.
        line = (
            "New Torrent: The Demon King By: Peter V Brett "
            "Category: ( Audiobooks - Fantasy ) Size: ( 921.91 MiB ) "
            "Filetype: ( m4b ) Language: ( English ) "
            "Link: ( https://www.myanonamouse.net/t/1233592 ) VIP"
        )
        result = parse_announce(line)
        assert result is not None
        url = build_download_url(result.torrent_id)
        assert url == "https://www.myanonamouse.net/tor/download.php?tid=1233592"
