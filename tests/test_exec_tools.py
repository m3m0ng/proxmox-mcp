"""Unit tests for proxmox_mcp.tools.exec (offline, no network, no real ssh).

The in-guest exec tools are thin wrappers: they ask ``get_config`` for the SSH
``Config``, build a host command via the ssh.py ``build_pct_exec`` /
``build_qm_exec`` helpers, and run it through ``run_ssh_command``. These tests
monkeypatch the module-level ``run_ssh_command`` reference with a fake that
records the command string it was given and returns a known ``ExecResult``, so
nothing here touches a real host.
"""

import shlex

import pytest

from mcp.server.fastmcp import FastMCP

from proxmox_mcp.config import Config
from proxmox_mcp.ssh import ExecResult
from proxmox_mcp.tools import exec as exec_tools


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #
def _config(**overrides):
    base = dict(
        host="10.0.0.10",
        user="agent@pve",
        token_name="mcp",
        token_value="supersecret",
        enable_exec=True,
        ssh_host="10.0.0.10",
        ssh_port=22,
        ssh_user="root",
        ssh_key_file="/home/me/.ssh/id_ed25519",
    )
    base.update(overrides)
    return Config(**base)


class _Recorder:
    """A fake ``run_ssh_command`` capturing its args and returning a fixed result."""

    def __init__(self, result: ExecResult):
        self._result = result
        self.calls = []

    def __call__(self, config, command, timeout=60):
        self.calls.append({"config": config, "command": command, "timeout": timeout})
        return self._result

    @property
    def last(self):
        return self.calls[-1]


@pytest.fixture
def fake_run(monkeypatch):
    result = ExecResult(exit_code=0, stdout="done\n", stderr="")
    recorder = _Recorder(result)
    monkeypatch.setattr(exec_tools, "run_ssh_command", recorder)
    return recorder


def _registered_tools(config):
    mcp = FastMCP("test")
    exec_tools.register(mcp, lambda: config)
    return {t.name: t for t in mcp._tool_manager.list_tools()}


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #
def test_registers_exactly_two_tools():
    tools = _registered_tools(_config())
    assert set(tools) == {"exec_in_container", "exec_in_vm"}
    assert len(tools) == 2


def test_every_tool_has_a_docstring_description():
    tools = _registered_tools(_config())
    for name, tool in tools.items():
        assert tool.description, f"{name} is missing a description"


# --------------------------------------------------------------------------- #
# exec_in_container -> pct exec
# --------------------------------------------------------------------------- #
def test_exec_in_container_builds_pct_exec(fake_run):
    config = _config()
    tools = _registered_tools(config)

    result = tools["exec_in_container"].fn(vmid=200, command="uptime")

    cmd = fake_run.last["command"]
    assert cmd.startswith("pct exec 200 -- ")
    # The config we returned from get_config is the one passed through.
    assert fake_run.last["config"] is config

    # Dict shape mapped from the ExecResult.
    assert result == {
        "exit_code": 0,
        "stdout": "done\n",
        "stderr": "",
        "ok": True,
    }


def test_exec_in_container_maps_nonzero_exit(monkeypatch):
    recorder = _Recorder(ExecResult(exit_code=2, stdout="", stderr="boom\n"))
    monkeypatch.setattr(exec_tools, "run_ssh_command", recorder)
    tools = _registered_tools(_config())

    result = tools["exec_in_container"].fn(vmid=200, command="false")
    assert result == {
        "exit_code": 2,
        "stdout": "",
        "stderr": "boom\n",
        "ok": False,
    }


# --------------------------------------------------------------------------- #
# exec_in_vm -> qm guest exec
# --------------------------------------------------------------------------- #
def test_exec_in_vm_builds_qm_guest_exec(fake_run):
    tools = _registered_tools(_config())

    result = tools["exec_in_vm"].fn(vmid=100, command="uptime")

    cmd = fake_run.last["command"]
    assert cmd.startswith("qm guest exec 100 -- ")
    assert result["ok"] is True


# --------------------------------------------------------------------------- #
# Shell-injection boundary — command is shlex-quoted inside the host command
# --------------------------------------------------------------------------- #
def test_exec_in_container_quotes_command(fake_run):
    tools = _registered_tools(_config())
    dangerous = "apt-get update && echo hi"

    tools["exec_in_container"].fn(vmid=200, command=dangerous)

    cmd = fake_run.last["command"]
    quoted = shlex.quote(dangerous)
    # The whole command is a single shlex-quoted token.
    assert quoted in cmd
    # The dangerous "&&" never reaches the host shell as a bare operator: it
    # only appears inside the quoted token. Splitting the host command must
    # yield the dangerous string intact as one final token.
    tokens = shlex.split(cmd)
    assert tokens[:4] == ["pct", "exec", "200", "--"]
    assert tokens[-1] == dangerous
    assert "&&" not in cmd.replace(quoted, "")


def test_exec_in_vm_quotes_command(fake_run):
    tools = _registered_tools(_config())
    for payload in ("$(reboot)", "`reboot`", "x && reboot", "x | tee /etc/passwd"):
        tools["exec_in_vm"].fn(vmid=100, command=payload)
        cmd = fake_run.last["command"]
        quoted = shlex.quote(payload)
        assert quoted in cmd
        tokens = shlex.split(cmd)
        assert tokens[-1] == payload
        assert payload not in cmd.replace(quoted, "")


# --------------------------------------------------------------------------- #
# timeout forwarding
# --------------------------------------------------------------------------- #
def test_timeout_defaults_to_60(fake_run):
    tools = _registered_tools(_config())
    tools["exec_in_container"].fn(vmid=200, command="uptime")
    assert fake_run.last["timeout"] == 60


def test_timeout_is_forwarded(fake_run):
    tools = _registered_tools(_config())
    tools["exec_in_vm"].fn(vmid=100, command="uptime", timeout=120)
    assert fake_run.last["timeout"] == 120


# --------------------------------------------------------------------------- #
# get_config indirection is lazy (invoked per call)
# --------------------------------------------------------------------------- #
def test_get_config_called_each_invocation(fake_run):
    count = {"n": 0}
    config = _config()

    def get_config():
        count["n"] += 1
        return config

    mcp = FastMCP("test")
    exec_tools.register(mcp, get_config)
    tools = {t.name: t for t in mcp._tool_manager.list_tools()}

    tools["exec_in_container"].fn(vmid=200, command="uptime")
    tools["exec_in_vm"].fn(vmid=100, command="uptime")
    assert count["n"] == 2
