from __future__ import annotations

from typing import Any


class FakeImap:
    def __init__(self) -> None:
        self.commands: list[tuple[Any, ...]] = []
        self.copy_ok = True

    def create(self, folder: str) -> None:
        self.commands.append(("create", folder))

    def uid(self, cmd: str, *args: Any) -> tuple[str, list[bytes] | None]:
        self.commands.append(("uid", cmd, *args))
        if cmd == "COPY":
            return ("OK" if self.copy_ok else "NO", None)
        return ("OK", None)

    def expunge(self) -> None:
        self.commands.append(("expunge",))


def test_dispose_delete() -> None:
    from app.imap_worker import dispose_uid

    client = FakeImap()
    assert dispose_uid(client, "1", mode="delete") == "delete"
    assert ("uid", "STORE", "1", "+FLAGS", "(\\Deleted)") in client.commands
    assert ("expunge",) in client.commands


def test_dispose_seen() -> None:
    from app.imap_worker import dispose_uid

    client = FakeImap()
    assert dispose_uid(client, "2", mode="seen") == "seen"
    assert ("uid", "STORE", "2", "+FLAGS", "(\\Seen)") in client.commands
    assert ("expunge",) not in client.commands


def test_dispose_move_falls_back_to_delete() -> None:
    from app.imap_worker import dispose_uid

    client = FakeImap()
    client.copy_ok = False
    assert dispose_uid(client, "3", mode="move", folder="Processed") == "delete"
    assert ("uid", "STORE", "3", "+FLAGS", "(\\Deleted)") in client.commands
