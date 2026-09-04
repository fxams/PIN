import json
from pathlib import Path

from fastapi.testclient import TestClient

from pin.crypto import private_key_hex
from pin.did import new_agent_identity
from pin.identity import (
    ENV_SIGNING_KEY,
    PIN_OPERATOR_DID,
    PIN_OPERATOR_FINGERPRINT,
    IdentityError,
    init_identity,
    load_identity,
    published_operator,
    require_identity,
)
from pin.node import create_app, get_lab
from pin.technocore_client import (
    operator_announce_text,
    preview_announce,
    sign_room,
    sweep_line,
    verify_room,
)


def test_init_and_show_are_public_only(tmp_path: Path):
    dest = tmp_path / "identity.json"
    ident = init_identity(dest)
    assert dest.exists()
    assert (dest.stat().st_mode & 0o777) == 0o600
    assert ident.did.startswith("did:key:z6Mk")
    raw = dest.read_text(encoding="utf-8")
    assert "seed" in raw
    public = ident.public_dict()
    assert "seed" not in public
    assert public["did"] == ident.did
    again = load_identity(dest)
    assert again is not None
    assert again.did == ident.did
    assert require_identity(dest).did == ident.did


def test_init_refuses_overwrite(tmp_path: Path):
    dest = tmp_path / "identity.json"
    init_identity(dest)
    try:
        init_identity(dest)
        raise AssertionError("expected IdentityError")
    except IdentityError as exc:
        assert "already exists" in str(exc)


def test_env_seed_loads(monkeypatch, tmp_path: Path):
    key, did, fp = new_agent_identity()
    monkeypatch.setenv(ENV_SIGNING_KEY, private_key_hex(key))
    ident = load_identity(tmp_path / "unused.json")
    assert ident is not None
    assert ident.did == did
    assert ident.fingerprint == fp
    assert ident.source == ENV_SIGNING_KEY


def test_file_did_mismatch_rejected(tmp_path: Path):
    dest = tmp_path / "identity.json"
    init_identity(dest)
    data = json.loads(dest.read_text(encoding="utf-8"))
    data["did"] = "did:key:z6Mknottherealoperator000000000000000000000"
    dest.write_text(json.dumps(data), encoding="utf-8")
    try:
        load_identity(dest)
        raise AssertionError("expected IdentityError")
    except IdentityError as exc:
        assert "does not match" in str(exc)


def test_technocore_sign_roundtrip(tmp_path: Path):
    ident = init_identity(tmp_path / "id.json")
    nonce = "1757010000000"
    text = operator_announce_text(ident.did)
    sig = sign_room(ident, "pin-jobs", nonce, text)
    assert len(sig) == 86
    assert sig[-1] in "AQgw"
    assert verify_room(ident, "pin-jobs", nonce, text, sig)
    assert not verify_room(ident, "pin-jobs", nonce, text + "x", sig)
    preview = preview_announce(ident, base="https://technocore.chat", room="pin-jobs", nonce=nonce)
    assert "seed" not in json.dumps(preview)
    assert "say-signed" in preview["say_url"]
    assert ident.did.split(":")[-1] in preview["say_url"]
    assert sweep_line("pin1\nframe") == "pin1 frame"


def test_published_operator_and_http_lanes(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(ENV_SIGNING_KEY, raising=False)
    monkeypatch.delenv("PIN_IDENTITY_PATH", raising=False)
    pub = published_operator()
    assert pub["did"] == PIN_OPERATOR_DID
    assert pub["fingerprint"] == PIN_OPERATOR_FINGERPRINT
    get_lab.cache_clear()
    client = TestClient(create_app())
    caps = client.get("/pin/capabilities").json()
    assert caps["operator_did"] == PIN_OPERATOR_DID
    assert caps["operator_key_loaded"] is False
    card = client.get("/.well-known/agent.json").json()
    assert card["identity"]["did"] == PIN_OPERATOR_DID
    op = client.get("/operator.json").json()
    assert op["did"] == PIN_OPERATOR_DID
    assert op["note_path"] == "/kv/did-30/4d8415d5273698"


def test_capabilities_do_not_depend_on_cwd_identity(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(ENV_SIGNING_KEY, raising=False)
    monkeypatch.delenv("PIN_IDENTITY_PATH", raising=False)
    get_lab.cache_clear()
    client = TestClient(create_app())
    caps = client.get("/pin/capabilities").json()
    assert caps["operator_did"] == PIN_OPERATOR_DID
    assert caps["operator_key_loaded"] is False
    assert "seed" not in json.dumps(caps)


def test_cli_show_and_announce_never_print_seed(tmp_path: Path):
    from typer.testing import CliRunner

    from pin.cli import app

    dest = tmp_path / "identity.json"
    init_identity(dest)
    runner = CliRunner()
    shown = runner.invoke(app, ["identity", "show", "--path", str(dest)])
    assert shown.exit_code == 0
    assert "seed" not in shown.stdout
    assert PIN_OPERATOR_DID in shown.stdout
    announced = runner.invoke(app, ["identity", "announce", "--path", str(dest)])
    assert announced.exit_code == 0
    assert "seed" not in announced.stdout
    assert "say-signed" in announced.stdout
    refused = runner.invoke(app, ["identity", "init", "--path", str(dest)])
    assert refused.exit_code == 2
