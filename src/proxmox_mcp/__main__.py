"""Enable ``python -m proxmox_mcp`` to launch the stdio server."""

from __future__ import annotations

from .server import main

if __name__ == "__main__":
    main()
