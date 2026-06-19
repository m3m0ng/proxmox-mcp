"""Tier A read/status tools for Proxmox VE.

These tools are strictly read-only: every one performs a proxmoxer ``.get()``
call and never mutates cluster state.

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
    """Register the Tier A read-only tools onto *mcp*.

    *get_api* is a zero-arg callable returning the proxmoxer API object; it is
    invoked on every tool call so the real client can be wired lazily.
    """

    @mcp.tool()
    def list_nodes() -> list[dict]:
        """List all nodes in the Proxmox cluster."""
        api = get_api()
        return api.nodes.get()

    @mcp.tool()
    def node_status(node: str) -> dict:
        """Get status and resource usage for a single node."""
        api = get_api()
        return api.nodes(node).status.get()

    @mcp.tool()
    def cluster_resources(resource_type: str = "vm") -> list[dict]:
        """List cluster resources of a given type (e.g. vm, storage, node)."""
        api = get_api()
        return api.cluster.resources.get(type=resource_type)

    @mcp.tool()
    def list_vms(node: str) -> list[dict]:
        """List QEMU virtual machines on a node."""
        api = get_api()
        return api.nodes(node).qemu.get()

    @mcp.tool()
    def list_containers(node: str) -> list[dict]:
        """List LXC containers on a node."""
        api = get_api()
        return api.nodes(node).lxc.get()

    @mcp.tool()
    def vm_status(node: str, vmid: int) -> dict:
        """Get the current runtime status of a QEMU VM."""
        api = get_api()
        return api.nodes(node).qemu(vmid).status.current.get()

    @mcp.tool()
    def container_status(node: str, vmid: int) -> dict:
        """Get the current runtime status of an LXC container."""
        api = get_api()
        return api.nodes(node).lxc(vmid).status.current.get()

    @mcp.tool()
    def vm_config(node: str, vmid: int) -> dict:
        """Get the configuration of a QEMU VM."""
        api = get_api()
        return api.nodes(node).qemu(vmid).config.get()

    @mcp.tool()
    def container_config(node: str, vmid: int) -> dict:
        """Get the configuration of an LXC container."""
        api = get_api()
        return api.nodes(node).lxc(vmid).config.get()

    @mcp.tool()
    def list_storage(node: str) -> list[dict]:
        """List storage available on a node."""
        api = get_api()
        return api.nodes(node).storage.get()

    @mcp.tool()
    def list_templates(node: str, storage: str) -> list[dict]:
        """List content (ISOs, container templates, backups) in a storage on a node."""
        api = get_api()
        return api.nodes(node).storage(storage).content.get()

    @mcp.tool()
    def next_vmid() -> dict:
        """Get the next free cluster-wide VM/container id."""
        api = get_api()
        return api.cluster.nextid.get()
