import os
from pathlib import Path
import socket
import subprocess
import time

import psycopg2
import pytest
import yaml

from spqrmigrate.backend import Console
from spqrmigrate.errors import MigrationError


def pytest_addoption(parser):
    parser.addoption("--fail-on-skip", action="store_true", help="Fail if any selected test is skipped")


def pytest_sessionfinish(session, exitstatus):
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if session.config.getoption("--fail-on-skip") and reporter and reporter.stats.get("skipped"):
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def launch(command, log):
    with log.open("ab") as output:
        return subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT)


def stop(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def wait_ready(process, probe, log):
    deadline = time.monotonic() + 30
    last_error = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            pytest.fail(f"Service exited with {process.returncode}; see {log}")
        try:
            probe()
            return
        except (OSError, psycopg2.Error, MigrationError) as exc:
            last_error = exc
            time.sleep(0.1)
    pytest.fail(f"Service did not become ready: {last_error}; see {log}")


class CoordinatorCluster:
    def __init__(self, root, coordinator_binary, etcd_binary, dsn):
        params = psycopg2.extensions.parse_dsn(dsn)
        if params.get("host") != "127.0.0.1" or not params.get("port"):
            pytest.fail("Managed tests require a DSN with host=127.0.0.1 and an explicit port")
        self.root = root
        self.dsn = dsn
        self.binary = str(Path(coordinator_binary).resolve())
        self.etcd_binary = str(Path(etcd_binary).resolve())
        self.grpc_port = free_port()
        self.etcd_port = free_port()
        self.peer_port = free_port()
        self.coordinator = None
        self.etcd = None
        shards = root / "shards.yaml"
        shards.write_text("shards: {}\n", encoding="utf-8")
        self.config = root / "coordinator.yaml"
        self.config.write_text(yaml.safe_dump({
            "host": "127.0.0.1",
            "coordinator_port": params["port"],
            "grpc_api_port": str(self.grpc_port),
            "qdb_addrs": [f"http://127.0.0.1:{self.etcd_port}"],
            "log_level": "info",
            "watch_task_groups": False,
            "recover_key_range_moves": False,
            "shard_data": str(shards),
            "frontend_rules": [{"pool_default": True, "auth_rule": {"auth_method": "ok"}}],
        }), encoding="utf-8")

    def start(self):
        client_url = f"http://127.0.0.1:{self.etcd_port}"
        peer_url = f"http://127.0.0.1:{self.peer_port}"
        self.etcd = launch([
            self.etcd_binary, "--name=ci", f"--data-dir={self.root / 'etcd-data'}",
            f"--listen-client-urls={client_url}", f"--advertise-client-urls={client_url}",
            f"--listen-peer-urls={peer_url}", f"--initial-advertise-peer-urls={peer_url}",
            f"--initial-cluster=ci={peer_url}",
        ], self.root / "etcd.log")
        wait_ready(self.etcd, lambda: self.connect_port(self.etcd_port), self.root / "etcd.log")
        self.start_coordinator()

    @staticmethod
    def connect_port(port):
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            pass

    def start_coordinator(self):
        log = self.root / "coordinator.log"
        self.coordinator = launch([
            self.binary, "--config", str(self.config), "--qdb-impl", "etcd",
        ], log)
        wait_ready(self.coordinator, self.probe_coordinator, log)

    def probe_coordinator(self):
        # The native gRPC listener starts only after coordinator leadership is acquired.
        self.connect_port(self.grpc_port)
        console = Console(self.dsn, "ci-readiness")
        try:
            console.history()
        finally:
            console.close()

    def restart(self):
        stop(self.coordinator)
        self.start_coordinator()

    def close(self):
        stop(self.coordinator)
        stop(self.etcd)


@pytest.fixture(scope="session")
def coordinator_cluster(tmp_path_factory):
    coordinator = os.environ.get("SPQRMIGRATE_TEST_COORDINATOR")
    etcd = os.environ.get("SPQRMIGRATE_TEST_ETCD")
    if not coordinator and not etcd:
        yield None
        return
    if not coordinator or not etcd or not os.environ.get("SPQRMIGRATE_TEST_DSN"):
        pytest.fail("Managed tests require SPQRMIGRATE_TEST_COORDINATOR, SPQRMIGRATE_TEST_ETCD, and SPQRMIGRATE_TEST_DSN")
    cluster = CoordinatorCluster(tmp_path_factory.mktemp("cluster"), coordinator, etcd,
                                 os.environ["SPQRMIGRATE_TEST_DSN"])
    try:
        cluster.start()
        yield cluster
    finally:
        cluster.close()
