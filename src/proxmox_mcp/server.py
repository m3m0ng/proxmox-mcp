"""Stdio MCP server entrypoint: the final wiring slice.

:func:`build_server` assembles a :class:`~mcp.server.fastmcp.FastMCP` instance
with every Tier A (read), Tier B (lifecycle) and Tier C (provision) tool
registered against a *lazily* built, guarded proxmoxer client.

Two properties matter:

* **No env / no network at build time.** The proxmoxer client is built on the
  first tool call (via the ``get_api`` closure), so importing or constructing
  the server requires neither environment variables nor a live Proxmox.
* **No deletes, ever.** The lazily-built client is wrapped with
  :func:`proxmox_mcp.safety.guard` so even the wired tools physically cannot
  issue a destructive verb, and :func:`assert_no_destructive_tools` is asserted
  before the server is returned as a fail-fast structural check.
"""

from __future__ import annotations

from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from .client import get_client
from .config import Config, load_config
from .safety import assert_no_destructive_tools, guard
from .tools import lifecycle, provision, read

__all__ = ["build_server", "main"]


def build_server(config: Optional[Config] = None) -> FastMCP:
    """Build and return a fully wired :class:`FastMCP` server.

    The proxmoxer client is built lazily and cached on first tool call, then
    wrapped with :func:`guard` so destructive verbs are impossible. When
    *config* is ``None`` it is loaded from the environment via
    :func:`load_config` -- but only at that first call, never at build time.
    """
    mcp = FastMCP("proxmox-mcp")

    cache: dict[str, Any] = {}

    def get_api() -> Any:
        """Return the cached, guarded proxmoxer api, building it on first use."""
        if "api" not in cache:
            cfg = config if config is not None else load_config()
            cache["api"] = guard(get_client(cfg))
        return cache["api"]

    read.register(mcp, get_api)
    lifecycle.register(mcp, get_api)
    provision.register(mcp, get_api)

    # Fail fast if a destructive tool ever sneaks into the registry.
    assert_no_destructive_tools(mcp)

    return mcp


def main() -> None:
    """Console entrypoint: run the wired server over stdio."""
    build_server().run(transport="stdio")


if __name__ == "__main__":
    main()
