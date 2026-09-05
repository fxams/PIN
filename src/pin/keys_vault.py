"""Copy PIN seeds to a directory that is not git and never print them.

Default vault is ``~/.pin-safe`` (0700). Workspace ``.pin/`` stays the working
copy and stays gitignored. Inventory JSON lists DIDs only.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pin.identity import IdentityError, default_identity_path, load_identity_file
from pin.roster import default_roster_dir, load_roster

ENV_SAFE_DIR = "PIN_SAFE_DIR"
INVENTORY_NAME = "inventory.json"
README_NAME = "README.txt"
README_TEXT = (
    "PIN seed vault. Mode 0700 / files 0600.\n"
    "Do not commit, upload, or paste these files.\n"
    "Working copy is .pin/ in the repo (gitignored).\n"
    "Restore with: pin keys restore --src <this-dir>\n"
    "This README and inventory.json contain no seeds.\n"
)


def default_safe_dir() -> Path:
    env = os.environ.get(ENV_SAFE_DIR)
    if env:
        return Path(env)
    return Path.home() / ".pin-safe"


def _chmod(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def _copy_secret_file(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    _chmod(dest.parent, 0o700)
    shutil.copy2(src, dest)
    _chmod(dest, 0o600)


def dest_is_committable(dest: Path, *, repo: Path | None = None) -> bool:
    """True when dest sits in a git work tree and is not ignored."""
    root = repo or Path.cwd()
    try:
        resolved = dest.resolve()
        resolved.relative_to(root.resolve())
    except ValueError:
        return False
    try:
        check = subprocess.run(
            ["git", "-C", str(root), "check-ignore", "-q", str(resolved)],
            check=False,
            capture_output=True,
        )
    except OSError:
        return False
    if check.returncode == 0:
        return False
    if check.returncode == 128:
        return False
    return True


@dataclass
class VaultReport:
    dest: str
    operator: bool = False
    operator_did: str | None = None
    operator_fingerprint: str | None = None
    n_roster: int = 0
    n_buyers: int = 0
    n_sellers: int = 0
    verified: int = 0
    seed: bool = False
    failed: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "dest": self.dest,
            "operator": self.operator,
            "operator_did": self.operator_did,
            "operator_fingerprint": self.operator_fingerprint,
            "n_roster": self.n_roster,
            "n_buyers": self.n_buyers,
            "n_sellers": self.n_sellers,
            "verified": self.verified,
            "seed": False,
            "failed": self.failed,
        }


def _inventory_body(report: VaultReport, agents: list[Any]) -> str:
    payload = {
        "v": "pin/1",
        "kind": "seed-vault",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "operator_did": report.operator_did,
        "operator_fingerprint": report.operator_fingerprint,
        "n_roster": report.n_roster,
        "n_buyers": report.n_buyers,
        "n_sellers": report.n_sellers,
        "agents": [
            {"did": a.did, "fingerprint": a.fingerprint, "role": a.role} for a in agents
        ],
        "seed": False,
    }
    return json.dumps(payload, indent=2) + "\n"


def backup_keys(
    dest: Path | None = None,
    *,
    identity_path: Path | None = None,
    roster_dir: Path | None = None,
    repo: Path | None = None,
) -> VaultReport:
    dest = dest or default_safe_dir()
    if dest_is_committable(dest, repo=repo):
        raise IdentityError(
            f"refusing to write seeds at {dest}: path is inside the git work tree and not ignored"
        )
    dest.mkdir(parents=True, exist_ok=True)
    _chmod(dest, 0o700)
    report = VaultReport(dest=str(dest))
    ident_src = identity_path or default_identity_path()
    roster_src = roster_dir or default_roster_dir()
    agents = []
    if ident_src.exists():
        ident = load_identity_file(ident_src)
        _copy_secret_file(ident_src, dest / "identity.json")
        report.operator = True
        report.operator_did = ident.did
        report.operator_fingerprint = ident.fingerprint
        report.verified += 1
    if roster_src.is_dir():
        agents = load_roster(roster_src)
        for agent in agents:
            src = roster_src / "keys" / f"{agent.fingerprint}.json"
            if not src.exists():
                report.failed.append(f"missing:{agent.fingerprint}")
                continue
            _copy_secret_file(src, dest / "roster" / "keys" / src.name)
            report.verified += 1
        for name in ("roles.json", "manifest.json"):
            extra = roster_src / name
            if extra.exists():
                _copy_secret_file(extra, dest / "roster" / name)
        report.n_roster = len(agents)
        report.n_buyers = sum(1 for a in agents if a.role == "buyer")
        report.n_sellers = sum(1 for a in agents if a.role == "seller")
    (dest / README_NAME).write_text(README_TEXT, encoding="utf-8")
    _chmod(dest / README_NAME, 0o600)
    inventory = dest / INVENTORY_NAME
    inventory.write_text(_inventory_body(report, agents), encoding="utf-8")
    _chmod(inventory, 0o600)
    return report


def verify_keys(
    dest: Path | None = None,
    *,
    identity_path: Path | None = None,
    roster_dir: Path | None = None,
) -> VaultReport:
    """Confirm every seed file re-derives its stored DID. Never returns a seed."""
    dest = dest or default_safe_dir()
    report = VaultReport(dest=str(dest))
    if (dest / "identity.json").exists():
        ident_file = dest / "identity.json"
    else:
        ident_file = identity_path or default_identity_path()
    if (dest / "roster" / "keys").is_dir():
        roster_root = dest / "roster"
    else:
        roster_root = roster_dir or default_roster_dir()
    if ident_file.exists():
        ident = load_identity_file(ident_file)
        report.operator = True
        report.operator_did = ident.did
        report.operator_fingerprint = ident.fingerprint
        report.verified += 1
        mode = ident_file.stat().st_mode & 0o777
        if mode != 0o600:
            report.failed.append(f"mode:identity:{oct(mode)}")
    if roster_root.is_dir():
        agents = load_roster(roster_root)
        report.n_roster = len(agents)
        report.n_buyers = sum(1 for a in agents if a.role == "buyer")
        report.n_sellers = sum(1 for a in agents if a.role == "seller")
        for agent in agents:
            path = roster_root / "keys" / f"{agent.fingerprint}.json"
            try:
                loaded = load_identity_file(path)
            except (IdentityError, OSError) as exc:
                report.failed.append(f"bad:{agent.fingerprint}:{exc.__class__.__name__}")
                continue
            if loaded.did != agent.did or loaded.fingerprint != agent.fingerprint:
                report.failed.append(f"mismatch:{agent.fingerprint}")
                continue
            mode = path.stat().st_mode & 0o777
            if mode != 0o600:
                report.failed.append(f"mode:{agent.fingerprint}:{oct(mode)}")
            report.verified += 1
    if not report.operator and report.n_roster == 0:
        report.failed.append("empty")
    return report


def restore_keys(
    src: Path,
    *,
    identity_path: Path | None = None,
    roster_dir: Path | None = None,
    overwrite: bool = False,
) -> VaultReport:
    """Copy a vault back to the working .pin paths. Refuses to clobber unless overwrite."""
    if not src.is_dir():
        raise IdentityError(f"vault not found: {src}")
    ident_dest = identity_path or default_identity_path()
    roster_dest = roster_dir or default_roster_dir()
    src_ident = src / "identity.json"
    if src_ident.exists():
        if ident_dest.exists() and not overwrite:
            raise IdentityError(f"identity already exists at {ident_dest}")
        _copy_secret_file(src_ident, ident_dest)
    src_roster = src / "roster"
    if src_roster.is_dir():
        if roster_dest.exists() and any(roster_dest.joinpath("keys").glob("*.json")) and not overwrite:
            raise IdentityError(f"roster already exists at {roster_dest}")
        for path in (src_roster / "keys").glob("*.json"):
            _copy_secret_file(path, roster_dest / "keys" / path.name)
        for name in ("roles.json", "manifest.json"):
            extra = src_roster / name
            if extra.exists():
                _copy_secret_file(extra, roster_dest / name)
    return verify_keys(src)
