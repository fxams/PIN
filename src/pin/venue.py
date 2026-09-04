"""In-lab Technocore-shaped venue.

Agents meet here the same way they meet on technocore.chat: rooms for
conversation, KV notes for durable JobSpecs. This is not the live venue —
tests and the local sidecar must not depend on technocore.chat room caps.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class RoomRecord:
    seq: int
    ts_ms: int
    nick: str
    text: str
    signed: bool
    did: str | None = None


@dataclass
class Venue:
    rooms: dict[str, list[RoomRecord]] = field(default_factory=dict)
    notes: dict[str, str] = field(default_factory=dict)

    def say(self, room: str, nick: str, text: str, *, signed: bool = False, did: str | None = None) -> RoomRecord:
        text = " ".join(text.split())  # Technocore single-line sweep (controls → space)
        bucket = self.rooms.setdefault(room, [])
        rec = RoomRecord(
            seq=len(bucket) + 1,
            ts_ms=time.time_ns() // 1_000_000,
            nick=nick,
            text=text,
            signed=signed,
            did=did,
        )
        bucket.append(rec)
        return rec

    def read(self, room: str, *, since: int = 0) -> list[RoomRecord]:
        return [rec for rec in self.rooms.get(room, []) if rec.seq > since]

    def render_room(self, room: str, *, since: int = 0) -> str:
        lines = [f"# room={room} lab-venue (not technocore.chat)"]
        for rec in self.read(room, since=since):
            who = rec.did if rec.signed and rec.did else f"~{rec.nick}"
            lines.append(f"{rec.seq}\t{who}\t{rec.text}")
        return "\n".join(lines) + ("\n" if lines else "")

    def note_get(self, ns: str, key: str) -> str | None:
        return self.notes.get(f"{ns}/{key}")

    def note_set(self, ns: str, key: str, value: str) -> str:
        self.notes[f"{ns}/{key}"] = value
        return value
