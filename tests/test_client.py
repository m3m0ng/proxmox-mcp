"""Unit tests for proxmox_mcp.client (offline, no network).

A fake Proxmox object stands in for ``proxmoxer.ProxmoxAPI`` so nothing here
touches a live server.
"""

import pytest

import proxmox_mcp.client as client_mod
from proxmox_mcp.client import (
    ProxmoxClient,
    get_client,
    node_from_upid,
    task_succeeded,
    task_warnings,
    wait_for_task,
)
from proxmox_mcp.config import Config


def _config():
    return Config(
        host="10.0.0.10",
        user="agent@pve",
        token_name="mcp",
        token_value="supersecret",
        verify_ssl=False,
        port=8006,
    )


# --------------------------------------------------------------------------- #
# Fake Proxmox API plumbing
# --------------------------------------------------------------------------- #
class _FakeStatus:
    def __init__(self, results):
        self._results = list(results)
        self.calls = 0

    def get(self):
        self.calls += 1
        # Return the last result once the script is exhausted (e.g. forever
        # "running" for the timeout test).
        idx = min(self.calls - 1, len(self._results) - 1)
        return self._results[idx]


class _FakeTask:
    def __init__(self, status):
        self.status = status


class _FakeNode:
    def __init__(self, expected_node, status):
        self._expected_node = expected_node
        self._status = status

    def tasks(self, upid):
        return _FakeTask(self._status)


class _FakeProxmox:
    """Mimics ``proxmox.nodes(node).tasks(upid).status.get()``."""

    def __init__(self, results):
        self._status = _FakeStatus(results)
        self.requested_nodes = []

    def nodes(self, node):
        self.requested_nodes.append(node)
        return _FakeNode(node, self._status)

    @property
    def status(self):
        return self._status


# --------------------------------------------------------------------------- #
# get_client / ProxmoxClient
# --------------------------------------------------------------------------- #
def test_get_client_passes_kwargs(monkeypatch):
    captured = {}

    def fake_api(**kwargs):
        captured.update(kwargs)
        return "SENTINEL_API"

    monkeypatch.setattr(client_mod, "ProxmoxAPI", fake_api)

    cfg = _config()
    api = get_client(cfg)

    assert api == "SENTINEL_API"
    assert captured == cfg.to_proxmoxer_kwargs()


def test_proxmox_client_wrapper_lazy_and_cached(monkeypatch):
    calls = {"n": 0}

    def fake_api(**kwargs):
        calls["n"] += 1
        return f"API-{calls['n']}"

    monkeypatch.setattr(client_mod, "ProxmoxAPI", fake_api)

    wrapper = ProxmoxClient(_config())
    # Lazy: nothing built until .api is accessed.
    assert calls["n"] == 0

    first = wrapper.api
    second = wrapper.api
    assert first == "API-1"
    assert second == "API-1"  # cached, not rebuilt
    assert calls["n"] == 1


# --------------------------------------------------------------------------- #
# node_from_upid
# --------------------------------------------------------------------------- #
def test_node_from_upid_parses_node():
    upid = "UPID:pve1:0001ABCD:00ABCDEF:65000000:qmstart:100:agent@pve:"
    assert node_from_upid(upid) == "pve1"


@pytest.mark.parametrize("bad", ["", "not-a-upid", "UPID:", "FOO:pve1:x"])
def test_node_from_upid_rejects_garbage(bad):
    with pytest.raises(ValueError):
        node_from_upid(bad)


# --------------------------------------------------------------------------- #
# task_succeeded
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "status",
    [
        {"status": "stopped", "exitstatus": "OK"},
        # Proxmox reports "WARNINGS: N" for a task that completed its work but
        # emitted warnings (e.g. the benign systemd-nesting note on Debian LXC
        # creation). That is success-with-warnings, not failure.
        {"status": "stopped", "exitstatus": "WARNINGS: 1"},
        {"status": "stopped", "exitstatus": "WARNINGS: 3"},
    ],
)
def test_task_succeeded_true_for_ok_and_warnings(status):
    assert task_succeeded(status) is True


@pytest.mark.parametrize(
    "status",
    [
        {"status": "stopped", "exitstatus": "some error"},
        {"status": "stopped", "exitstatus": ""},
        {"status": "stopped"},
        {},
    ],
)
def test_task_succeeded_false_otherwise(status):
    assert task_succeeded(status) is False


@pytest.mark.parametrize(
    "status,expected",
    [
        ({"exitstatus": "OK"}, False),
        ({"exitstatus": "WARNINGS: 2"}, True),
        ({"exitstatus": "some error"}, False),
        ({}, False),
    ],
)
def test_task_warnings(status, expected):
    assert task_warnings(status) is expected


# --------------------------------------------------------------------------- #
# wait_for_task
# --------------------------------------------------------------------------- #
def test_wait_for_task_polls_until_stopped(monkeypatch):
    sleeps = []
    monkeypatch.setattr(client_mod.time, "sleep", lambda s: sleeps.append(s))

    results = [
        {"status": "running"},
        {"status": "running"},
        {"status": "stopped", "exitstatus": "OK"},
    ]
    fake = _FakeProxmox(results)

    final = wait_for_task(fake, "pve1", "UPID:pve1:x:y:z:t:i:u:", poll_interval=0.01)

    assert final == {"status": "stopped", "exitstatus": "OK"}
    # Polled exactly until the stopped result (3 gets).
    assert fake.status.calls == 3
    # Slept between polls but not after the final one.
    assert len(sleeps) == 2
    assert fake.requested_nodes == ["pve1", "pve1", "pve1"]


def test_wait_for_task_accepts_node_from_upid(monkeypatch):
    monkeypatch.setattr(client_mod.time, "sleep", lambda s: None)
    fake = _FakeProxmox([{"status": "stopped", "exitstatus": "OK"}])

    upid = "UPID:pvenode:0001:0002:0003:qmstart:100:agent@pve:"
    final = wait_for_task(fake, None, upid, poll_interval=0.01)

    assert final["status"] == "stopped"
    assert fake.requested_nodes == ["pvenode"]


def test_wait_for_task_times_out(monkeypatch):
    # Drive a deterministic clock so the timeout triggers without real waiting.
    fake_now = {"t": 0.0}
    monkeypatch.setattr(client_mod.time, "monotonic", lambda: fake_now["t"])

    def fake_sleep(s):
        fake_now["t"] += s

    monkeypatch.setattr(client_mod.time, "sleep", fake_sleep)

    fake = _FakeProxmox([{"status": "running"}])  # never stops

    with pytest.raises(TimeoutError):
        wait_for_task(fake, "pve1", "UPID:pve1:x:y:z:t:i:u:", timeout=5, poll_interval=1.0)
