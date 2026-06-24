"""Unit tests for proxmox_mcp.tools.provision (offline, no network).

A recording fake stands in for ``proxmoxer.ProxmoxAPI``. It captures the
chained-attribute + call access path proxmoxer uses
(e.g. ``api.nodes(node).lxc.create(...)`` or
``api.nodes(node).qemu(vmid).clone.post(...)``) and returns a result marker so
each provisioning tool can be asserted to hit the expected endpoint with the
expected (None-pruned) kwargs.

These tools are Tier C (provisioning / create). A guard asserts the fake's
``.delete`` is never reached by any provisioning tool: provision.py must contain
no delete/destroy/remove verbs.
"""

import pytest

from mcp.server.fastmcp import FastMCP

from proxmox_mcp.tools import provision


# --------------------------------------------------------------------------- #
# Recording fake api
# --------------------------------------------------------------------------- #
class _FakeApi:
    """Records the proxmoxer-style access path leading to a terminal verb.

    Every attribute access appends ``"<name>"`` to the path and every call
    appends the positional args. Terminal verbs ``.create(**kwargs)``,
    ``.post(**kwargs)``, ``.set(**kwargs)`` and ``.get(**kwargs)`` record the
    full path and kwargs on the shared recorder and return a fabricated result.

    Accessing ``delete`` flips ``recorder["delete_touched"]`` so a test can
    assert no provisioning tool ever reaches a delete/destroy verb.
    """

    _VERBS = {
        "create": "UPID:pve1:00001234:00ABCDEF:60000000:vzcreate:100:root@pam:",
        "post": "UPID:pve1:00005678:00ABCDEF:60000000:qmclone:100:root@pam:",
        "set": None,
        "get": "999",
    }

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
        if name in self._VERBS:
            ret = self._VERBS[name]

            def _verb(**kwargs):
                self._recorder["verb"] = name
                self._recorder["path"] = self._path
                self._recorder["kwargs"] = kwargs
                self._recorder["returned"] = ret
                return ret

            return _verb
        return _FakeApi(self._recorder, self._path + [name])

    def __call__(self, *args):
        return _FakeApi(self._recorder, self._path + [("call", args)])


def _new_fake():
    recorder = {}
    return _FakeApi(recorder, []), recorder


def _tools(fake_api):
    mcp = FastMCP("test")
    provision.register(mcp, lambda: fake_api)
    return {t.name: t for t in mcp._tool_manager.list_tools()}


_TOOL_NAMES = {
    "create_container",
    "create_vm",
    "clone_vm",
    "clone_container",
    "set_vm_config",
    "set_container_config",
    "allocate_vmid",
}


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #
def test_registers_exactly_the_seven_tools():
    fake, _ = _new_fake()
    tools = _tools(fake)
    assert set(tools) == _TOOL_NAMES
    assert len(tools) == 7


def test_every_tool_has_a_docstring_description():
    fake, _ = _new_fake()
    tools = _tools(fake)
    for name, tool in tools.items():
        assert tool.description, f"{name} is missing a description"


# --------------------------------------------------------------------------- #
# create_container
# --------------------------------------------------------------------------- #
def test_create_container_minimal_drops_none_and_defaults_rootfs():
    fake, rec = _new_fake()
    tools = _tools(fake)

    result = tools["create_container"].fn(
        node="pve1",
        vmid=200,
        ostemplate="local:vztmpl/debian-12-standard.tar.zst",
        storage="local-lvm",
    )

    assert rec["verb"] == "create"
    assert rec["path"] == ["nodes", ("call", ("pve1",)), "lxc"]
    kw = rec["kwargs"]
    # hostname is None by default and must be omitted
    assert "hostname" not in kw
    assert kw["vmid"] == 200
    assert kw["ostemplate"] == "local:vztmpl/debian-12-standard.tar.zst"
    assert kw["storage"] == "local-lvm"
    assert kw["cores"] == 1
    assert kw["memory"] == 512
    assert kw["rootfs"] == "local-lvm:8"
    assert kw["net0"] == "name=eth0,bridge=vmbr0,ip=dhcp"
    assert result == rec["returned"]
    assert result.startswith("UPID:")


def test_create_container_forwards_hostname_extra_and_rootfs():
    fake, rec = _new_fake()
    tools = _tools(fake)

    tools["create_container"].fn(
        node="pve1",
        vmid=201,
        ostemplate="local:vztmpl/x.tar.zst",
        storage="local-lvm",
        hostname="ct1",
        rootfs="local-lvm:16",
        cores=4,
        memory=2048,
        unprivileged=1,
    )

    kw = rec["kwargs"]
    assert kw["hostname"] == "ct1"
    assert kw["rootfs"] == "local-lvm:16"
    assert kw["cores"] == 4
    assert kw["memory"] == 2048
    assert kw["unprivileged"] == 1


# --------------------------------------------------------------------------- #
# create_vm
# --------------------------------------------------------------------------- #
def test_create_vm_minimal_drops_none():
    fake, rec = _new_fake()
    tools = _tools(fake)

    result = tools["create_vm"].fn(node="pve1", vmid=300)

    assert rec["verb"] == "create"
    assert rec["path"] == ["nodes", ("call", ("pve1",)), "qemu"]
    kw = rec["kwargs"]
    # name and scsi0 default to None and must be omitted
    assert "name" not in kw
    assert "scsi0" not in kw
    assert kw["vmid"] == 300
    assert kw["cores"] == 1
    assert kw["memory"] == 512
    assert kw["net0"] == "virtio,bridge=vmbr0"
    assert kw["ostype"] == "l26"
    assert result == rec["returned"]


def test_create_vm_forwards_name_scsi0_and_extra():
    fake, rec = _new_fake()
    tools = _tools(fake)

    tools["create_vm"].fn(
        node="pve1",
        vmid=301,
        name="web",
        scsi0="local-lvm:32",
        ide2="local:iso/x.iso,media=cdrom",
    )

    kw = rec["kwargs"]
    assert kw["name"] == "web"
    assert kw["scsi0"] == "local-lvm:32"
    assert kw["ide2"] == "local:iso/x.iso,media=cdrom"


# --------------------------------------------------------------------------- #
# clone_vm / clone_container
# --------------------------------------------------------------------------- #
def test_clone_vm_full_true_maps_to_one_and_drops_none():
    fake, rec = _new_fake()
    tools = _tools(fake)

    result = tools["clone_vm"].fn(
        node="pve1", source_vmid=300, newid=350, storage="local-lvm"
    )

    assert rec["verb"] == "post"
    assert rec["path"] == ["nodes", ("call", ("pve1",)), "qemu", ("call", (300,)), "clone"]
    kw = rec["kwargs"]
    assert kw["newid"] == 350
    assert kw["full"] == 1
    assert kw["storage"] == "local-lvm"
    # name and target are None and must be omitted
    assert "name" not in kw
    assert "target" not in kw
    assert result == rec["returned"]


def test_clone_vm_full_false_maps_to_zero():
    fake, rec = _new_fake()
    tools = _tools(fake)

    tools["clone_vm"].fn(node="pve1", source_vmid=300, newid=351, full=False)

    kw = rec["kwargs"]
    assert kw["full"] == 0
    assert "storage" not in kw


def test_clone_container_full_true_maps_to_one_and_drops_none():
    fake, rec = _new_fake()
    tools = _tools(fake)

    tools["clone_container"].fn(
        node="pve1", source_vmid=200, newid=250, hostname="ct-clone", storage="local-lvm"
    )

    assert rec["verb"] == "post"
    assert rec["path"] == ["nodes", ("call", ("pve1",)), "lxc", ("call", (200,)), "clone"]
    kw = rec["kwargs"]
    assert kw["newid"] == 250
    assert kw["full"] == 1
    assert kw["hostname"] == "ct-clone"
    assert kw["storage"] == "local-lvm"
    assert "target" not in kw


def test_clone_container_full_false_maps_to_zero():
    fake, rec = _new_fake()
    tools = _tools(fake)

    tools["clone_container"].fn(node="pve1", source_vmid=200, newid=251, full=False)

    assert rec["kwargs"]["full"] == 0


@pytest.mark.parametrize(
    "name,call",
    [
        (
            "create_container",
            lambda t: t.fn(
                node="pve1",
                vmid=200,
                ostemplate="local:vztmpl/x",
                storage="local-lvm",
                wait=True,
                wait_timeout=12,
            ),
        ),
        (
            "create_vm",
            lambda t: t.fn(node="pve1", vmid=300, wait=True, wait_timeout=12),
        ),
        (
            "clone_vm",
            lambda t: t.fn(
                node="pve1", source_vmid=300, newid=350, wait=True, wait_timeout=12
            ),
        ),
        (
            "clone_container",
            lambda t: t.fn(
                node="pve1", source_vmid=200, newid=250, wait=True, wait_timeout=12
            ),
        ),
    ],
)
def test_long_running_tools_wait_when_requested(monkeypatch, name, call):
    fake, rec = _new_fake()
    tools = _tools(fake)
    waited = {}

    def fake_wait_for_task(api, node, upid, timeout):
        waited.update({"api": api, "node": node, "upid": upid, "timeout": timeout})
        return {"status": "stopped", "exitstatus": "OK"}

    monkeypatch.setattr(provision, "wait_for_task", fake_wait_for_task)

    result = call(tools[name])

    assert waited == {
        "api": fake,
        "node": "pve1",
        "upid": rec["returned"],
        "timeout": 12,
    }
    assert result == {
        "status": "stopped",
        "exitstatus": "OK",
        "upid": rec["returned"],
        "node": "pve1",
        "success": True,
        "warnings": False,
    }
    assert "wait" not in rec["kwargs"]
    assert "wait_timeout" not in rec["kwargs"]


def test_wait_timeout_error_includes_upid(monkeypatch):
    fake, rec = _new_fake()
    tools = _tools(fake)

    def fake_wait_for_task(api, node, upid, timeout):
        raise TimeoutError(f"Task {upid} timed out")

    monkeypatch.setattr(provision, "wait_for_task", fake_wait_for_task)

    with pytest.raises(TimeoutError, match="UPID:pve1"):
        tools["create_vm"].fn(node="pve1", vmid=300, wait=True, wait_timeout=1)


# --------------------------------------------------------------------------- #
# set_vm_config / set_container_config
# --------------------------------------------------------------------------- #
def test_set_vm_config_puts_config():
    fake, rec = _new_fake()
    tools = _tools(fake)

    tools["set_vm_config"].fn(node="pve1", vmid=300, memory=4096, cores=8)

    assert rec["verb"] == "set"
    assert rec["path"] == ["nodes", ("call", ("pve1",)), "qemu", ("call", (300,)), "config"]
    assert rec["kwargs"] == {"memory": 4096, "cores": 8}


def test_set_vm_config_empty_raises():
    fake, _ = _new_fake()
    tools = _tools(fake)
    with pytest.raises(ValueError):
        tools["set_vm_config"].fn(node="pve1", vmid=300)


def test_set_container_config_puts_config():
    fake, rec = _new_fake()
    tools = _tools(fake)

    tools["set_container_config"].fn(node="pve1", vmid=200, memory=1024)

    assert rec["verb"] == "set"
    assert rec["path"] == ["nodes", ("call", ("pve1",)), "lxc", ("call", (200,)), "config"]
    assert rec["kwargs"] == {"memory": 1024}


def test_set_container_config_empty_raises():
    fake, _ = _new_fake()
    tools = _tools(fake)
    with pytest.raises(ValueError):
        tools["set_container_config"].fn(node="pve1", vmid=200)


# --------------------------------------------------------------------------- #
# allocate_vmid
# --------------------------------------------------------------------------- #
def test_allocate_vmid_hits_cluster_nextid():
    fake, rec = _new_fake()
    tools = _tools(fake)

    result = tools["allocate_vmid"].fn()

    assert rec["verb"] == "get"
    assert rec["path"] == ["cluster", "nextid"]
    assert result == rec["returned"]


# --------------------------------------------------------------------------- #
# No-delete guard + lazy wiring
# --------------------------------------------------------------------------- #
def test_drop_none_helper():
    assert provision._drop_none({"a": 1, "b": None, "c": 0}) == {"a": 1, "c": 0}


@pytest.mark.parametrize(
    "name,call",
    [
        ("create_container", lambda t: t.fn(node="pve1", vmid=200, ostemplate="local:vztmpl/x", storage="s")),
        ("create_vm", lambda t: t.fn(node="pve1", vmid=300)),
        ("clone_vm", lambda t: t.fn(node="pve1", source_vmid=300, newid=350, storage="s")),
        ("clone_container", lambda t: t.fn(node="pve1", source_vmid=200, newid=250, storage="s")),
        ("set_vm_config", lambda t: t.fn(node="pve1", vmid=300, memory=1)),
        ("set_container_config", lambda t: t.fn(node="pve1", vmid=200, memory=1)),
        ("allocate_vmid", lambda t: t.fn()),
    ],
)
def test_no_tool_touches_delete(name, call):
    fake, rec = _new_fake()
    tools = _tools(fake)
    call(tools[name])
    assert "delete_touched" not in rec
    assert "delete_called" not in rec


def test_module_source_has_no_destructive_verbs():
    import inspect

    src = inspect.getsource(provision)
    for bad in ("delete", "destroy", ".remove", "rollback"):
        assert bad not in src, f"provision.py must not reference {bad!r}"


def test_get_api_called_each_invocation():
    fake, _ = _new_fake()
    count = {"n": 0}

    def get_api():
        count["n"] += 1
        return fake

    mcp = FastMCP("test")
    provision.register(mcp, get_api)
    tools = {t.name: t for t in mcp._tool_manager.list_tools()}

    tools["allocate_vmid"].fn()
    tools["set_vm_config"].fn(node="pve1", vmid=300, memory=1)
    assert count["n"] == 2
