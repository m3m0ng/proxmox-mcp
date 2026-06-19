"""Tests for opt-in exec tool wiring in proxmox_mcp.server (TE4).

Fully offline: an explicit :class:`Config` object is passed to ``build_server``
so neither environment variables nor a live connection are needed. The exec
tools only need the config object to register (the proxmoxer api stays lazy and
the SSH connection is only made on an actual tool call, which we never make).
"""

from mcp.server.fastmcp import FastMCP

from proxmox_mcp import server
from proxmox_mcp.config import Config
from proxmox_mcp.safety import assert_no_destructive_tools


# The 27 baseline tools (12 read + 8 lifecycle + 7 provision).
_EXEC_TOOLS = {"exec_in_container", "exec_in_vm"}


def _config(enable_exec: bool) -> Config:
    """Minimally valid Config; ssh fields stay default. No env, no network."""
    return Config(
        host="proxmox.invalid",
        user="agent@pve",
        token_name="mcp",
        token_value="secret",
        enable_exec=enable_exec,
    )


def _tool_names(mcp: FastMCP) -> set[str]:
    return {t.name for t in mcp._tool_manager.list_tools()}


def test_exec_disabled_keeps_baseline_27_without_exec_tools():
    mcp = server.build_server(config=_config(enable_exec=False))
    names = _tool_names(mcp)
    assert len(names) == 27
    assert "exec_in_container" not in names
    assert "exec_in_vm" not in names


def test_exec_enabled_adds_two_exec_tools_for_29():
    mcp = server.build_server(config=_config(enable_exec=True))
    names = _tool_names(mcp)
    assert len(names) == 29
    assert "exec_in_container" in names
    assert "exec_in_vm" in names


def test_exec_enabled_still_passes_no_destructive_assertion():
    mcp = server.build_server(config=_config(enable_exec=True))
    # Must not raise -- exec tool names are non-destructive.
    assert_no_destructive_tools(mcp)


def test_enabling_exec_does_not_alter_baseline_tools():
    baseline = _tool_names(server.build_server(config=_config(enable_exec=False)))
    with_exec = _tool_names(server.build_server(config=_config(enable_exec=True)))
    # Every baseline tool is still present; only the exec tools are added.
    assert baseline <= with_exec
    assert with_exec - baseline == _EXEC_TOOLS


# --------------------------------------------------------------------------- #
# Deployment path: build_server() with NO config reads PROXMOX_ENABLE_EXEC from
# the environment (this is how `python -m proxmox_mcp` actually runs). Regression
# guard: exec opt-in must work env-driven, not only with an explicit Config.
# --------------------------------------------------------------------------- #
def test_env_enable_exec_registers_exec_tools_without_explicit_config(monkeypatch):
    # Make load_config explode to prove we do NOT do a full config load at build
    # time -- only the boolean env flag is read to decide registration.
    monkeypatch.setattr(
        server,
        "load_config",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("load_config must not run at build time")
        ),
    )
    monkeypatch.setenv("PROXMOX_ENABLE_EXEC", "true")
    mcp = server.build_server()  # no config -> env-driven deployment path
    names = _tool_names(mcp)
    assert "exec_in_container" in names
    assert "exec_in_vm" in names
    assert len(names) == 29


def test_env_without_enable_exec_stays_at_baseline(monkeypatch):
    monkeypatch.delenv("PROXMOX_ENABLE_EXEC", raising=False)
    monkeypatch.setattr(
        server,
        "load_config",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("load_config must not run at build time")
        ),
    )
    mcp = server.build_server()
    names = _tool_names(mcp)
    assert "exec_in_container" not in names
    assert len(names) == 27
