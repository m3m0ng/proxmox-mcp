"""Tests for the stdio server wiring (proxmox_mcp.server).

Everything here is fully offline: building the server must NOT require any
environment variables or network access (the api is built lazily on first tool
call). A fake client build is monkeypatched in so the lazy ``get_api`` closure
can be exercised without touching a real Proxmox.
"""

import pytest

from mcp.server.fastmcp import FastMCP

from proxmox_mcp import server
from proxmox_mcp.config import Config


# --------------------------------------------------------------------------- #
# A dummy config that needs no env vars / no network.
# --------------------------------------------------------------------------- #
def _dummy_config() -> Config:
    return Config(
        host="proxmox.invalid",
        user="agent@pve",
        token_name="mcp",
        token_value="secret",
        verify_ssl=False,
        port=8006,
    )


# A recording fake api standing in for proxmoxer.ProxmoxAPI.
class _FakeApi:
    def __init__(self, recorder, path):
        self._recorder = recorder
        self._path = path

    def __getattr__(self, name):
        if name in ("_recorder", "_path"):
            raise AttributeError(name)
        if name == "get":
            def _get(**kwargs):
                self._recorder["path"] = self._path
                return {"path": tuple(self._path), "kwargs": kwargs}
            return _get
        return _FakeApi(self._recorder, self._path + [name])

    def __call__(self, *args):
        return _FakeApi(self._recorder, self._path + [("call", args)])


# --------------------------------------------------------------------------- #
# build_server: returns a FastMCP with the full tool set, no env/network needed.
# --------------------------------------------------------------------------- #
def test_build_server_returns_fastmcp_with_full_tool_set():
    mcp = server.build_server(config=_dummy_config())
    assert isinstance(mcp, FastMCP)
    names = {t.name for t in mcp._tool_manager.list_tools()}
    # 14 read + 8 lifecycle + 7 provision = 29.
    assert len(names) == 29
    for expected in (
        "list_vms",
        "node_status",
        "get_task_status",
        "list_tasks",
        "start_vm",
        "create_container",
        "clone_vm",
        "set_vm_config",
        "allocate_vmid",
    ):
        assert expected in names, expected


def test_build_server_does_not_require_env_or_network(monkeypatch):
    """Even with no config passed and no env vars, building must not blow up.

    load_config() is only reached lazily on the first tool call; building the
    server alone must never touch env or network.
    """
    # Wipe the proxmox env vars to prove build_server does not read them.
    for var in (
        "PROXMOX_HOST",
        "PROXMOX_USER",
        "PROXMOX_TOKEN_NAME",
        "PROXMOX_TOKEN_VALUE",
        "PROXMOX_PORT",
        "PROXMOX_VERIFY_SSL",
        "PROXMOX_ENABLE_EXEC",
    ):
        monkeypatch.delenv(var, raising=False)

    # Make load_config explode if called during build (it must not be).
    def _boom(*a, **k):
        raise AssertionError("load_config must not be called during build_server")

    monkeypatch.setattr(server, "load_config", _boom)

    mcp = server.build_server()  # no config -> still must not read env
    assert isinstance(mcp, FastMCP)
    assert len(list(mcp._tool_manager.list_tools())) == 29


def test_build_server_passes_no_destructive_tools_assertion():
    # build_server already calls assert_no_destructive_tools internally; calling
    # it again here must also pass (no destructive tool names present).
    from proxmox_mcp.safety import assert_no_destructive_tools

    mcp = server.build_server(config=_dummy_config())
    assert_no_destructive_tools(mcp)  # must not raise


# --------------------------------------------------------------------------- #
# The lazily-built api is guarded: a .delete access raises PermissionError.
# --------------------------------------------------------------------------- #
def test_lazy_api_is_guarded_against_delete(monkeypatch):
    recorder = {}
    built = {"n": 0}

    def fake_get_client(config):
        built["n"] += 1
        return _FakeApi(recorder, [])

    # Patch the client build so no network happens.
    monkeypatch.setattr(server, "get_client", fake_get_client)

    mcp = server.build_server(config=_dummy_config())
    tools = {t.name: t for t in mcp._tool_manager.list_tools()}

    # api not built until first tool call (lazy).
    assert built["n"] == 0

    # Calling a read tool builds + caches the guarded api and forwards the get.
    result = tools["node_status"].fn(node="pve1")
    assert built["n"] == 1
    assert recorder["path"] == ["nodes", ("call", ("pve1",)), "status"]
    assert result["path"] == ("nodes", ("call", ("pve1",)), "status")

    # A second call must reuse the cached client (built only once).
    tools["list_nodes"].fn()
    assert built["n"] == 1


def test_get_api_closure_wraps_with_guard(monkeypatch):
    """Directly exercise the get_api closure and prove it returns a guarded api."""
    recorder = {}
    captured = {}

    def fake_get_client(config):
        return _FakeApi(recorder, [])

    monkeypatch.setattr(server, "get_client", fake_get_client)

    # Capture the get_api closure handed to the tool modules.
    real_read_register = server.read.register

    def spy_register(mcp, get_api):
        captured["get_api"] = get_api
        return real_read_register(mcp, get_api)

    monkeypatch.setattr(server.read, "register", spy_register)

    server.build_server(config=_dummy_config())
    get_api = captured["get_api"]

    guarded = get_api()
    # Navigation works through the guard...
    assert guarded.nodes("pve1").status.get()["path"] == ("nodes", ("call", ("pve1",)), "status")
    # ...but any delete access is forbidden.
    with pytest.raises(PermissionError):
        _ = guarded.nodes("pve1").qemu(100).delete


# --------------------------------------------------------------------------- #
# Import smoke: importing the entrypoints must not raise / need env vars.
# --------------------------------------------------------------------------- #
def test_module_entrypoint_imports_without_env(monkeypatch):
    for var in (
        "PROXMOX_HOST",
        "PROXMOX_USER",
        "PROXMOX_TOKEN_NAME",
        "PROXMOX_TOKEN_VALUE",
    ):
        monkeypatch.delenv(var, raising=False)

    import importlib

    # Both the server module and the python -m entrypoint must import cleanly.
    importlib.import_module("proxmox_mcp.server")
    importlib.import_module("proxmox_mcp.__main__")
    assert callable(server.main)
