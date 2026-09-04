"""tclk paper rail — same note shape as @flop-labs/tclk, holds no value.

Line: `tclkpaper1 {status} {lock} {statement} {refundAfterMs} [secret]`
Note: ns=`tclk-paper-{contract[2:4]}` key=`{contract[4:18]}`
`lock` on the note is the lock kind (`hash` | `point`).
`lock.ref` on the tclk/1 lock frame is the contract id.
"""

from __future__ import annotations

from dataclasses import dataclass

from pin.tclk_frames import HEX32_RE, HEX33_RE, TclkError, verify_secret

PAPER_PREFIX = "tclkpaper1"
PAPER_STATUSES = frozenset({"locked", "claimed", "refunded"})
PAPER_LOCKS = frozenset({"hash", "point"})


@dataclass
class PaperNote:
    status: str
    lock: str
    statement: str
    refund_after_ms: int
    secret: str | None = None

    def line(self) -> str:
        parts = [PAPER_PREFIX, self.status, self.lock, self.statement, str(self.refund_after_ms)]
        if self.secret:
            parts.append(self.secret)
        return " ".join(parts)


def paper_note_path(contract: str) -> tuple[str, str]:
    if not HEX32_RE.match(contract):
        raise TclkError("paper note needs a contract id")
    return f"tclk-paper-{contract[2:4]}", contract[4:18]


def encode_paper_note(note: PaperNote) -> str:
    if note.status not in PAPER_STATUSES:
        raise TclkError("unknown paper status")
    if note.lock not in PAPER_LOCKS:
        raise TclkError("paper lock must be hash or point")
    if (note.status == "claimed") != bool(note.secret):
        raise TclkError("claimed paper note needs the secret, and only then")
    return note.line()


def decode_paper_note(line: str) -> PaperNote | None:
    parts = line.split(" ")
    if len(parts) < 5 or len(parts) > 6 or parts[0] != PAPER_PREFIX:
        return None
    status, lock, statement, refund_raw = parts[1], parts[2], parts[3], parts[4]
    secret = parts[5] if len(parts) > 5 else None
    if status not in PAPER_STATUSES or lock not in PAPER_LOCKS:
        return None
    if not (HEX32_RE.match(statement) or HEX33_RE.match(statement)):
        return None
    try:
        refund_after_ms = int(refund_raw)
    except ValueError:
        return None
    if refund_after_ms <= 0:
        return None
    if (status == "claimed") != bool(secret):
        return None
    if secret is not None and not HEX32_RE.match(secret):
        return None
    return PaperNote(
        status=status,
        lock=lock,
        statement=statement,
        refund_after_ms=refund_after_ms,
        secret=secret,
    )


class PaperStore:
    """In-memory paper notes. Same CAS rules as the tclk paper rail."""

    def __init__(self) -> None:
        self.notes: dict[tuple[str, str], str] = {}

    def raw(self, contract: str) -> str | None:
        return self.notes.get(paper_note_path(contract))

    def lock(
        self, contract: str, statement: str, refund_after_ms: int, *, now_ms: int, lock: str = "hash"
    ) -> PaperNote:
        if now_ms >= refund_after_ms:
            raise TclkError("refusing to lock into an already-open refund window")
        path = paper_note_path(contract)
        if path in self.notes:
            raise TclkError("paper lock already present")
        note = PaperNote(status="locked", lock=lock, statement=statement, refund_after_ms=refund_after_ms)
        self.notes[path] = encode_paper_note(note)
        return note

    def claim(self, contract: str, secret: str, now_ms: int) -> PaperNote:
        path = paper_note_path(contract)
        current = self.notes.get(path)
        if current is None:
            raise TclkError("paper note is not locked")
        note = decode_paper_note(current)
        if note is None or note.status != "locked":
            raise TclkError("paper note is not locked")
        if now_ms >= note.refund_after_ms:
            raise TclkError("paper lock expired")
        if not verify_secret(note.lock, note.statement, secret):
            raise TclkError("secret does not open the statement")
        claimed = PaperNote(
            status="claimed",
            lock=note.lock,
            statement=note.statement,
            refund_after_ms=note.refund_after_ms,
            secret=secret,
        )
        self.notes[path] = encode_paper_note(claimed)
        return claimed

    def refund(self, contract: str, now_ms: int) -> PaperNote:
        path = paper_note_path(contract)
        current = self.notes.get(path)
        if current is None:
            raise TclkError("paper note is not locked")
        note = decode_paper_note(current)
        if note is None or note.status != "locked":
            raise TclkError("paper note is not locked")
        if now_ms < note.refund_after_ms:
            raise TclkError("paper refund is early")
        refunded = PaperNote(
            status="refunded",
            lock=note.lock,
            statement=note.statement,
            refund_after_ms=note.refund_after_ms,
        )
        self.notes[path] = encode_paper_note(refunded)
        return refunded
