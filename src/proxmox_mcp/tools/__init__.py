"""MCP tool modules for proxmox_mcp.

Each tool module in this subpackage exposes a ``register`` function::

    def register(mcp: FastMCP, get_api) -> None: ...

where ``get_api`` is a zero-arg callable returning the proxmoxer ``ProxmoxAPI``
object. The indirection keeps tools testable offline (pass a fake api) and lets
the server wire the real client lazily. Tools are defined inside ``register``
and decorated with ``@mcp.tool()``.
"""
