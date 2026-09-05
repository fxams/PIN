import json
from pathlib import Path

from typer.testing import CliRunner

from pin.cli import app
from pin.identity import IdentityError, init_identity, load_identity_file
from pin.keys_vault import backup_keys, dest_is_committable, restore_keys, verify_keys
from pin.roster import init_roster


def test_backup_and_verify_never_echo_seed(tmp_path: Path):
    ident = init_identity(tmp_path / "work" / "identity.json")
    roster = tmp_path / "work" / "roster"
    agents = init_roster(roster, buyers=1, sellers=1)
    dest = tmp_path / "safe"
    report = backup_keys(
        dest,
        identity_path=tmp_path / "work" / "identity.json",
        roster_dir=roster,
        repo=tmp_path / "not-a-repo",
    )
    assert report.operator is True
    assert report.operator_did == ident.did
    assert report.n_roster == 2
    assert report.n_buyers == 1
    assert report.n_sellers == 1
    assert report.verified == 3
    assert report.seed is False
    secret = json.loads((tmp_path / "work" / "identity.json").read_text(encoding="utf-8"))["seed"]
    assert secret not in json.dumps(report.as_dict())
    inventory = json.loads((dest / "inventory.json").read_text(encoding="utf-8"))
    assert inventory["seed"] is False
    assert secret not in json.dumps(inventory)
    assert (dest.stat().st_mode & 0o777) == 0o700
    assert ((dest / "identity.json").stat().st_mode & 0o777) == 0o600
    checked = verify_keys(dest)
    assert checked.failed == []
    assert checked.verified == 3
    assert load_identity_file(dest / "identity.json").did == ident.did
    assert {a.fingerprint for a in agents} <= {
        p.stem for p in (dest / "roster" / "keys").glob("*.json")
    }


def test_backup_refuses_committable_repo_path(tmp_path: Path):
    init_identity(tmp_path / "id.json")
    leaked = Path.cwd() / "leaked-pin-seeds"
    assert dest_is_committable(leaked, repo=Path.cwd())
    try:
        backup_keys(leaked, identity_path=tmp_path / "id.json", roster_dir=tmp_path / "empty")
        raise AssertionError("expected IdentityError")
    except IdentityError as exc:
        assert "not ignored" in str(exc)
    assert not leaked.exists()


def test_restore_refuses_overwrite(tmp_path: Path):
    init_identity(tmp_path / "work" / "identity.json")
    init_roster(tmp_path / "work" / "roster", buyers=1, sellers=1)
    vault = tmp_path / "safe"
    backup_keys(
        vault,
        identity_path=tmp_path / "work" / "identity.json",
        roster_dir=tmp_path / "work" / "roster",
        repo=tmp_path,
    )
    try:
        restore_keys(
            vault,
            identity_path=tmp_path / "work" / "identity.json",
            roster_dir=tmp_path / "work" / "roster",
        )
        raise AssertionError("expected IdentityError")
    except IdentityError as exc:
        assert "already exists" in str(exc)
    other = tmp_path / "restored" / "identity.json"
    restored = restore_keys(
        vault,
        identity_path=other,
        roster_dir=tmp_path / "restored" / "roster",
    )
    assert restored.operator_did == load_identity_file(other).did
    assert restored.n_roster == 2


def test_keys_cli_hides_seeds(tmp_path: Path):
    init_identity(tmp_path / "id.json")
    init_roster(tmp_path / "roster", buyers=1, sellers=1)
    dest = tmp_path / "safe"
    runner = CliRunner()
    backed = runner.invoke(
        app,
        [
            "keys",
            "backup",
            "--dest",
            str(dest),
            "--path",
            str(tmp_path / "id.json"),
            "--roster-dir",
            str(tmp_path / "roster"),
        ],
    )
    assert backed.exit_code == 0
    secret = json.loads((tmp_path / "id.json").read_text(encoding="utf-8"))["seed"]
    assert secret not in backed.stdout
    assert secret not in dest.joinpath("inventory.json").read_text(encoding="utf-8")
    shown = runner.invoke(app, ["keys", "verify", "--dest", str(dest)])
    assert shown.exit_code == 0
    assert secret not in shown.stdout
    assert shown.stdout.count('"seed": false') == 1
    assert load_identity_file(dest / "identity.json").did.startswith("did:key:")
