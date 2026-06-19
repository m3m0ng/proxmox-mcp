"""Configuration loading for proxmox_mcp.

Reads Proxmox connection settings from environment variables and exposes them
as a typed :class:`Config` dataclass plus a helper that produces the keyword
arguments expected by :class:`proxmoxer.ProxmoxAPI`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

__all__ = ["Config", "load_config", "parse_dotenv"]

_TRUTHY = {"true", "1", "yes"}

_DEFAULT_PORT = 8006
_DEFAULT_VERIFY_SSL = "false"

_DOTENV_FILENAME = ".env"


def _parse_bool(value: str) -> bool:
    """Parse a string into a bool: 'true'/'1'/'yes' (any case) -> True."""
    return value.strip().lower() in _TRUTHY


def parse_dotenv(text: str) -> dict[str, str]:
    """Parse ``.env`` file contents into a dict of ``KEY -> value``.

    Supports ``KEY=value`` lines, ``#`` comments, blank lines, an optional
    leading ``export``, and surrounding single/double quotes on the value.
    Lines without ``=`` are ignored. No interpolation is performed.
    """
    result: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        result[key] = value
    return result


def _effective_environ() -> Mapping[str, str]:
    """Merge an optional ``.env`` in the cwd under the real ``os.environ``.

    Values already present in ``os.environ`` (e.g. the MCP client's ``env``
    block) always take precedence over the ``.env`` file. ``os.environ`` is
    never mutated.
    """
    merged: dict[str, str] = {}
    path = os.path.join(os.getcwd(), _DOTENV_FILENAME)
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            merged.update(parse_dotenv(fh.read()))
    merged.update(os.environ)
    return merged


@dataclass
class Config:
    """Proxmox connection configuration."""

    host: str
    user: str
    token_name: str
    token_value: str
    verify_ssl: bool = False
    port: int = _DEFAULT_PORT

    def to_proxmoxer_kwargs(self) -> dict:
        """Return kwargs for ``proxmoxer.ProxmoxAPI(...)``.

        The ``host`` never includes the port; the port is passed separately.
        """
        return {
            "host": self.host,
            "user": self.user,
            "token_name": self.token_name,
            "token_value": self.token_value,
            "verify_ssl": self.verify_ssl,
            "port": self.port,
        }


def load_config(env: Mapping[str, str] | None = None) -> Config:
    """Load :class:`Config` from a mapping of environment variables.

    When ``env`` is ``None``, reads from ``os.environ`` merged over an optional
    ``.env`` file in the current directory (``os.environ`` wins). This keeps
    real secrets in a git-ignored ``.env`` for local use while letting an MCP
    client's ``env`` block override them in deployment.

    Raises:
        ValueError: if any required variable is missing or empty. The message
            names every offending variable.
    """
    if env is None:
        env = _effective_environ()

    required = (
        "PROXMOX_HOST",
        "PROXMOX_USER",
        "PROXMOX_TOKEN_NAME",
        "PROXMOX_TOKEN_VALUE",
    )
    missing = [name for name in required if not (env.get(name) or "").strip()]
    if missing:
        raise ValueError(
            "Missing required environment variable(s): " + ", ".join(missing)
        )

    host = env["PROXMOX_HOST"].strip()

    port_raw = (env.get("PROXMOX_PORT") or "").strip()
    port = int(port_raw) if port_raw else _DEFAULT_PORT

    # If the host carries an explicit ":port", split it off and prefer it.
    if ":" in host:
        host_part, _, port_part = host.rpartition(":")
        if host_part and port_part:
            host = host_part
            port = int(port_part)

    verify_ssl = _parse_bool(env.get("PROXMOX_VERIFY_SSL", _DEFAULT_VERIFY_SSL))

    return Config(
        host=host,
        user=env["PROXMOX_USER"].strip(),
        token_name=env["PROXMOX_TOKEN_NAME"].strip(),
        token_value=env["PROXMOX_TOKEN_VALUE"],
        verify_ssl=verify_ssl,
        port=port,
    )
