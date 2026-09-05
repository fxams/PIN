import json

from pin.identity import init_identity
from pin.roster import (
    ROSTER_NOTE_PATH,
    did_note_parts,
    init_roster,
    load_roster,
    preview_roster,
    publish_roster,
    roster_line,
    roster_note_body,
)


def test_init_roster_public_only(tmp_path):
    root = tmp_path / "roster"
    first = init_roster(root, count=3)
    assert len(first) == 3
    assert len({ident.did for ident in first}) == 3
    again = init_roster(root, count=3)
    assert {ident.did for ident in again} == {ident.did for ident in first}
    grown = init_roster(root, count=5)
    assert len(grown) == 5
    loaded = load_roster(root)
    assert len(loaded) == 5
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert "seed" not in json.dumps(manifest)
    for ident in loaded:
        assert (root / "keys" / f"{ident.fingerprint}.json").exists()


def test_roster_lines_are_unique():
    from pin.crypto import generate_miner_key, private_key_hex
    from pin.identity import identity_from_seed

    idents = [identity_from_seed(private_key_hex(generate_miner_key()), source="t") for _ in range(8)]
    lines = [roster_line(ident, index=i + 1, total=8) for i, ident in enumerate(idents)]
    assert len(set(lines)) == 8
    assert all(ident.did in lines[i] for i, ident in enumerate(idents))
    assert all("job.proto=pin" in line for line in lines)


def test_roster_note_fits_100_dids():
    class _I:
        def __init__(self, i: int) -> None:
            self.did = f"did:key:z6Mk{'A' * 43}{i:01d}"
            self.fingerprint = f"{i:016x}"

    text = roster_note_body([_I(i) for i in range(100)])  # type: ignore[arg-type]
    assert len(text) <= 8192
    body = json.loads(text)
    assert body["n"] == 100
    assert body["spec"] == "/kv/pin/llms"
    assert len(body["dids"]) == 100


def test_did_note_parts_match_operator_convention():
    assert did_note_parts("304d8415d5273698") == ("did-30", "4d8415d5273698")


def test_preview_and_cli_hide_seeds(tmp_path):
    from typer.testing import CliRunner

    from pin.cli import app

    op = init_identity(tmp_path / "op.json")
    root = tmp_path / "roster"
    init_roster(root, count=2)
    preview = preview_roster(load_roster(root), op, posts=2)
    assert preview["n"] == 2
    assert preview["roster_path"] == ROSTER_NOTE_PATH
    assert "seed" not in json.dumps(preview)
    runner = CliRunner()
    shown = runner.invoke(app, ["roster", "show", "--roster-dir", str(root)])
    assert shown.exit_code == 0
    assert "seed" not in shown.stdout
    pub = runner.invoke(
        app,
        ["roster", "publish", "--roster-dir", str(root), "--path", str(tmp_path / "op.json")],
    )
    assert pub.exit_code == 0
    assert "seed" not in pub.stdout
    assert "job.proto=pin" in pub.stdout


def test_publish_roster_posts_pin_room_only(tmp_path, monkeypatch):
    op = init_identity(tmp_path / "op.json")
    root = tmp_path / "roster"
    idents = init_roster(root, count=2)
    urls: list[str] = []

    class _Resp:
        def __init__(self, status: int = 200) -> None:
            self.status_code = status
            self.text = "ok"

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def request(self, method: str, url: str, json=None):
            urls.append(url)
            return _Resp()

    monkeypatch.setattr("pin.roster.httpx.Client", _Client)
    result = publish_roster(idents, op, posts=2, base="https://technocore.chat")
    assert result.rooms_ok == 2
    assert result.notes_ok == 2
    assert result.operator_status == 200
    assert result.roster_status == 200
    assert any(u.endswith("/kv/pin/roster") for u in urls)
    assert any(u.endswith("/r/pin") for u in urls)
    assert not any("/r/kibble" in u or "/r/lobby" in u or "/r/tclk-offers" in u for u in urls)
    assert "seed" not in str(urls)
