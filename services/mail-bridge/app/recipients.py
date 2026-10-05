from __future__ import annotations

import re
from email.utils import getaddresses

# bookbridge+local@domain.com  (local: lowercase alnum)
_ALIAS_RE = re.compile(
    r"^(?P<prefix>[^+@\s]+)\+(?P<local>[a-z0-9]+)@(?P<domain>[^@\s]+)$",
    re.IGNORECASE,
)


def addresses_from_headers(headers: dict[str, str | None]) -> list[str]:
    """Extract email addresses from common recipient headers, in priority order."""
    ordered_keys = ("Delivered-To", "X-Original-To", "To", "Cc")
    found: list[str] = []
    seen: set[str] = set()
    for key in ordered_keys:
        raw = headers.get(key)
        if not raw:
            continue
        for _, addr in getaddresses([raw]):
            addr = addr.strip()
            if not addr:
                continue
            key_l = addr.lower()
            if key_l in seen:
                continue
            seen.add(key_l)
            found.append(addr)
    return found


def match_plus_alias(
    address: str,
    *,
    prefix: str,
    domain: str,
) -> str | None:
    """Return mail_local if address matches prefix+local@domain, else None."""
    m = _ALIAS_RE.match(address.strip())
    if not m:
        return None
    if m.group("prefix").lower() != prefix.lower():
        return None
    if m.group("domain").lower() != domain.lower():
        return None
    return m.group("local").lower()


def resolve_user_mail_local(
    headers: dict[str, str | None],
    *,
    prefix: str,
    domain: str,
) -> str | None:
    for addr in addresses_from_headers(headers):
        local = match_plus_alias(addr, prefix=prefix, domain=domain)
        if local:
            return local
    return None
