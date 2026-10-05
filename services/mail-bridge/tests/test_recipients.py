from __future__ import annotations

from app.recipients import addresses_from_headers, match_plus_alias, resolve_user_mail_local


def test_addresses_priority_and_dedupe() -> None:
    addrs = addresses_from_headers(
        {
            "To": "bookbridge+to@hoerdle.de",
            "Delivered-To": "bookbridge+delivered@hoerdle.de",
            "Cc": "bookbridge+to@hoerdle.de, other@example.com",
        }
    )
    assert addrs[0].lower().startswith("bookbridge+delivered@")
    assert "other@example.com" in [a.lower() for a in addrs]


def test_match_case_insensitive_domain() -> None:
    assert match_plus_alias("BookBridge+AbC9@Hoerdle.DE", prefix="bookbridge", domain="hoerdle.de") == "abc9"
