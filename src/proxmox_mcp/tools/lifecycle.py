"""Tier B lifecycle tools for Proxmox VE (start/stop/shutdown/reboot).

These tools mutate cluster state but are *reversible* (Tier B): they change the
power state of a guest and never delete or destroy it. Every tool performs a
proxmoxer ``.post()`` against a guest's ``status`` subtree and returns the UPID
string of the resulting task so the caller can poll it via a read tool. None of
them auto-waits and none of them ever issues a delete/destroy verb.

proxmoxer shapes (all POST, each returns a UPID string)::

    VM:  api.nodes(node).qemu(vmid).status.<action>.post()
    LXC: api.nodes(node).lxc(vmid).status.<action>.post()

where ``<action>`` is one of ``start``, ``stop`` (hard stop), ``shutdown``
(ACPI graceful), ``reboot``.

The module follows the project tool-registration pattern: a single
``register(mcp, get_api)`` entry point defines the tools and attaches them to a
:class:`~mcp.server.fastmcp.FastMCP` instance. ``get_api`` is a zero-arg
callable returning the proxmoxer ``ProxmoxAPI`` object, resolved lazily on each
invocation so tools can be tested offline with a fake api.
"""

from __future__ import annotations

from typing import Any, Callable

from mcp.server.fastmcp import FastMCP

__all__ = ["register"]


def register(mcp: FastMCP, get_api: Callable[[], Any]) -> None:
    """Register the Tier B lifecycle tools onto *mcp*.

    *get_api* is a zero-arg callable returning the proxmoxer API object; it is
    invoked on every tool call so the real client can be wired lazily.
    """

    def _power(guest: str, action: str, node: str, vmid: int) -> str:
        """POST a power *action* to a *guest* ('qemu' or 'lxc') and return its UPID."""
        api = get_api()
        guest_endpoint = getattr(api.nodes(node), guest)(vmid)
        return getattr(guest_endpoint.status, action).post()

    # --- QEMU VMs --------------------------------------------------------- #
    @mcp.tool()
    def start_vm(node: str, vmid: int) -> str:
        """Start a QEMU VM; returns the task UPID."""
        return _power("qemu", "start", node, vmid)

    @mcp.tool()
    def stop_vm(node: str, vmid: int) -> str:
        """Hard-stop a QEMU VM (immediate power off); returns the task UPID."""
        return _power("qemu", "stop", node, vmid)

    @mcp.tool()
    def shutdown_vm(node: str, vmid: int) -> str:
        """Gracefully shut down a QEMU VM via ACPI; returns the task UPID."""
        return _power("qemu", "shutdown", node, vmid)

    @mcp.tool()
    def reboot_vm(node: str, vmid: int) -> str:
        """Reboot a QEMU VM; returns the task UPID."""
        return _power("qemu", "reboot", node, vmid)

    # --- LXC containers --------------------------------------------------- #
    @mcp.tool()
    def start_container(node: str, vmid: int) -> str:
        """Start an LXC container; returns the task UPID."""
        return _power("lxc", "start", node, vmid)

    @mcp.tool()
    def stop_container(node: str, vmid: int) -> str:
        """Hard-stop an LXC container (immediate power off); returns the task UPID."""
        return _power("lxc", "stop", node, vmid)

    @mcp.tool()
    def shutdown_container(node: str, vmid: int) -> str:
        """Gracefully shut down an LXC container via ACPI; returns the task UPID."""
        return _power("lxc", "shutdown", node, vmid)

    @mcp.tool()
    def reboot_container(node: str, vmid: int) -> str:
        """Reboot an LXC container; returns the task UPID."""
        return _power("lxc", "reboot", node, vmid)
