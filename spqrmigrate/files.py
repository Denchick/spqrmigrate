from dataclasses import dataclass
import logging
from pathlib import Path
import re

import sqlparse

from .errors import MigrationError

NAME = re.compile(r"V(?P<version>\d+)__(?P<description>.+)\.sql$")
HOOKS = ("beforeAll", "beforeEach", "afterEach", "afterAll")


@dataclass
class Migration:
    version: int
    description: str
    path: Path

    @property
    def transactional(self):
        return "NONTRANSACTIONAL" not in self.description

    def info(self):
        return dict(version=self.version, description=self.description, type="auto",
                    installed_by=None, installed_on=None, transactional=False)


def discover(base_dir):
    root = Path(base_dir) / "migrations"
    if not root.is_dir():
        raise MigrationError(f"Migrations directory not found: {root}")
    result = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        match = NAME.fullmatch(path.name)
        if not match:
            logging.warning("Skipping unmatched file %s", path)
            continue
        version = int(match["version"])
        if version in result:
            raise MigrationError(f"Duplicate version {version}: {result[version].path} and {path}")
        result[version] = Migration(version, match["description"].replace("_", " "), path)
    return result


def callbacks(value, base_dir):
    result = {name: [] for name in HOOKS}
    if isinstance(value, str):
        entries = []
        for item in value.split(","):
            if item:
                name, sep, path = item.partition(":")
                if not sep:
                    raise MigrationError(f"Expected callback type:path, got {item!r}")
                entries.append((name, [path]))
    elif isinstance(value, dict):
        entries = value.items()
    else:
        raise MigrationError("callbacks must be a string or mapping")
    for name, paths in entries:
        if name not in result or (paths is not None and not isinstance(paths, list)):
            raise MigrationError(f"Invalid callback definition: {name}")
        for value in paths or []:
            if not isinstance(value, str):
                raise MigrationError(f"Callback {name}: expected a path string")
            path = Path(base_dir) / value
            if not path.exists():
                raise MigrationError(f"Callback not found: {path}")
            result[name].extend(sorted(path.iterdir()) if path.is_dir() else [path])
    return result


def read_statements(path):
    try:
        text = path.read_text(encoding="utf-8")
        if "/* pgmigrate-encoding: utf-8 */" not in text:
            text.encode("ascii")
        return [s.strip() for s in sqlparse.split(sqlparse.format(text, strip_comments=True)) if s.strip()]
    except (OSError, UnicodeError, sqlparse.exceptions.SQLParseError) as exc:
        raise MigrationError(f"Cannot read {path}: {exc}") from exc


def validate(statement, source):
    """Conservative allowlist; the Console parser validates the complete grammar."""
    parsed = sqlparse.parse(statement)
    if len(parsed) != 1:
        raise MigrationError(f"{source}: expected one SQL command")
    tokens = [t for t in parsed[0].flatten()
              if not t.is_whitespace and t.ttype not in sqlparse.tokens.Comment and t.value != ";"]
    words = ["<quoted>" if t.ttype in sqlparse.tokens.Literal.String else t.value.upper() for t in tokens]
    allowed = words[:2] in (["CREATE", "DISTRIBUTION"], ["DROP", "DISTRIBUTION"])
    if words[:2] == ["ALTER", "DISTRIBUTION"]:
        allowed = any(words[i:i + 2] in (["ATTACH", "RELATION"], ["DETACH", "RELATION"],
                                        ["ATTACH", "TABLE"], ["DETACH", "TABLE"])
                      for i in range(3, len(words) - 1))
    if any(w in words for w in ("CASCADE", "DEFAULT", "ALL")):
        allowed = False
    if not allowed:
        raise MigrationError(f"{source}: forbidden command: {statement}. "
                             "Only distribution and relation DDL is allowed; "
                             "key ranges, data movement, CASCADE and default shards are excluded.")
