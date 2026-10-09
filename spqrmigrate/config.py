import argparse
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .errors import MigrationError


@dataclass
class Config:
    base_dir: str = ""
    conn: str = "dbname=postgres user=postgres connect_timeout=1"
    schema: str | None = None
    target: int | None = None
    baseline: int = 0
    user: str | None = None
    callbacks: str | dict = ""
    session: list = field(default_factory=list)
    dryrun: bool = False
    termination_interval: float | None = None
    disable_schema_check: bool = False
    check_serial_versions: bool = False
    show_only_unapplied: bool = False
    force_mixed: bool = False
    ungroup: bool = False
    set_version_info_after_callbacks: bool = False
    nontransactional: bool = False


def parser():
    p = argparse.ArgumentParser(description="Migrate SPQR metadata through coordinator Console")
    help_text = {
        "--conn": "Coordinator Console connection string",
        "--target": "Target version: an integer or latest",
        "--baseline": "Baseline version (default: 0)",
        "--schema": "History namespace; requires --disable_schema_check",
        "--user": "Author recorded in history (default: connection user)",
        "--callbacks": "Comma-separated type:path callbacks",
        "--termination_interval": "Unsupported: PostgreSQL backend termination",
    }
    p.add_argument("cmd", choices=["info", "migrate", "baseline", "clean"])
    for short, long, kind in [
        ("-c", "--conn", str), ("-t", "--target", str), ("-b", "--baseline", int),
        ("-m", "--schema", str), ("-u", "--user", str), ("-a", "--callbacks", str),
        ("-l", "--termination_interval", float),
    ]:
        p.add_argument(short, long, type=kind, help=help_text[long])
    p.add_argument("-d", "--base_dir", default="", help="Directory containing migrations/ and migrations.yml")
    p.add_argument("-s", "--session", action="append", help="Unsupported: PostgreSQL session setup")
    for flags in [
        ("-n", "--dryrun"), ("--disable_schema_check",), ("--check_serial_versions",),
        ("-o", "--show_only_unapplied"), ("--force_mixed",), ("--ungroup",),
    ]:
        unsupported = flags[-1] in ("--dryrun", "--force_mixed", "--ungroup")
        p.add_argument(*flags, action="store_true",
                       help="Unsupported: Console has no metadata transactions" if unsupported else None)
    p.add_argument("--nontransactional", action="store_true",
                   help="Explicitly execute files and callbacks without rollback (PoC mode)")
    p.add_argument("-v", "--verbose", action="count", default=0)
    return p


def load(args):
    path = Path(args.base_dir) / "migrations.yml"
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError:
        raw = {}
    except yaml.YAMLError as exc:
        raise MigrationError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise MigrationError(f"{path} must contain a mapping")
    values = {}
    for name in Config.__dataclass_fields__:
        if name in raw:
            values[name] = raw[name]
        # pgmigrate 1.0.13 applies absent store_true=False values too.
        if getattr(args, name, None) is not None:
            values[name] = getattr(args, name)
    config = Config(**values)
    try:
        config.target = (float("inf") if config.target == "latest" else
                         int(config.target) if config.target is not None else None)
        config.baseline = int(config.baseline)
    except (TypeError, ValueError) as exc:
        raise MigrationError("Expected integer baseline/target, or target=latest") from exc
    if config.schema is None:
        config.schema = "public"
        config.disable_schema_check = True
    if not isinstance(config.schema, str) or not config.schema:
        raise MigrationError("schema must be a nonempty history namespace")
    if not config.disable_schema_check:
        raise MigrationError("PostgreSQL schema check is inapplicable; use --disable_schema_check")
    if config.user is not None and (not isinstance(config.user, str) or not config.user):
        raise MigrationError("user must be a nonempty author name")
    if config.dryrun or config.ungroup or config.force_mixed:
        raise MigrationError("Console has no metadata transactions; dryrun/ungroup/force_mixed are unsupported")
    if config.session or config.termination_interval:
        raise MigrationError("PostgreSQL session setup and backend termination are unsupported in this PoC")
    return config
