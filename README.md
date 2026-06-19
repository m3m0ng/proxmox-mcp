# proxmox-mcp

A Python **[MCP](https://modelcontextprotocol.io) server for [Proxmox VE](https://www.proxmox.com/en/proxmox-virtual-environment/overview)**.
It lets AI coding agents (Claude Code, Claude Desktop, Cursor, or any
MCP-compatible client) **see and operate your Proxmox homelab through natural
language** — read cluster status, power guests on and off, and provision new
VMs/containers — **but never delete anything.** Deletion stays a human-only
action, by design.

> Ask your agent *"what's running on my Proxmox?"*, *"spin up a Debian container
> for a test"*, or *"reboot the docker VM"* — and it just works, safely.

---

## Table of contents

- [What is this?](#what-is-this)
- [How it works](#how-it-works)
- [Capabilities](#capabilities)
- [The no-delete guarantee](#the-no-delete-guarantee)
- [Quick start](#quick-start)
  - [1. Set up Proxmox (one-time)](#1-set-up-proxmox-one-time)
  - [2. Install](#2-install)
  - [3. Configure](#3-configure)
  - [4. Register with your MCP client](#4-register-with-your-mcp-client)
- [Optional: in-guest command execution](#optional-in-guest-command-execution)
- [Usage examples](#usage-examples)
- [Testing](#testing)
- [Security model](#security-model)
- [Roadmap](#roadmap)

---

## What is this?

The [Model Context Protocol (MCP)](https://modelcontextprotocol.io) is an open
standard that lets AI assistants call external tools. This project is an MCP
**server**: a small program your AI client launches, which then talks to your
Proxmox host's REST API on the agent's behalf.

The design goal is **safe delegation**: give an agent enough power to be useful
(observe, operate, deploy) while making destructive actions structurally
impossible. There is no "delete VM" tool here, and a runtime guard blocks any
destructive API call even if one were added by mistake.

## How it works

```
┌──────────────────┐   stdio    ┌──────────────────┐   HTTPS    ┌──────────────────┐
│  AI client       │ ─────────► │  proxmox-mcp     │ ─────────► │  Proxmox VE      │
│ (Claude Code,    │            │  (this server)   │  API token │  (your homelab)  │
│  Cursor, …)      │ ◄───────── │                  │ ◄───────── │                  │
└──────────────────┘   tools    └──────────────────┘   JSON     └──────────────────┘
```

- Runs **on your machine** (wherever the AI client runs), not on the Proxmox box.
- Communicates with the client over **stdio**, and with Proxmox over **HTTPS**
  using an **API token** (no passwords, easy to revoke).
- Tolerates the **self-signed certificate** a default Proxmox install ships with.

## Capabilities

**~27 tools** across three always-on tiers, plus an optional fourth:

### Tier A — read / status *(12, strictly read-only)*
`list_nodes` · `node_status` · `cluster_resources` · `list_vms` ·
`list_containers` · `vm_status` · `container_status` · `vm_config` ·
`container_config` · `list_storage` · `list_templates` · `next_vmid`

### Tier B — lifecycle *(8, reversible power state)*
`start_vm` · `stop_vm` · `shutdown_vm` · `reboot_vm` ·
`start_container` · `stop_container` · `shutdown_container` · `reboot_container`

### Tier C — provision *(7, additive: create / clone / configure)*
`create_container` · `create_vm` · `clone_vm` · `clone_container` ·
`set_vm_config` · `set_container_config` · `allocate_vmid`

### Tier D′ — in-guest exec *(2, opt-in, off by default)*
`exec_in_container` · `exec_in_vm` — run commands **inside** a guest (e.g.
`apt-get update`, install/start an app). Disabled unless you explicitly turn it
on; see [Optional: in-guest command execution](#optional-in-guest-command-execution).

### Never present: delete / destroy
There is no tool to delete, destroy, remove, purge, roll back, shrink, wipe, or
erase anything. That is intentional — see below.

## The no-delete guarantee

Deletion is human-only **by design**. This matters because Proxmox RBAC **cannot
separate "create" from "delete"**: the `VM.Allocate` privilege required to create
a guest *also* permits destroying one at the API level. There is no role you can
grant that means "create but not delete."

So the guarantee is enforced **inside this server**, in two independent layers:

1. **No destructive tools exist.** `build_server()` runs
   `assert_no_destructive_tools()` at startup and refuses to start if a tool name
   matching a destructive verb is ever registered.
2. **A runtime guard wraps the client.** The proxmoxer API object is wrapped in a
   `SafeProxmox` proxy that raises `PermissionError` the instant any `.delete` is
   touched, anywhere in a call chain.

To cut off access entirely, **revoke the API token** in Proxmox.

---

## Quick start

### 1. Set up Proxmox (one-time)

Create a dedicated user, a least-privilege role, an ACL assignment, and an API
token. The fastest path is the `pveum` CLI on your Proxmox host:

```sh
# 1. a dedicated user
pveum user add agent@pve

# 2. a least-privilege role (read + power + provision; NO admin)
pveum role add AgentRole -privs "VM.Audit Sys.Audit Datastore.Audit VM.PowerMgmt VM.Allocate VM.Config.Disk VM.Config.CPU VM.Config.Memory VM.Config.Network VM.Config.Options VM.Clone Datastore.AllocateSpace SDN.Use"

# 3. grant the role (use a pool path instead of / to limit blast radius)
pveum acl modify / -user agent@pve -role AgentRole

# 4. create the API token — prints the secret ONCE; copy it
pveum user token add agent@pve mcp --privsep 0
```

> **Note on `SDN.Use`:** it is **required** on Proxmox VE 8.x/9.x to attach a
> guest to *any* bridge, including the default `vmbr0` (the API checks
> `/sdn/zones/localnetwork/<bridge>`). Without it, create/clone fails with
> `403 Forbidden: Permission check failed (... SDN.Use)`.

Prefer the web UI? Datacenter → Permissions → **Users** (add `agent@pve`) →
**Roles** (create `AgentRole` with the privileges above) → **Add → User
Permission** (path `/`, user `agent@pve`, role `AgentRole`) → **API Tokens**
(add token `mcp`, uncheck *Privilege Separation*, copy the secret).

### 2. Install

```sh
pip install -e .
```

This installs the package and a `proxmox-mcp` console script. You can also launch
it with `python -m proxmox_mcp`. Requires Python 3.11+.

### 3. Configure

All settings come from environment variables. There are two ways to supply them:

- **Deployment:** set them in your MCP client's `env` block (see step 4).
- **Local dev / testing:** copy `.env.example` to `.env` and fill in real values.
  `.env` is git-ignored — **never commit real credentials.** Anything set in the
  actual process environment (e.g. the client's `env` block) overrides `.env`.

| Variable              | Required | Default  | Notes                                       |
| --------------------- | -------- | -------- | ------------------------------------------- |
| `PROXMOX_HOST`        | ✅       | —        | Hostname/IP, e.g. `proxmox.lan` (no scheme) |
| `PROXMOX_USER`        | ✅       | —        | e.g. `agent@pve`                            |
| `PROXMOX_TOKEN_NAME`  | ✅       | —        | e.g. `mcp`                                  |
| `PROXMOX_TOKEN_VALUE` | ✅       | —        | the token secret copied during setup        |
| `PROXMOX_VERIFY_SSL`  |          | `false`  | `false` for self-signed certs               |
| `PROXMOX_PORT`        |          | `8006`   | Proxmox API port                            |

(In-guest exec adds more variables — see
[that section](#optional-in-guest-command-execution).)

### 4. Register with your MCP client

**`.mcp.json`** (Claude Code project config, Cursor, etc.):

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
        "PROXMOX_VERIFY_SSL": "false"
      }
    }
  }
}
```

(No console script? Use `"command": "python", "args": ["-m", "proxmox_mcp"]`.)

**`claude mcp add`** (Claude Code CLI):

```sh
claude mcp add proxmox \
  --env PROXMOX_HOST=proxmox.lan \
  --env PROXMOX_USER=agent@pve \
  --env PROXMOX_TOKEN_NAME=mcp \
  --env PROXMOX_TOKEN_VALUE=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx \
  --env PROXMOX_VERIFY_SSL=false \
  -- proxmox-mcp
```

Restart the client, then ask it *"list my Proxmox nodes"* to confirm.

---

## Optional: in-guest command execution

To let an agent run commands **inside** a guest (the second half of "deploy an
application" — e.g. `apt-get update`, install and start a service), enable the
**Tier D′** exec tools. They are **off by default**.

Exec works by SSHing to the **Proxmox host** and running `pct exec` (LXC) or
`qm guest exec` (VM, requires the QEMU guest agent). Every argument is
`shlex`-quoted so it cannot break out into the host shell, and there is **no raw
host-shell tool** — only guest-scoped execution.

⚠️ **Security:** this is a real privilege grant. An agent with exec enabled can
run arbitrary commands **as root inside any guest**, reachable via the SSH
credential you configure. Only enable it if you trust the agent with that, and
prefer a dedicated SSH key with access limited to what you need.

Set these additional variables:

| Variable               | Required for exec | Default        | Notes                                      |
| ---------------------- | ----------------- | -------------- | ------------------------------------------ |
| `PROXMOX_ENABLE_EXEC`  | ✅ (`true`)        | `false`        | Master switch; registers the exec tools    |
| `PROXMOX_SSH_HOST`     |                   | `PROXMOX_HOST` | Host to SSH into (defaults to the PVE host) |
| `PROXMOX_SSH_PORT`     |                   | `22`           | SSH port                                   |
| `PROXMOX_SSH_USER`     |                   | `root`         | SSH user (must be able to run `pct`/`qm`)  |
| `PROXMOX_SSH_KEY_FILE` |                   | —              | Path to a private key (preferred)          |
| `PROXMOX_SSH_PASSWORD` |                   | —              | Used only if no key file is given          |

Example `env` additions:

```json
"PROXMOX_ENABLE_EXEC": "true",
"PROXMOX_SSH_USER": "root",
"PROXMOX_SSH_KEY_FILE": "/home/you/.ssh/proxmox_agent"
```

With exec enabled the server exposes **29 tools** (27 baseline + 2 exec).

---

## Usage examples

Once registered, you talk to your agent in plain language; it picks the tools.

| You say… | Tools the agent uses |
| --- | --- |
| "What's running on my Proxmox?" | `list_nodes`, `list_vms`, `list_containers` |
| "How much RAM is free on node pve?" | `node_status` |
| "Reboot the docker VM (id 102)." | `reboot_vm` |
| "Create a Debian 12 container, 2 cores, 2 GB, on vmbr0." | `list_templates`, `allocate_vmid`, `create_container`, `start_container` |
| "Clone template 9000 into a new VM and start it." | `clone_vm`, `start_vm` |
| "Install nginx in container 250." *(exec enabled)* | `exec_in_container` |
| "Delete that test VM." | ❌ refused — deletion is human-only |

## Testing

The test suite is **fully offline** — proxmoxer and paramiko are mocked, so no
env vars and no network are needed:

```sh
python -m pytest -q
```

A **live smoke test** against a real Proxmox is not part of the offline suite; it
just needs the env vars set so the server can connect.

## Security model

- **Auth:** API token only (no passwords); scope it with a least-privilege role
  and, ideally, a pool-scoped ACL. Revoke instantly with
  `pveum user token remove agent@pve mcp`.
- **No deletes:** enforced in-server (no destructive tools + runtime guard),
  because Proxmox RBAC cannot express "create but not delete."
- **Secrets:** live in env vars or a git-ignored `.env`; never in source or argv.
- **Exec is opt-in** and, when on, scoped to guest commands via `pct`/`qm` with
  shell-safe quoting — no arbitrary host shell.

## Roadmap

- ✅ Tier A/B/C (read, lifecycle, provision) — no-delete guaranteed
- ✅ Tier D′ in-guest exec (opt-in)
- ⏳ cloud-init provisioning helpers for VMs
- ⏳ file push/pull into guests for app deployment

---

*Built with [proxmoxer](https://github.com/proxmoxer/proxmoxer) and the official
[MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk).*
