"""The no-delete safety invariant: agents can create/operate but NEVER destroy.

This module enforces the project's crown-jewel guarantee in two layers:

1. **Runtime guard** -- :func:`guard` wraps a proxmoxer ``ProxmoxAPI`` object in
   a transparent proxy (:class:`SafeProxmox`). The proxy forwards normal
   chained resource navigation (``api.nodes(node).qemu(vmid)...``) and the
   allowed HTTP verbs (``get``/``post``/``set``/``put``/``create``) straight
   through to the wrapped object, but raises :class:`PermissionError` the
   instant anything touches a ``delete`` attribute -- anywhere in the chain.

2. **Registry guard** -- :func:`assert_no_destructive_tools` introspects a
   FastMCP instance's registered tools and raises :class:`PermissionError` if
   any tool *name* matches a destructive verb on the denylist.

Both layers are import-light and fully testable offline.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = [
    "SafeProxmox",
    "guard",
    "DESTRUCTIVE_PATTERNS",
    "assert_no_destructive_tools",
]


# The single forbidden HTTP verb. proxmoxer issues a destructive request via a
# ``.delete()`` call on a resource path, so blocking access to the ``delete``
# attribute anywhere in the proxy chain removes the capability entirely.
_FORBIDDEN_VERB = "delete"

# Terminal HTTP verbs that return plain data (not a further-chainable resource).
# Their results are passed through unwrapped so callers receive real dicts/lists.
_TERMINAL_VERBS = frozenset({"get", "post", "set", "put", "create"})


class SafeProxmox:
    """A transparent proxy over a proxmoxer API object that forbids deletes.

    Attribute access returns another :class:`SafeProxmox` wrapping the
    underlying attribute, *except* when the requested name is ``delete``
    (case-insensitive), in which case a :class:`PermissionError` is raised
    immediately -- before anything is forwarded to the wrapped object.

    Calling the proxy forwards the call to the wrapped target and re-wraps the
    result, so the proxmoxer chained pattern
    (``api.nodes(node).qemu(vmid).status.start.post()``) works unchanged while
    ``api.nodes(node).qemu(vmid).delete()`` is impossible.
    """

    __slots__ = ("_target",)

    def __init__(self, target: Any):
        object.__setattr__(self, "_target", target)

    def __getattr__(self, name: str) -> Any:
        # ``__getattr__`` only fires for names not found normally; with
        # ``__slots__`` that means everything except ``_target``.
        if name.lower() == _FORBIDDEN_VERB:
            raise PermissionError(
                "Refusing to access 'delete': destructive operations are "
                "permanently disabled by the no-delete safety guard."
            )
        attr = getattr(object.__getattribute__(self, "_target"), name)
        # Terminal verbs return plain data; hand back the bound method directly
        # so its result is not wrapped in a proxy. All other names continue the
        # chained-navigation pattern and stay wrapped.
        if name.lower() in _TERMINAL_VERBS:
            return attr
        return SafeProxmox(attr)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        target = object.__getattribute__(self, "_target")
        return SafeProxmox(target(*args, **kwargs))

    def __getitem__(self, key: Any) -> Any:
        target = object.__getattribute__(self, "_target")
        return SafeProxmox(target[key])

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        target = object.__getattribute__(self, "_target")
        return f"SafeProxmox({target!r})"


def guard(api: Any) -> SafeProxmox:
    """Wrap *api* in a :class:`SafeProxmox` that forbids destructive verbs.

    The returned proxy behaves like the underlying proxmoxer API for all
    non-destructive use but raises :class:`PermissionError` on any ``delete``.
    """
    return SafeProxmox(api)


# --------------------------------------------------------------------------- #
# Registry guard
# --------------------------------------------------------------------------- #

# Destructive verbs that must never appear as (a token in) a tool name. Matched
# word-ish so benign substrings (e.g. "removable" -> "remove") do not trip it.
DESTRUCTIVE_PATTERNS = (
    "delete",
    "destroy",
    "remove",
    "purge",
    "rollback",
    "shrink",
    "wipe",
    "erase",
)


def _tokens(name: str) -> list[str]:
    """Split a tool name into lowercase word-ish tokens.

    Splits on non-alphanumeric separators and camelCase boundaries so that
    ``delete_vm``, ``deleteVm`` and ``vm-delete`` all yield a ``delete`` token,
    while ``removable`` stays a single token that is *not* equal to ``remove``.
    """
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)
    return [t for t in re.split(r"[^a-zA-Z0-9]+", spaced) if t]


def _name_is_destructive(name: str) -> bool:
    """Return True if *name* contains a destructive verb as a whole token."""
    tokens = {t.lower() for t in _tokens(name)}
    return any(verb in tokens for verb in DESTRUCTIVE_PATTERNS)


def _list_tools(mcp: Any) -> list[Any]:
    """Return the registered tool objects from a FastMCP instance.

    Uses the synchronous internal tool manager, which is stable across the
    installed mcp version and avoids needing an event loop.
    """
    tool_manager = getattr(mcp, "_tool_manager", None)
    if tool_manager is not None and hasattr(tool_manager, "list_tools"):
        return list(tool_manager.list_tools())
    raise RuntimeError(
        "Cannot introspect FastMCP tools: no synchronous _tool_manager.list_tools()"
    )


def assert_no_destructive_tools(mcp: Any) -> None:
    """Raise :class:`PermissionError` if *mcp* exposes any destructive tool.

    Scans every registered tool's *name* against :data:`DESTRUCTIVE_PATTERNS`
    using word-ish token matching. Raises with the offending names listed if
    any match; returns ``None`` when the tool set is clean.
    """
    offenders = sorted(
        {t.name for t in _list_tools(mcp) if _name_is_destructive(t.name)}
    )
    if offenders:
        raise PermissionError(
            "Destructive tool(s) registered, violating the no-delete guarantee: "
            + ", ".join(offenders)
        )
