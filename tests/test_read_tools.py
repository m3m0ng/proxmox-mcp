"""Unit tests for proxmox_mcp.tools.read (offline, no network).

A recording fake stands in for ``proxmoxer.ProxmoxAPI``. It captures the
chained-attribute + call access path proxmoxer uses
(e.g. ``api.nodes(node).qemu(vmid).status.current.get()``) and returns a
marker so each tool can be asserted to hit the expected endpoint and forward
its params.
"""

import pytest

from mcp.server.fastmcp import FastMCP

from proxmox_mcp.tools import read


# --------------------------------------------------------------------------- #
# Recording fake api
# --------------------------------------------------------------------------- #
class _FakeApi:
    """Records the proxmoxer-style access path leading to ``.get(...)``.

    Every attribute access appends ``"<name>"`` to the path and every call
    appends the positional args. ``.get(**kwargs)`` records ``("get", kwargs)``
    on the shared recorder and returns a marker echoing the full path.
    """

    def __init__(self, recorder, path):
        self._recorder = recorder
        self._path = path

    def __getattr__(self, name):
        if name in ("_recorder", "_path"):
            raise AttributeError(name)
        if name == "get":
            def _get(**kwargs):
                self._recorder["path"] = self._path
                self._recorder["get_kwargs"] = kwargs
                return {"__endpoint__": tuple(self._path), "kwargs": kwargs}
            return _get
        return _FakeApi(self._recorder, self._path + [name])

    def __call__(self, *args):
        return _FakeApi(self._recorder, self._path + [("call", args)])


def _new_fake():
    recorder = {}
    return _FakeApi(recorder, []), recorder


# --------------------------------------------------------------------------- #
# Helpers to drive the registered tools
# --------------------------------------------------------------------------- #
def _registered_tools(fake_api):
    mcp = FastMCP("test")
    read.register(mcp, lambda: fake_api)
    return {t.name: t for t in mcp._tool_manager.list_tools()}


# The 12 Tier A tools, with the expected access path (as built by the fake)
# and the kwargs forwarded to ``.get()``.
_EXPECTED = {
    "list_nodes": (
        {},
        ["nodes"],
        {},
    ),
    "node_status": (
        {"node": "pve1"},
        ["nodes", ("call", ("pve1",)), "status"],
        {},
    ),
    "cluster_resources": (
        {"resource_type": "storage"},
        ["cluster", "resources"],
        {"type": "storage"},
    ),
    "list_vms": (
        {"node": "pve1"},
        ["nodes", ("call", ("pve1",)), "qemu"],
        {},
    ),
    "list_containers": (
        {"node": "pve1"},
        ["nodes", ("call", ("pve1",)), "lxc"],
        {},
    ),
    "vm_status": (
        {"node": "pve1", "vmid": 100},
        ["nodes", ("call", ("pve1",)), "qemu", ("call", (100,)), "status", "current"],
        {},
    ),
    "container_status": (
        {"node": "pve1", "vmid": 200},
        ["nodes", ("call", ("pve1",)), "lxc", ("call", (200,)), "status", "current"],
        {},
    ),
    "vm_config": (
        {"node": "pve1", "vmid": 100},
        ["nodes", ("call", ("pve1",)), "qemu", ("call", (100,)), "config"],
        {},
    ),
    "container_config": (
        {"node": "pve1", "vmid": 200},
        ["nodes", ("call", ("pve1",)), "lxc", ("call", (200,)), "config"],
        {},
    ),
    "list_storage": (
        {"node": "pve1"},
        ["nodes", ("call", ("pve1",)), "storage"],
        {},
    ),
    "list_templates": (
        {"node": "pve1", "storage": "local"},
        ["nodes", ("call", ("pve1",)), "storage", ("call", ("local",)), "content"],
        {},
    ),
    "next_vmid": (
        {},
        ["cluster", "nextid"],
        {},
    ),
}


def test_registers_exactly_the_twelve_tools():
    fake, _ = _new_fake()
    tools = _registered_tools(fake)
    assert set(tools) == set(_EXPECTED)
    assert len(tools) == 12


def test_every_tool_has_a_docstring_description():
    fake, _ = _new_fake()
    tools = _registered_tools(fake)
    for name, tool in tools.items():
        assert tool.description, f"{name} is missing a description"


@pytest.mark.parametrize("name", list(_EXPECTED))
def test_tool_hits_expected_endpoint_and_forwards_params(name):
    kwargs, expected_path, expected_get_kwargs = _EXPECTED[name]
    fake, recorder = _new_fake()
    tools = _registered_tools(fake)

    result = tools[name].fn(**kwargs)

    assert recorder["path"] == expected_path
    assert recorder["get_kwargs"] == expected_get_kwargs
    # The tool returns exactly what the api returned.
    assert result == {"__endpoint__": tuple(expected_path), "kwargs": expected_get_kwargs}


def test_cluster_resources_defaults_to_vm():
    fake, recorder = _new_fake()
    tools = _registered_tools(fake)

    tools["cluster_resources"].fn()

    assert recorder["path"] == ["cluster", "resources"]
    assert recorder["get_kwargs"] == {"type": "vm"}


def test_get_api_called_each_invocation():
    """The get_api indirection must be invoked per call (lazy wiring)."""
    fake, _ = _new_fake()
    count = {"n": 0}

    def get_api():
        count["n"] += 1
        return fake

    mcp = FastMCP("test")
    read.register(mcp, get_api)
    tools = {t.name: t for t in mcp._tool_manager.list_tools()}

    tools["list_nodes"].fn()
    tools["next_vmid"].fn()
    assert count["n"] == 2
