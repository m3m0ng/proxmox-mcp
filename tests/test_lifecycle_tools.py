"""Unit tests for proxmox_mcp.tools.lifecycle (offline, no network).

A recording fake stands in for ``proxmoxer.ProxmoxAPI``. It captures the
chained-attribute + call access path proxmoxer uses
(e.g. ``api.nodes(node).qemu(vmid).status.start.post()``) and returns a UPID
marker so each lifecycle tool can be asserted to hit the expected endpoint and
return the UPID the post produced.

These tools are Tier B (mutating but reversible). A guard asserts the fake's
``.delete`` is never reached by any lifecycle tool.
"""

import pytest

from mcp.server.fastmcp import FastMCP

from proxmox_mcp.tools import lifecycle


# --------------------------------------------------------------------------- #
# Recording fake api
# --------------------------------------------------------------------------- #
class _FakeApi:
    """Records the proxmoxer-style access path leading to ``.post(...)``.

    Every attribute access appends ``"<name>"`` to the path and every call
    appends the positional args. ``.post(**kwargs)`` records the full path on
    the shared recorder and returns a fabricated UPID string.

    Accessing ``delete`` flips ``recorder["delete_touched"]`` so a test can
    assert no lifecycle tool ever reaches a delete/destroy verb.
    """

    def __init__(self, recorder, path):
        self._recorder = recorder
        self._path = path

    def __getattr__(self, name):
        if name in ("_recorder", "_path"):
            raise AttributeError(name)
        if name == "delete":
            self._recorder["delete_touched"] = True

            def _delete(**kwargs):  # pragma: no cover - must never be called
                self._recorder["delete_called"] = True
                return None

            return _delete
        if name == "post":
            def _post(**kwargs):
                upid = "UPID:pve1:00001234:00ABCDEF:60000000:vzstart:100:root@pam:"
                self._recorder["path"] = self._path
                self._recorder["post_kwargs"] = kwargs
                self._recorder["returned_upid"] = upid
                return upid
            return _post
        return _FakeApi(self._recorder, self._path + [name])

    def __call__(self, *args):
        return _FakeApi(self._recorder, self._path + [("call", args)])


def _new_fake():
    recorder = {}
    return _FakeApi(recorder, []), recorder


def _registered_tools(fake_api):
    mcp = FastMCP("test")
    lifecycle.register(mcp, lambda: fake_api)
    return {t.name: t for t in mcp._tool_manager.list_tools()}


# The 8 Tier B lifecycle tools, with call kwargs and the expected access path
# (as built by the fake) leading to ``.post()``.
def _path(guest, action, vmid):
    return ["nodes", ("call", ("pve1",)), guest, ("call", (vmid,)), "status", action]


_EXPECTED = {
    "start_vm": ({"node": "pve1", "vmid": 100}, _path("qemu", "start", 100)),
    "stop_vm": ({"node": "pve1", "vmid": 100}, _path("qemu", "stop", 100)),
    "shutdown_vm": ({"node": "pve1", "vmid": 100}, _path("qemu", "shutdown", 100)),
    "reboot_vm": ({"node": "pve1", "vmid": 100}, _path("qemu", "reboot", 100)),
    "start_container": ({"node": "pve1", "vmid": 200}, _path("lxc", "start", 200)),
    "stop_container": ({"node": "pve1", "vmid": 200}, _path("lxc", "stop", 200)),
    "shutdown_container": ({"node": "pve1", "vmid": 200}, _path("lxc", "shutdown", 200)),
    "reboot_container": ({"node": "pve1", "vmid": 200}, _path("lxc", "reboot", 200)),
}


def test_registers_exactly_the_eight_tools():
    fake, _ = _new_fake()
    tools = _registered_tools(fake)
    assert set(tools) == set(_EXPECTED)
    assert len(tools) == 8


def test_every_tool_has_a_docstring_description():
    fake, _ = _new_fake()
    tools = _registered_tools(fake)
    for name, tool in tools.items():
        assert tool.description, f"{name} is missing a description"


@pytest.mark.parametrize("name", list(_EXPECTED))
def test_tool_posts_to_expected_endpoint_and_returns_upid(name):
    kwargs, expected_path = _EXPECTED[name]
    fake, recorder = _new_fake()
    tools = _registered_tools(fake)

    result = tools[name].fn(**kwargs)

    assert recorder["path"] == expected_path
    # POST carries no body for these status actions.
    assert recorder["post_kwargs"] == {}
    # The tool returns exactly the UPID the post produced.
    assert result == recorder["returned_upid"]
    assert isinstance(result, str) and result.startswith("UPID:")


@pytest.mark.parametrize("name", list(_EXPECTED))
def test_tool_never_touches_delete(name):
    """Sanity guard for the no-delete intent: no lifecycle tool may reach delete."""
    kwargs, _ = _EXPECTED[name]
    fake, recorder = _new_fake()
    tools = _registered_tools(fake)

    tools[name].fn(**kwargs)

    assert "delete_touched" not in recorder
    assert "delete_called" not in recorder


def test_get_api_called_each_invocation():
    """The get_api indirection must be invoked per call (lazy wiring)."""
    fake, _ = _new_fake()
    count = {"n": 0}

    def get_api():
        count["n"] += 1
        return fake

    mcp = FastMCP("test")
    lifecycle.register(mcp, get_api)
    tools = {t.name: t for t in mcp._tool_manager.list_tools()}

    tools["start_vm"].fn(node="pve1", vmid=100)
    tools["stop_container"].fn(node="pve1", vmid=200)
    assert count["n"] == 2
