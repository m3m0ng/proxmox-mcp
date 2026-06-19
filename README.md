# proxmox-mcp

A Python **stdio MCP server for Proxmox VE**. It exposes a curated set of tools
that let an MCP client (e.g. Claude Code) read cluster status, manage guest
power lifecycle, and provision new VMs/containers — **but never delete
anything**.

## Why no delete?

Deletion is human-only **by design**. This matters because Proxmox RBAC cannot
separate "create" from "delete": the `VM.Allocate` privilege required to create
a guest *also* permits destroying one at the API level. There is no role you can
grant that says "create but not delete."

So the no-delete guarantee is enforced **inside this server**, in two layers:

1. **No destructive tools are registered.** There is simply no `delete_vm` (or
   destroy/remove/purge/rollback/shrink/wipe/erase) tool. `build_server()` runs
   `assert_no_destructive_tools()` at startup and refuses to start if one ever
   sneaks in.
2. **A runtime guard wraps the client.** The proxmoxer API object is wrapped in
   a `SafeProxmox` proxy that raises `PermissionError` the instant any `.delete`
   attribute is touched, anywhere in a call chain.

To remove access entirely, revoke the API token in Proxmox (see below).

## Capabilities (~27 tools)

### Tier A — read / status (12, strictly read-only)
`list_nodes`, `node_status`, `cluster_resources`, `list_vms`,
`list_containers`, `vm_status`, `container_status`, `vm_config`,
`container_config`, `list_storage`, `list_templates`, `next_vmid`

### Tier B — lifecycle (8, reversible power state)
`start_vm`, `stop_vm`, `shutdown_vm`, `reboot_vm`,
`start_container`, `stop_container`, `shutdown_container`, `reboot_container`

### Tier C — provision (7, additive: create / clone / config)
`create_container`, `create_vm`, `clone_vm`, `clone_container`,
`set_vm_config`, `set_container_config`, `allocate_vmid`

## Proxmox setup (human, one-time)

Create a dedicated user, a custom role, an ACL assignment, and an API token.

### UI steps (Datacenter view)

1. **Permissions → Users → Add**: user `agent`, realm `pve` → `agent@pve`.
2. **Permissions → Roles → Create**: name `AgentRole`, select privileges:
   - `VM.Audit`, `Sys.Audit`, `Datastore.Audit` (read/status)
   - `VM.PowerMgmt` (start/stop/shutdown/reboot)
   - `VM.Allocate` (create — note: also permits delete at the API; see security note)
   - `VM.Config.Disk`, `VM.Config.CPU`, `VM.Config.Memory`, `VM.Config.Network`,
     `VM.Config.Options` (set config)
   - `VM.Clone` (clone)
   - `Datastore.AllocateSpace` (disk allocation for create/clone)
   - `SDN.Use` — **required** on Proxmox VE 8.x/9.x to attach a guest to *any*
     bridge, including the default `vmbr0` (the API checks
     `/sdn/zones/localnetwork/<bridge>`). Without it, create/clone fails with
     `403 Forbidden: Permission check failed (... SDN.Use)`.
3. **Permissions → Add → Group/User Permission**: path `/` (or a scoped
   pool/path to limit blast radius), user `agent@pve`, role `AgentRole`.
4. **Permissions → API Tokens → Add**: user `agent@pve`, token id `mcp`.
   **Uncheck "Privilege Separation"** (so the token inherits the user's role),
   or leave it checked and grant the token the role explicitly. **Copy the
   secret value now — it is shown only once.**

### `pveum` CLI equivalents

```sh
pveum user add agent@pve
pveum role add AgentRole -privs "VM.Audit Sys.Audit Datastore.Audit VM.PowerMgmt VM.Allocate VM.Config.Disk VM.Config.CPU VM.Config.Memory VM.Config.Network VM.Config.Options VM.Clone Datastore.AllocateSpace SDN.Use"
pveum acl modify / -user agent@pve -role AgentRole
pveum user token add agent@pve mcp --privsep 0
```

The last command prints the token secret once — capture it for
`PROXMOX_TOKEN_VALUE` below.

### Security note

`VM.Allocate` technically **also permits delete** at the Proxmox API, and RBAC
offers no way to grant create without delete. The no-delete guarantee is
therefore provided by **this server** (no delete tools + the runtime guard), not
by Proxmox RBAC. To cut off access at any time, **delete the API token** in
Proxmox (`pveum user token remove agent@pve mcp`).

## Install

```sh
pip install -e .
```

This installs the package and a `proxmox-mcp` console script
(`proxmox_mcp.server:main`). You can also launch it via `python -m proxmox_mcp`.

## Configuration

All connection settings come from environment variables (listed below). Two ways
to supply them:

- **Deployment:** set them in your MCP client's `env` block (see below).
- **Local dev:** copy `.env.example` to `.env` and fill in real values. `.env` is
  git-ignored — **never commit real credentials.** Any variable already set in the
  process environment (e.g. the MCP client's `env` block) overrides the `.env`.

## Register with Claude Code

The server reads its connection settings from environment variables:

| Variable               | Required | Notes                                            |
| ---------------------- | -------- | ------------------------------------------------ |
| `PROXMOX_HOST`         | yes      | Hostname/IP, e.g. `proxmox.lan` (no scheme)      |
| `PROXMOX_USER`         | yes      | e.g. `agent@pve`                                 |
| `PROXMOX_TOKEN_NAME`   | yes      | e.g. `mcp`                                       |
| `PROXMOX_TOKEN_VALUE`  | yes      | the token secret copied during setup             |
| `PROXMOX_VERIFY_SSL`   | no       | `false` for self-signed certs (default `false`)  |
| `PROXMOX_PORT`         | no       | defaults to `8006`                               |

### `.mcp.json` snippet

```json
{
  "mcpServers": {
    "proxmox": {
      "command": "proxmox-mcp",
      "env": {
        "PROXMOX_HOST": "proxmox.lan",
        "PROXMOX_USER": "agent@pve",
        "PROXMOX_TOKEN_NAME": "mcp",
        "PROXMOX_TOKEN_VALUE": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
        "PROXMOX_VERIFY_SSL": "false",
        "PROXMOX_PORT": "8006"
      }
    }
  }
}
```

(If you prefer not to install the console script, use
`"command": "python", "args": ["-m", "proxmox_mcp"]`.)

### `claude mcp add` example

```sh
claude mcp add proxmox \
  --env PROXMOX_HOST=proxmox.lan \
  --env PROXMOX_USER=agent@pve \
  --env PROXMOX_TOKEN_NAME=mcp \
  --env PROXMOX_TOKEN_VALUE=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx \
  --env PROXMOX_VERIFY_SSL=false \
  -- proxmox-mcp
```

## Testing

The test suite is fully offline (proxmoxer is mocked) — no env vars, no network:

```sh
python -m pytest -q
```

A **live smoke test** against a real Proxmox requires the token + host env vars
to be set (`PROXMOX_HOST`, `PROXMOX_USER`, `PROXMOX_TOKEN_NAME`,
`PROXMOX_TOKEN_VALUE`); it is not part of the offline suite.

## Scope (v1)

Provisioning stops at **"guest created / cloned / started."** Running an
application *inside* a guest (cloud-init, SSH, package install, etc.) is a
deferred future tier and is intentionally out of scope for v1.
