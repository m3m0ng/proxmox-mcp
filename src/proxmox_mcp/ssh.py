"""SSH transport for running commands on the Proxmox host.

In-guest exec (``pct exec`` for LXC, ``qm guest exec`` for VMs) is performed by
running the corresponding host command over SSH. This module provides:

* :func:`run_ssh_command` — connect to the host and run one command, returning
  an :class:`ExecResult`.
* :func:`build_pct_exec` / :func:`build_qm_exec` — pure helpers that build a
  shell-safe host command string. They are the security boundary: every
  user-supplied command is ``shlex``-quoted so it cannot break out into the
  host shell.

``paramiko`` is imported lazily (mirroring :mod:`proxmox_mcp.client`'s handling
of ``proxmoxer``) so this module can be imported and unit-tested without the
library or a real host present.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass

from .config import Config

__all__ = [
    "ExecResult",
    "run_ssh_command",
    "build_pct_exec",
    "build_qm_exec",
]


def _import_paramiko():
    """Return the ``paramiko`` module (imported lazily).

    Tests monkeypatch the module-level ``paramiko`` name with a fake instead of
    calling this, so the real import only happens when actually connecting.
    """
    import paramiko as _p

    return _p


# Module-level name so tests can monkeypatch ``ssh.paramiko`` with a fake module
# without touching the real ``paramiko`` package. ``None`` means "import on
# first use".
paramiko = None


@dataclass
class ExecResult:
    """Result of running a command over SSH."""

    exit_code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        """True if the command exited successfully (exit code 0)."""
        return self.exit_code == 0


def run_ssh_command(config: Config, command: str, timeout: int = 60) -> ExecResult:
    """Connect to the Proxmox host over SSH and run *command*.

    Uses :meth:`Config.ssh_target` for the connection kwargs. Returns an
    :class:`ExecResult` with the command's exit code and decoded stdout/stderr.
    The SSH client is always closed before returning, including on error.
    """
    paramiko_mod = paramiko if paramiko is not None else _import_paramiko()

    client = paramiko_mod.SSHClient()
    client.set_missing_host_key_policy(paramiko_mod.AutoAddPolicy())
    try:
        client.connect(**config.ssh_target(), timeout=timeout)
        _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
        exit_code = stdout.channel.recv_exit_status()
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        return ExecResult(exit_code=exit_code, stdout=out, stderr=err)
    finally:
        client.close()


def build_pct_exec(vmid: int, command: str) -> str:
    """Build a host command that runs *command* inside LXC *vmid* via ``pct``.

    Produces ``pct exec <vmid> -- /bin/sh -lc <quoted command>``. *command* is
    passed through :func:`shlex.quote` so shell metacharacters cannot escape
    into the host shell. *vmid* must be an ``int``.
    """
    vmid = _validate_vmid(vmid)
    return f"pct exec {vmid} -- /bin/sh -lc {shlex.quote(command)}"


def build_qm_exec(vmid: int, command: str) -> str:
    """Build a host command that runs *command* inside VM *vmid* via the agent.

    Produces ``qm guest exec <vmid> -- /bin/sh -lc <quoted command>``.
    *command* is passed through :func:`shlex.quote` so shell metacharacters
    cannot escape into the host shell. *vmid* must be an ``int``.
    """
    vmid = _validate_vmid(vmid)
    return f"qm guest exec {vmid} -- /bin/sh -lc {shlex.quote(command)}"


def _validate_vmid(vmid: int) -> int:
    """Return *vmid* as an int, rejecting non-integer (incl. bool) values."""
    if isinstance(vmid, bool) or not isinstance(vmid, int):
        raise TypeError(f"vmid must be an int, got {type(vmid).__name__}")
    return vmid
