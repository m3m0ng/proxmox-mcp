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


# The Tier A tools, with the expected access path (as built by the fake)
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
    "get_task_status": (
        {"upid": "UPID:pve1:0001:0002:0003:qmstart:100:agent@pve:"},
        ["nodes", ("call", ("pve1",)), "tasks", ("call", ("UPID:pve1:0001:0002:0003:qmstart:100:agent@pve:",)), "status"],
        {},
    ),
    "list_tasks": (
        {"node": "pve1", "limit": 10},
        ["nodes", ("call", ("pve1",)), "tasks"],
        {"limit": 10},
    ),
}


def _expected_result(name, expected_path, expected_get_kwargs):
    result = {"__endpoint__": tuple(expected_path), "kwargs": expected_get_kwargs}
    if name == "get_task_status":
        result.update(
            {
                "upid": "UPID:pve1:0001:0002:0003:qmstart:100:agent@pve:",
                "node": "pve1",
                "success": False,
                "warnings": False,
            }
        )
    return result


def test_registers_exactly_the_read_tools():
    fake, _ = _new_fake()
    tools = _registered_tools(fake)
    assert set(tools) == set(_EXPECTED)
    assert len(tools) == 14


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
    assert result == _expected_result(name, expected_path, expected_get_kwargs)


def test_cluster_resources_defaults_to_vm():
    fake, recorder = _new_fake()
    tools = _registered_tools(fake)

    tools["cluster_resources"].fn()

    assert recorder["path"] == ["cluster", "resources"]
    assert recorder["get_kwargs"] == {"type": "vm"}


def test_get_task_status_classifies_finished_results():
    class _Status:
        def __init__(self, payload):
            self.payload = payload

        def get(self):
            return self.payload

    class _Task:
        def __init__(self, payload):
            self.status = _Status(payload)

    class _Tasks:
        def __init__(self, payload):
            self.payload = payload

        def __call__(self, upid):
            return _Task(self.payload)

    class _Node:
        def __init__(self, payload):
            self.tasks = _Tasks(payload)

    class _Api:
        def __init__(self, payload):
            self.payload = payload

        def nodes(self, node):
            return _Node(self.payload)

    fake = _Api({"status": "stopped", "exitstatus": "WARNINGS: 1"})
    tools = _registered_tools(fake)

    result = tools["get_task_status"].fn(
        upid="UPID:pve1:0001:0002:0003:qmstart:100:agent@pve:"
    )

    assert result["success"] is True
    assert result["warnings"] is True


def test_get_task_status_rejects_bad_upid_before_get_api():
    def get_api():
        raise AssertionError("get_api must not be called for malformed UPIDs")

    mcp = FastMCP("test")
    read.register(mcp, get_api)
    tools = {t.name: t for t in mcp._tool_manager.list_tools()}

    with pytest.raises(ValueError):
        tools["get_task_status"].fn(upid="not-a-upid")


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
