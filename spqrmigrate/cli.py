import logging
import sys

import psycopg2
import sqlparse.engine.grouping

from .backend import Console
from .config import load, parser
from .errors import MigrationError
from .runner import run


def main(argv=None):
    args = parser().parse_args(argv)
    logging.basicConfig(level=logging.ERROR - 10 * min(3, args.verbose),
                        format="%(asctime)s %(levelname)-8s: %(message)s")
    sqlparse.engine.grouping.MAX_GROUPING_DEPTH = None
    sqlparse.engine.grouping.MAX_GROUPING_TOKENS = None
    backend = None
    try:
        config = load(args)
        backend = Console(config.conn, config.schema)
        run(config, args.cmd, backend, sys.stdout)
        return 0
    except (MigrationError, psycopg2.Error, OSError, ValueError) as exc:
        logging.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        logging.error("Interrupted; partial nontransactional changes may remain")
        return 130
    finally:
        if backend is not None:
            backend.close()
