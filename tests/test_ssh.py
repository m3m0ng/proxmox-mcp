"""Unit tests for proxmox_mcp.ssh (offline, no network, no real paramiko).

A fake ``paramiko`` module stands in for the real library so nothing here
touches a live host or requires paramiko to be installed.
"""

import shlex

import pytest

import proxmox_mcp.ssh as ssh_mod
from proxmox_mcp.ssh import (
    ExecResult,
    build_pct_exec,
    build_qm_exec,
    run_ssh_command,
)
from proxmox_mcp.config import Config


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


# --------------------------------------------------------------------------- #
# Fake paramiko plumbing
# --------------------------------------------------------------------------- #
class _FakeChannel:
    def __init__(self, exit_code):
        self._exit_code = exit_code

    def recv_exit_status(self):
        return self._exit_code


class _FakeStream:
    """Stands in for paramiko stdout/stderr file-like objects."""

    def __init__(self, data: bytes, exit_code=None):
        self._data = data
        if exit_code is not None:
            self.channel = _FakeChannel(exit_code)

    def read(self):
        return self._data


class _FakeSSHClient:
    def __init__(self):
        self.connect_kwargs = None
        self.exec_command_args = None
        self.exec_command_kwargs = None
        self.closed = False
        self.missing_host_key_policy = None
        # Defaults the test can override.
        self._exit_code = 0
        self._stdout = b"hello\n"
        self._stderr = b""

    def set_missing_host_key_policy(self, policy):
        self.missing_host_key_policy = policy

    def connect(self, **kwargs):
        self.connect_kwargs = kwargs

    def exec_command(self, command, timeout=None):
        self.exec_command_args = command
        self.exec_command_kwargs = {"timeout": timeout}
        stdin = _FakeStream(b"")
        stdout = _FakeStream(self._stdout, exit_code=self._exit_code)
        stderr = _FakeStream(self._stderr)
        return stdin, stdout, stderr

    def close(self):
        self.closed = True


class _FakeAutoAddPolicy:
    pass


class _FakeParamiko:
    """A fake ``paramiko`` module exposing the bits ssh.py uses."""

    AutoAddPolicy = _FakeAutoAddPolicy

    def __init__(self):
        self.last_client = None

    def SSHClient(self):  # noqa: N802 - mirror paramiko's class name
        client = _FakeSSHClient()
        self.last_client = client
        return client


@pytest.fixture
def fake_paramiko(monkeypatch):
    fake = _FakeParamiko()
    monkeypatch.setattr(ssh_mod, "paramiko", fake)
    return fake


# --------------------------------------------------------------------------- #
# run_ssh_command
# --------------------------------------------------------------------------- #
def test_run_ssh_command_connects_with_ssh_target_kwargs(fake_paramiko):
    config = _config()
    result = run_ssh_command(config, "uptime", timeout=30)

    client = fake_paramiko.last_client
    assert client is not None

    # Connected with exactly the kwargs from ssh_target() (+ timeout).
    assert client.connect_kwargs["hostname"] == "10.0.0.10"
    assert client.connect_kwargs["port"] == 22
    assert client.connect_kwargs["username"] == "root"
    assert client.connect_kwargs["key_filename"] == "/home/me/.ssh/id_ed25519"
    assert client.connect_kwargs["timeout"] == 30

    # AutoAddPolicy applied.
    assert isinstance(client.missing_host_key_policy, _FakeAutoAddPolicy)

    # Ran the given command.
    assert client.exec_command_args == "uptime"

    # Returned the fake's results.
    assert isinstance(result, ExecResult)
    assert result.exit_code == 0
    assert result.stdout == "hello\n"
    assert result.stderr == ""

    # Client was closed.
    assert client.closed is True


def test_run_ssh_command_uses_password_when_no_key(fake_paramiko):
    config = _config(ssh_key_file=None, ssh_password="hunter2")
    run_ssh_command(config, "ls")

    client = fake_paramiko.last_client
    assert client.connect_kwargs["password"] == "hunter2"
    assert "key_filename" not in client.connect_kwargs


def test_run_ssh_command_returns_stderr_and_nonzero_exit(fake_paramiko):
    config = _config()

    # Patch the next-created client's outputs via the factory.
    orig_factory = _FakeParamiko.SSHClient

    def factory(self):
        client = orig_factory(self)
        client._exit_code = 2
        client._stdout = b""
        client._stderr = b"boom\n"
        return client

    fake_paramiko.SSHClient = factory.__get__(fake_paramiko, _FakeParamiko)

    result = run_ssh_command(config, "false")
    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == "boom\n"
    assert result.ok is False


def test_run_ssh_command_closes_client_on_exception(fake_paramiko):
    config = _config()

    def factory(self):
        client = _FakeSSHClient()
        self.last_client = client

        def boom(command, timeout=None):
            raise RuntimeError("exec failed")

        client.exec_command = boom
        return client

    fake_paramiko.SSHClient = factory.__get__(fake_paramiko, _FakeParamiko)

    with pytest.raises(RuntimeError):
        run_ssh_command(config, "whoami")

    assert fake_paramiko.last_client.closed is True


def test_run_ssh_command_decodes_with_errors_replace(fake_paramiko):
    config = _config()

    def factory(self):
        client = _FakeSSHClient()
        self.last_client = client
        client._stdout = b"\xff\xfeok"
        return client

    fake_paramiko.SSHClient = factory.__get__(fake_paramiko, _FakeParamiko)

    result = run_ssh_command(config, "cat /bin/x")
    # Invalid bytes are replaced, not raised.
    assert "ok" in result.stdout


# --------------------------------------------------------------------------- #
# ExecResult.ok
# --------------------------------------------------------------------------- #
def test_exec_result_ok_true_on_zero():
    assert ExecResult(0, "", "").ok is True


def test_exec_result_ok_false_on_nonzero():
    assert ExecResult(1, "", "").ok is False
    assert ExecResult(255, "out", "err").ok is False


# --------------------------------------------------------------------------- #
# build_pct_exec / build_qm_exec — the shell-injection security boundary
# --------------------------------------------------------------------------- #
def test_build_pct_exec_prefix_and_structure():
    cmd = build_pct_exec(250, "uptime")
    assert cmd.startswith("pct exec 250 -- /bin/sh -lc ")
    # The inner command is shlex-quoted.
    assert cmd.endswith(shlex.quote("uptime"))


def test_build_qm_exec_prefix_and_structure():
    cmd = build_qm_exec(250, "uptime")
    assert cmd.startswith("qm guest exec 250 -- /bin/sh -lc ")
    assert cmd.endswith(shlex.quote("uptime"))


def test_build_pct_exec_quotes_semicolon_injection():
    dangerous = "apt update; rm -rf /"
    cmd = build_pct_exec(250, dangerous)

    quoted = shlex.quote(dangerous)
    # The whole dangerous string is a SINGLE shlex-quoted argument.
    assert quoted in cmd
    # The dangerous "; rm -rf /" must live inside the quotes, not reach the
    # host shell as a separate statement. Splitting the host command with
    # shlex must yield the dangerous string intact as one token.
    tokens = shlex.split(cmd)
    assert tokens[:5] == ["pct", "exec", "250", "--", "/bin/sh"]
    assert tokens[-1] == dangerous
    assert "; rm -rf /" not in cmd.replace(quoted, "")


def test_build_qm_exec_quotes_command_substitution():
    for payload in ("$(reboot)", "`reboot`", "x && reboot", "x | tee /etc/passwd"):
        cmd = build_qm_exec(250, payload)
        quoted = shlex.quote(payload)
        assert quoted in cmd
        tokens = shlex.split(cmd)
        assert tokens[-1] == payload
        # Nothing dangerous leaks outside the quoted token.
        assert payload not in cmd.replace(quoted, "")


def test_build_pct_exec_rejects_non_int_vmid():
    with pytest.raises((TypeError, ValueError)):
        build_pct_exec("250; rm -rf /", "uptime")  # type: ignore[arg-type]


def test_build_qm_exec_rejects_non_int_vmid():
    with pytest.raises((TypeError, ValueError)):
        build_qm_exec("250; rm -rf /", "uptime")  # type: ignore[arg-type]
