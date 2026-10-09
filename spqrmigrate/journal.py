import base64
from datetime import datetime, timezone
import json

from .errors import MigrationError


def decode(value):
    if value is None:
        return []
    try:
        data = json.loads(value)
    except (ValueError, TypeError) as exc:
        raise MigrationError("Invalid JSON in spqrmigrate history; refusing to overwrite it") from exc
    if not isinstance(data, dict) or data.get("format") != 1 or not isinstance(data.get("history"), list):
        raise MigrationError("Unsupported spqrmigrate history format")
    seen = set()
    for row in data["history"]:
        if (not isinstance(row, dict) or set(row) != {
                "version", "description", "type", "installed_by", "installed_on"}
                or type(row["version"]) is not int or row["version"] in seen
                or row["type"] not in ("auto", "manual")
                or not all(isinstance(row[k], str) for k in ("description", "installed_by", "installed_on"))):
            raise MigrationError("Malformed spqrmigrate history entry")
        seen.add(row["version"])
    return data["history"]


def encode(history):
    return json.dumps(dict(format=1, history=history), ensure_ascii=False, separators=(",", ":"))


def storage_value(history):
    # Console string literals do not support PostgreSQL apostrophe escaping.
    return "json-base64-v1:" + base64.urlsafe_b64encode(encode(history).encode("utf-8")).decode("ascii")


def stored_history(value):
    if not isinstance(value, str) or not value.startswith("json-base64-v1:"):
        raise MigrationError("Unsupported spqrmigrate journal encoding; refusing to overwrite it")
    try:
        payload = base64.b64decode(value.removeprefix("json-base64-v1:"), altchars=b"-_", validate=True)
        text = payload.decode("utf-8")
    except (ValueError, UnicodeError) as exc:
        raise MigrationError("Invalid spqrmigrate journal encoding; refusing to overwrite it") from exc
    return decode(text)


def entry(version, description, author, kind="auto"):
    return dict(version=version, description=description, type=kind, installed_by=author,
                installed_on=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
