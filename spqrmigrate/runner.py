import json
import logging

from .errors import MigrationError
from .files import callbacks, discover, read_statements, validate
from .journal import entry


def run(config, command, backend, stdout):
    if command == "migrate" and config.target is None:
        raise MigrationError("Unknown target; use -t latest or an integer version")
    hooks = callbacks(config.callbacks, config.base_dir)
    with backend.lock():
        history = backend.history()
        author = config.user or backend.user
        if command == "clean":
            backend.clean()
            return
        if command == "baseline":
            if any(row["version"] >= config.baseline for row in history):
                raise MigrationError(f"Version {config.baseline} already applied; cannot baseline")
            backend.save([entry(config.baseline, "Forced baseline", author, "manual")])
            return
        files = discover(config.base_dir)
        floor = max([config.baseline] + [row["version"] for row in history])
        target = config.target if config.target is not None else float("inf")
        pending = [m for version, m in sorted(files.items()) if floor < version <= target]
        if command == "info":
            result = {row["version"]: dict(row, transactional=False) for row in history
                      if not config.show_only_unapplied}
            result.update({m.version: m.info() for m in pending})
            stdout.write(json.dumps(dict(sorted(result.items())), indent=4, separators=(",", ": ")) + "\n")
            return
        if not pending:
            return
        if not config.nontransactional and any(m.transactional for m in pending):
            raise MigrationError("Console cannot execute transactional migrations. "
                                 "Use --nontransactional explicitly for this PoC (no rollback)")
        if not history and not config.nontransactional:
            raise MigrationError("pgmigrate requires transactional initialization; "
                                 "use --nontransactional explicitly for this PoC")
        if config.check_serial_versions:
            versions = [m.version for m in pending]
            if history:
                versions.insert(0, max(row["version"] for row in history))
            if versions[-1] - versions[0] + 1 != len(versions):
                raise MigrationError(f"Migration versions have gaps: {versions}")
        paths = [m.path for m in pending] + [path for paths in hooks.values() for path in paths]
        loaded = {}
        for path in dict.fromkeys(paths):
            loaded[path] = read_statements(path)
            for statement in loaded[path]:
                validate(statement, path)
        logging.warning("PoC: executing without transactions; metadata and history are separate writes")

        def execute(path):
            for statement in loaded[path]:
                try:
                    backend.execute(statement)
                except MigrationError as exc:
                    raise MigrationError(f"{path}: {exc}. Partial changes may remain; "
                                         "inspect info before retrying idempotent SQL") from exc

        # Preserve pgmigrate's omission of hooks on files marked NONTRANSACTIONAL.
        with_hooks = [m for m in pending if m.transactional]
        for migration in pending:
            if migration.transactional:
                if migration is with_hooks[0]:
                    for path in hooks["beforeAll"]:
                        execute(path)
                for path in hooks["beforeEach"]:
                    execute(path)
            logging.info("Applying %s: %s", migration.version, migration.path)
            execute(migration.path)
            record = entry(migration.version, migration.description, author)
            if config.set_version_info_after_callbacks and migration.transactional:
                for path in hooks["afterEach"]:
                    execute(path)
            try:
                backend.save(history + [record])
            except MigrationError as exc:
                raise MigrationError(f"{migration.path}: file executed but recording version failed; "
                                     "reconnect and inspect info before retrying. {exc}") from exc
            history.append(record)
            if not config.set_version_info_after_callbacks and migration.transactional:
                for path in hooks["afterEach"]:
                    execute(path)
            if migration.transactional and migration is with_hooks[-1]:
                for path in hooks["afterAll"]:
                    execute(path)
