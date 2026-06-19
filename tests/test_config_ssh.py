"""Unit tests for SSH + enable-exec config settings (pure, no network)."""

import pytest

from proxmox_mcp.config import Config, load_config


def _full_env(**overrides):
    env = {
        "PROXMOX_HOST": "10.0.0.10",
        "PROXMOX_USER": "agent@pve",
        "PROXMOX_TOKEN_NAME": "mcp",
        "PROXMOX_TOKEN_VALUE": "supersecret",
    }
    env.update(overrides)
    return env


def test_ssh_defaults_when_absent():
    cfg = load_config(_full_env())
    assert isinstance(cfg, Config)
    assert cfg.enable_exec is False
    assert cfg.ssh_user == "root"
    assert cfg.ssh_port == 22
    # ssh_host defaults to the parsed Proxmox host (no port).
    assert cfg.ssh_host == "10.0.0.10"
    assert cfg.ssh_key_file is None
    assert cfg.ssh_password is None


def test_ssh_host_defaults_to_parsed_host_without_port():
    cfg = load_config(_full_env(PROXMOX_HOST="10.0.0.10:9999"))
    # The host's explicit port is split off; ssh_host should not include it.
    assert cfg.host == "10.0.0.10"
    assert cfg.ssh_host == "10.0.0.10"


@pytest.mark.parametrize("value", ["true", "True", "TRUE", "1", "yes", "YES", " yes "])
def test_enable_exec_truthy(value):
    cfg = load_config(_full_env(PROXMOX_ENABLE_EXEC=value))
    assert cfg.enable_exec is True


@pytest.mark.parametrize("value", ["false", "0", "no", "", "anything", "False"])
def test_enable_exec_falsy(value):
    cfg = load_config(_full_env(PROXMOX_ENABLE_EXEC=value))
    assert cfg.enable_exec is False


def test_ssh_overrides_parsed():
    cfg = load_config(
        _full_env(
            PROXMOX_SSH_HOST="ssh.example.com",
            PROXMOX_SSH_PORT="2222",
            PROXMOX_SSH_USER="ops",
        )
    )
    assert cfg.ssh_host == "ssh.example.com"
    assert cfg.ssh_port == 2222
    assert isinstance(cfg.ssh_port, int)
    assert cfg.ssh_user == "ops"


def test_empty_ssh_host_falls_back_to_host():
    cfg = load_config(_full_env(PROXMOX_SSH_HOST="  "))
    assert cfg.ssh_host == "10.0.0.10"


def test_ssh_target_with_key_only():
    cfg = load_config(_full_env(PROXMOX_SSH_KEY_FILE="/home/ops/.ssh/id_ed25519"))
    target = cfg.ssh_target()
    assert target == {
        "hostname": "10.0.0.10",
        "port": 22,
        "username": "root",
        "key_filename": "/home/ops/.ssh/id_ed25519",
    }
    assert "password" not in target


def test_ssh_target_with_password_only():
    cfg = load_config(_full_env(PROXMOX_SSH_PASSWORD="hunter2"))
    target = cfg.ssh_target()
    assert target == {
        "hostname": "10.0.0.10",
        "port": 22,
        "username": "root",
        "password": "hunter2",
    }
    assert "key_filename" not in target


def test_ssh_target_prefers_key_when_both_set():
    cfg = load_config(
        _full_env(
            PROXMOX_SSH_KEY_FILE="/home/ops/.ssh/id_ed25519",
            PROXMOX_SSH_PASSWORD="hunter2",
        )
    )
    target = cfg.ssh_target()
    assert target["key_filename"] == "/home/ops/.ssh/id_ed25519"
    assert "password" not in target


def test_ssh_target_with_neither_set():
    cfg = load_config(_full_env())
    target = cfg.ssh_target()
    assert target == {
        "hostname": "10.0.0.10",
        "port": 22,
        "username": "root",
    }
    assert "key_filename" not in target
    assert "password" not in target


@pytest.mark.parametrize(
    "missing",
    ["PROXMOX_HOST", "PROXMOX_USER", "PROXMOX_TOKEN_NAME", "PROXMOX_TOKEN_VALUE"],
)
def test_required_validation_unchanged(missing):
    # Exec settings are opt-in and must not affect required-var validation.
    env = _full_env(PROXMOX_ENABLE_EXEC="true")
    del env[missing]
    with pytest.raises(ValueError) as exc:
        load_config(env)
    assert missing in str(exc.value)


def test_to_proxmoxer_kwargs_excludes_ssh():
    cfg = load_config(
        _full_env(
            PROXMOX_ENABLE_EXEC="true",
            PROXMOX_SSH_HOST="ssh.example.com",
            PROXMOX_SSH_KEY_FILE="/k",
        )
    )
    kwargs = cfg.to_proxmoxer_kwargs()
    assert set(kwargs) == {
        "host",
        "user",
        "token_name",
        "token_value",
        "verify_ssl",
        "port",
    }
