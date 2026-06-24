"""The crown-jewel safety test: agents may create/operate but NEVER destroy.

Two enforcement layers live in :mod:`proxmox_mcp.safety`:

1. A runtime ``guard(api)`` proxy that transparently forwards proxmoxer-style
   chained access and the allowed verbs (``get``/``post``/``set``/``put``/
   ``create``) but raises :class:`PermissionError` the instant anything touches
   a ``delete`` attribute anywhere in the chain.

2. A registry guard ``assert_no_destructive_tools(mcp)`` that introspects a
   FastMCP instance and raises if any registered tool *name* looks destructive.

Everything here is fully offline -- a recording fake stands in for the real
proxmoxer ``ProxmoxAPI`` object.
"""

import pytest

from mcp.server.fastmcp import FastMCP

from proxmox_mcp.safety import guard, assert_no_destructive_tools
from proxmox_mcp.tools import read, lifecycle, provision


# --------------------------------------------------------------------------- #
# Recording fake api (proxmoxer-shaped)
# --------------------------------------------------------------------------- #
class _FakeApi:
    """Records the chained-attribute + call access path leading to a verb.

    Attribute access appends ``"<name>"`` to the path; calling appends the
    positional args. The terminal verbs (``get``/``post``/``set``/``put``/
    ``create``/``delete``) record themselves and return a marker echoing the
    full path so a test can assert exactly where a call landed.
    """

    _VERBS = ("get", "post", "set", "put", "create", "delete")

    def __init__(self, recorder, path):
        self._recorder = recorder
        self._path = path

    def __getattr__(self, name):
        if name in ("_recorder", "_path"):
            raise AttributeError(name)
        if name in self._VERBS:
            def _verb(*args, **kwargs):
                self._recorder["path"] = self._path + [name]
                self._recorder["args"] = args
                self._recorder["kwargs"] = kwargs
                return {"__verb__": name, "path": tuple(self._path), "kwargs": kwargs}
            return _verb
        return _FakeApi(self._recorder, self._path + [name])

    def __call__(self, *args):
        return _FakeApi(self._recorder, self._path + [("call", args)])


def _new_fake():
    recorder = {}
    return _FakeApi(recorder, []), recorder


# --------------------------------------------------------------------------- #
# Layer 1: runtime guard proxy
# --------------------------------------------------------------------------- #
def test_chained_navigation_and_get_forward_to_underlying():
    fake, recorder = _new_fake()
    api = guard(fake)

    result = api.nodes("pve1").qemu(100).status.current.get()

    assert recorder["path"] == [
        "nodes", ("call", ("pve1",)), "qemu", ("call", (100,)),
        "status", "current", "get",
    ]
    assert result["__verb__"] == "get"


def test_post_forwards_to_underlying():
    fake, recorder = _new_fake()
    api = guard(fake)

    api.nodes("pve1").qemu(100).status.start.post()

    assert recorder["path"][-1] == "post"
    assert recorder["path"][:5] == [
        "nodes", ("call", ("pve1",)), "qemu", ("call", (100,)), "status",
    ]


def test_set_put_create_verbs_forward():
    for verb in ("set", "put", "create"):
        fake, recorder = _new_fake()
        api = guard(fake)
        getattr(api.nodes("pve1").qemu, verb)(name="x")
        assert recorder["path"][-1] == verb
        assert recorder["kwargs"] == {"name": "x"}


def test_delete_attribute_access_raises_at_top_level():
    fake, _ = _new_fake()
    api = guard(fake)
    with pytest.raises(PermissionError):
        _ = api.delete


def test_delete_anywhere_in_chain_raises_before_call():
    fake, recorder = _new_fake()
    api = guard(fake)
    with pytest.raises(PermissionError):
        # Must raise on attribute *access*, before any call is forwarded.
        api.nodes("pve1").qemu(100).delete
    assert recorder == {}, "guard must not forward anything when delete is touched"


def test_delete_call_raises_and_does_not_reach_fake():
    fake, recorder = _new_fake()
    api = guard(fake)
    with pytest.raises(PermissionError):
        api.nodes("pve1").qemu(100).delete()
    assert recorder == {}


def test_delete_is_case_insensitive():
    fake, _ = _new_fake()
    api = guard(fake)
    for name in ("Delete", "DELETE", "deleTE"):
        with pytest.raises(PermissionError):
            getattr(api.nodes("pve1"), name)


def test_permission_error_message_is_clear():
    fake, _ = _new_fake()
    api = guard(fake)
    with pytest.raises(PermissionError) as exc:
        _ = api.nodes("pve1").qemu(100).delete
    msg = str(exc.value).lower()
    assert "delete" in msg


# --------------------------------------------------------------------------- #
# Layer 2: registry guard
# --------------------------------------------------------------------------- #
def _dummy_get_api():
    fake, _ = _new_fake()
    return fake


def _mcp_with_real_tools():
    mcp = FastMCP("test")
    read.register(mcp, _dummy_get_api)
    lifecycle.register(mcp, _dummy_get_api)
    provision.register(mcp, _dummy_get_api)
    return mcp


def test_real_tool_set_passes_registry_guard():
    mcp = _mcp_with_real_tools()
    # Should not raise.
    assert_no_destructive_tools(mcp)


def test_real_tool_count_is_about_29():
    mcp = _mcp_with_real_tools()
    names = [t.name for t in mcp._tool_manager.list_tools()]
    # 14 read + 8 lifecycle + 7 provision = 29.
    assert len(names) == 29


def test_registry_guard_flags_a_bad_delete_tool():
    mcp = _mcp_with_real_tools()

    @mcp.tool()
    def delete_vm(node: str, vmid: int) -> str:
        """A deliberately destructive tool that must never exist."""
        return "nope"

    with pytest.raises(PermissionError) as exc:
        assert_no_destructive_tools(mcp)
    assert "delete_vm" in str(exc.value)


@pytest.mark.parametrize(
    "bad_name",
    ["destroy_vm", "remove_container", "purge_backups", "rollback_snapshot",
     "shrink_disk", "wipe_storage", "erase_vm"],
)
def test_registry_guard_flags_each_destructive_verb(bad_name):
    mcp = FastMCP("test")

    @mcp.tool(name=bad_name)
    def _bad() -> str:
        """A destructive tool."""
        return "nope"

    with pytest.raises(PermissionError):
        assert_no_destructive_tools(mcp)


def test_denylist_over_real_tool_names_finds_none():
    """Direct denylist scan over the actual registered tool names."""
    from proxmox_mcp.safety import DESTRUCTIVE_PATTERNS, _name_is_destructive

    mcp = _mcp_with_real_tools()
    names = [t.name for t in mcp._tool_manager.list_tools()]
    assert names, "expected the real tools to be registered"
    offenders = [n for n in names if _name_is_destructive(n)]
    assert offenders == [], f"destructive tool names found: {offenders}"
    # Sanity: the denylist itself is populated.
    assert DESTRUCTIVE_PATTERNS


def test_benign_substrings_do_not_false_positive():
    """Names containing destructive verbs only as benign substrings pass."""
    from proxmox_mcp.safety import _name_is_destructive

    # "removable" contains "remove" only as a substring -> must NOT trip.
    assert not _name_is_destructive("list_removable_media_options")
    # Verify obviously-fine names stay clean.
    for ok in ("list_vms", "create_vm", "start_container", "set_vm_config",
               "next_vmid", "cluster_resources"):
        assert not _name_is_destructive(ok), ok
