"""Tier C provisioning tools for Proxmox VE (create / clone / set-config).

These tools *create* new guests or *update* the configuration of existing ones.
They are additive and non-destructive: every tool either allocates a new
resource (an LXC container, a QEMU VM, or a clone of one) or applies a config
update. ``set_*_config`` maps to proxmoxer ``.config.set(...)`` which is a PUT
that updates configuration in place. No tool in this module ever issues a
destructive verb, shrinks a disk, or reverts a snapshot.

proxmoxer shapes::

    create LXC: api.nodes(node).lxc.create(**kwargs)          -> UPID string
    create VM:  api.nodes(node).qemu.create(**kwargs)         -> UPID string
    clone VM:   api.nodes(node).qemu(src).clone.post(**kwargs)-> UPID string
    clone LXC:  api.nodes(node).lxc(src).clone.post(**kwargs) -> UPID string
    set VM:     api.nodes(node).qemu(vmid).config.set(**cfg)  -> PUT
    set LXC:    api.nodes(node).lxc(vmid).config.set(**cfg)   -> PUT
    next id:    api.cluster.nextid.get()                      -> id string

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


def _drop_none(d: dict) -> dict:
    """Return *d* without any keys whose value is ``None``."""
    return {k: v for k, v in d.items() if v is not None}


def register(mcp: FastMCP, get_api: Callable[[], Any]) -> None:
    """Register the Tier C provisioning tools onto *mcp*.

    *get_api* is a zero-arg callable returning the proxmoxer API object; it is
    invoked on every tool call so the real client can be wired lazily.
    """

    @mcp.tool()
    def create_container(
        node: str,
        vmid: int,
        ostemplate: str,
        storage: str,
        hostname: str | None = None,
        cores: int = 1,
        memory: int = 512,
        rootfs: str | None = None,
        net0: str = "name=eth0,bridge=vmbr0,ip=dhcp",
        **extra: Any,
    ) -> str:
        """Create an LXC container from a template; returns the task UPID."""
        api = get_api()
        kwargs = _drop_none(
            {
                "vmid": vmid,
                "ostemplate": ostemplate,
                "storage": storage,
                "hostname": hostname,
                "cores": cores,
                "memory": memory,
                "rootfs": rootfs or f"{storage}:8",
                "net0": net0,
                **extra,
            }
        )
        return api.nodes(node).lxc.create(**kwargs)

    @mcp.tool()
    def create_vm(
        node: str,
        vmid: int,
        name: str | None = None,
        cores: int = 1,
        memory: int = 512,
        net0: str = "virtio,bridge=vmbr0",
        scsi0: str | None = None,
        ostype: str = "l26",
        **extra: Any,
    ) -> str:
        """Create a QEMU virtual machine; returns the task UPID."""
        api = get_api()
        kwargs = _drop_none(
            {
                "vmid": vmid,
                "name": name,
                "cores": cores,
                "memory": memory,
                "net0": net0,
                "scsi0": scsi0,
                "ostype": ostype,
                **extra,
            }
        )
        return api.nodes(node).qemu.create(**kwargs)

    @mcp.tool()
    def clone_vm(
        node: str,
        source_vmid: int,
        newid: int,
        name: str | None = None,
        full: bool = True,
        storage: str | None = None,
        target: str | None = None,
        **extra: Any,
    ) -> str:
        """Clone a QEMU VM (full clone needs storage); returns the task UPID."""
        api = get_api()
        kwargs = _drop_none(
            {
                "newid": newid,
                "full": 1 if full else 0,
                "name": name,
                "storage": storage,
                "target": target,
                **extra,
            }
        )
        return api.nodes(node).qemu(source_vmid).clone.post(**kwargs)

    @mcp.tool()
    def clone_container(
        node: str,
        source_vmid: int,
        newid: int,
        hostname: str | None = None,
        full: bool = True,
        storage: str | None = None,
        target: str | None = None,
        **extra: Any,
    ) -> str:
        """Clone an LXC container (full clone needs storage); returns the task UPID."""
        api = get_api()
        kwargs = _drop_none(
            {
                "newid": newid,
                "full": 1 if full else 0,
                "hostname": hostname,
                "storage": storage,
                "target": target,
                **extra,
            }
        )
        return api.nodes(node).lxc(source_vmid).clone.post(**kwargs)

    @mcp.tool()
    def set_vm_config(node: str, vmid: int, **config: Any) -> Any:
        """Update the configuration of a QEMU VM (PUT); requires at least one field."""
        if not config:
            raise ValueError("set_vm_config requires at least one config field")
        api = get_api()
        return api.nodes(node).qemu(vmid).config.set(**config)

    @mcp.tool()
    def set_container_config(node: str, vmid: int, **config: Any) -> Any:
        """Update the configuration of an LXC container (PUT); requires at least one field."""
        if not config:
            raise ValueError("set_container_config requires at least one config field")
        api = get_api()
        return api.nodes(node).lxc(vmid).config.set(**config)

    @mcp.tool()
    def allocate_vmid() -> Any:
        """Get the next free cluster-wide VM/container id to use before a create."""
        api = get_api()
        return api.cluster.nextid.get()
