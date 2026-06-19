"""Tier C in-guest exec tools for Proxmox VE.

These tools run a command *as root inside a target guest* by SSHing to the
Proxmox host and invoking the host's guest-exec mechanism: ``pct exec`` for LXC
containers and ``qm guest exec`` (the QEMU guest agent) for VMs. They are useful
for in-guest provisioning such as ``apt-get update`` or installing/starting an
application.

Unlike the read/lifecycle tools, exec needs the SSH :class:`~proxmox_mcp.config.Config`
rather than the proxmoxer API, so ``register`` takes ``get_config`` (a zero-arg
callable returning the ``Config``) instead of ``get_api``.

The tools are deliberately thin. All shell quoting / injection safety lives in
the ssh.py builders (:func:`build_pct_exec` / :func:`build_qm_exec`), which
``shlex``-quote the user command so it cannot break out into the host shell.
There is intentionally NO tool that runs an arbitrary command directly on the
host shell — only ``pct``/``qm``-mediated guest exec is exposed.
"""

from __future__ import annotations

from typing import Callable

from mcp.server.fastmcp import FastMCP

from ..config import Config
from ..ssh import build_pct_exec, build_qm_exec, run_ssh_command

__all__ = ["register"]


def register(mcp: FastMCP, get_config: Callable[[], Config]) -> None:
    """Register the in-guest exec tools onto *mcp*.

    *get_config* is a zero-arg callable returning the SSH :class:`Config`; it is
    invoked on every tool call so the real config can be wired lazily.
    """

    @mcp.tool()
    def exec_in_container(vmid: int, command: str, timeout: int = 60) -> dict:
        """Run a shell command as root INSIDE LXC container *vmid* (via ``pct exec`` over SSH to the Proxmox host)."""
        cfg = get_config()
        cmd = build_pct_exec(vmid, command)
        result = run_ssh_command(cfg, cmd, timeout=timeout)
        return {
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "ok": result.ok,
        }

    @mcp.tool()
    def exec_in_vm(vmid: int, command: str, timeout: int = 60) -> dict:
        """Run a shell command as root INSIDE QEMU VM *vmid* via the QEMU guest agent (``qm guest exec`` over SSH to the Proxmox host); the guest agent must be installed and running in the VM."""
        cfg = get_config()
        cmd = build_qm_exec(vmid, command)
        result = run_ssh_command(cfg, cmd, timeout=timeout)
        return {
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "ok": result.ok,
        }
