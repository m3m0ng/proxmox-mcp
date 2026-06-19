"""Proxmox API client wrapper and UPID task-waiting helpers.

This module builds a :class:`proxmoxer.ProxmoxAPI` from a :class:`Config` and
provides small, testable helpers for polling Proxmox tasks (identified by their
UPID strings) to completion.

``proxmoxer`` is imported lazily so that this module (and the helpers that do
not need a live connection) can be imported and unit-tested without the library
or a real server present.
"""

from __future__ import annotations

import time
from typing import Optional

from .config import Config

__all__ = [
    "ProxmoxClient",
    "get_client",
    "wait_for_task",
    "task_succeeded",
    "node_from_upid",
]


def _import_proxmox_api():
    """Return ``proxmoxer.ProxmoxAPI`` (imported lazily).

    Tests monkeypatch the module-level ``ProxmoxAPI`` name instead of calling
    this, so the real import only happens when actually connecting.
    """
    from proxmoxer import ProxmoxAPI as _ProxmoxAPI

    return _ProxmoxAPI


# Module-level name so tests can monkeypatch ``client.ProxmoxAPI`` without
# touching the real ``proxmoxer`` package. ``None`` means "import on first use".
ProxmoxAPI = None


def get_client(config: Config):
    """Build a ``proxmoxer.ProxmoxAPI`` from *config*.

    Uses :meth:`Config.to_proxmoxer_kwargs` for the connection arguments.
    """
    api_cls = ProxmoxAPI if ProxmoxAPI is not None else _import_proxmox_api()
    return api_cls(**config.to_proxmoxer_kwargs())


class ProxmoxClient:
    """Thin wrapper exposing a lazily-built, cached raw ``ProxmoxAPI`` as ``.api``."""

    def __init__(self, config: Config):
        self.config = config
        self._api = None

    @property
    def api(self):
        """The underlying ``proxmoxer.ProxmoxAPI`` (built on first access)."""
        if self._api is None:
            self._api = get_client(self.config)
        return self._api


def node_from_upid(upid: str) -> str:
    """Extract the node name from a Proxmox UPID string.

    UPID format::

        UPID:<node>:<pid>:<pstart>:<starttime>:<type>:<id>:<user>:

    Raises:
        ValueError: if *upid* is not a well-formed UPID.
    """
    if not isinstance(upid, str):
        raise ValueError(f"UPID must be a string, got {type(upid).__name__}")
    parts = upid.split(":")
    # "UPID", node, pid, pstart, starttime, type, id, user, (trailing "")
    if parts[0] != "UPID" or len(parts) < 3 or not parts[1]:
        raise ValueError(f"Not a valid UPID: {upid!r}")
    return parts[1]


def wait_for_task(
    proxmox,
    node: Optional[str],
    upid: str,
    timeout: float = 300,
    poll_interval: float = 1.0,
) -> dict:
    """Poll a Proxmox task to completion and return its final status dict.

    Polls ``proxmox.nodes(node).tasks(upid).status.get()`` until the returned
    dict reports ``status == "stopped"``, then returns that dict.

    If *node* is falsy it is derived from *upid* via :func:`node_from_upid`, so
    callers may pass just the UPID.

    Raises:
        TimeoutError: if the task does not finish within *timeout* seconds.
    """
    if not node:
        node = node_from_upid(upid)

    deadline = time.monotonic() + timeout
    while True:
        status = proxmox.nodes(node).tasks(upid).status.get()
        if status.get("status") == "stopped":
            return status
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"Task {upid} on node {node} did not finish within {timeout}s"
            )
        time.sleep(poll_interval)


def task_succeeded(status_dict: dict) -> bool:
    """Return True iff the task's ``exitstatus`` is exactly ``"OK"``."""
    return status_dict.get("exitstatus") == "OK"
