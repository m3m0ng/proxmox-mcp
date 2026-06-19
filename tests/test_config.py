"""Unit tests for proxmox_mcp.config (pure, no network)."""

import pytest

from proxmox_mcp.config import Config, load_config, parse_dotenv


def _full_env(**overrides):
    env = {
        "PROXMOX_HOST": "10.0.0.10",
        "PROXMOX_USER": "agent@pve",
        "PROXMOX_TOKEN_NAME": "mcp",
        "PROXMOX_TOKEN_VALUE": "supersecret",
        "PROXMOX_VERIFY_SSL": "false",
        "PROXMOX_PORT": "8006",
    }
    env.update(overrides)
    return env


def test_load_config_parses_full_env():
    cfg = load_config(_full_env())
    assert isinstance(cfg, Config)
    assert cfg.host == "10.0.0.10"
    assert cfg.user == "agent@pve"
    assert cfg.token_name == "mcp"
    assert cfg.token_value == "supersecret"
    assert cfg.verify_ssl is False
    assert cfg.port == 8006


def test_defaults_when_optional_absent():
    env = _full_env()
    del env["PROXMOX_VERIFY_SSL"]
    del env["PROXMOX_PORT"]
    cfg = load_config(env)
    assert cfg.verify_ssl is False
    assert cfg.port == 8006


@pytest.mark.parametrize("value", ["true", "True", "TRUE", "1", "yes", "YES", " yes "])
def test_verify_ssl_truthy(value):
    cfg = load_config(_full_env(PROXMOX_VERIFY_SSL=value))
    assert cfg.verify_ssl is True


@pytest.mark.parametrize("value", ["false", "0", "no", "", "anything", "False"])
def test_verify_ssl_falsy(value):
    cfg = load_config(_full_env(PROXMOX_VERIFY_SSL=value))
    assert cfg.verify_ssl is False


@pytest.mark.parametrize(
    "missing",
    ["PROXMOX_HOST", "PROXMOX_USER", "PROXMOX_TOKEN_NAME", "PROXMOX_TOKEN_VALUE"],
)
def test_missing_required_raises_naming_var(missing):
    env = _full_env()
    del env[missing]
    with pytest.raises(ValueError) as exc:
        load_config(env)
    assert missing in str(exc.value)


def test_empty_required_raises_naming_var():
    env = _full_env(PROXMOX_TOKEN_VALUE="")
    with pytest.raises(ValueError) as exc:
        load_config(env)
    assert "PROXMOX_TOKEN_VALUE" in str(exc.value)


def test_missing_multiple_required_lists_all():
    env = _full_env()
    del env["PROXMOX_HOST"]
    del env["PROXMOX_USER"]
    with pytest.raises(ValueError) as exc:
        load_config(env)
    msg = str(exc.value)
    assert "PROXMOX_HOST" in msg
    assert "PROXMOX_USER" in msg


def test_to_proxmoxer_kwargs():
    cfg = load_config(_full_env(PROXMOX_VERIFY_SSL="true", PROXMOX_PORT="8006"))
    kwargs = cfg.to_proxmoxer_kwargs()
    assert kwargs == {
        "host": "10.0.0.10",
        "user": "agent@pve",
        "token_name": "mcp",
        "token_value": "supersecret",
        "verify_ssl": True,
        "port": 8006,
    }
    assert isinstance(kwargs["verify_ssl"], bool)
    assert isinstance(kwargs["port"], int)


def test_host_with_port_is_split_and_preferred():
    env = _full_env(PROXMOX_HOST="10.0.0.10:9999")
    # PROXMOX_PORT present but host port should win
    cfg = load_config(env)
    assert cfg.host == "10.0.0.10"
    assert cfg.port == 9999
    kwargs = cfg.to_proxmoxer_kwargs()
    assert kwargs["host"] == "10.0.0.10"
    assert kwargs["port"] == 9999


# --------------------------------------------------------------------------- #
# .env dev fallback
# --------------------------------------------------------------------------- #


def test_parse_dotenv_basic_comments_quotes_and_export():
    text = (
        "# a comment\n"
        "\n"
        "PROXMOX_HOST=10.0.0.10\n"
        "export PROXMOX_USER=agent@pve\n"
        'PROXMOX_TOKEN_VALUE="quoted-secret"\n'
        "PROXMOX_TOKEN_NAME='mcp'\n"
        "NOT_A_PAIR\n"
        "EMPTY=\n"
    )
    parsed = parse_dotenv(text)
    assert parsed["PROXMOX_HOST"] == "10.0.0.10"
    assert parsed["PROXMOX_USER"] == "agent@pve"
    assert parsed["PROXMOX_TOKEN_VALUE"] == "quoted-secret"
    assert parsed["PROXMOX_TOKEN_NAME"] == "mcp"
    assert parsed["EMPTY"] == ""
    assert "NOT_A_PAIR" not in parsed


def test_load_config_reads_dotenv_when_env_is_none(tmp_path, monkeypatch):
    # Isolate: empty os.environ + cwd pointing at a temp dir holding a .env.
    monkeypatch.chdir(tmp_path)
    for var in (
        "PROXMOX_HOST",
        "PROXMOX_USER",
        "PROXMOX_TOKEN_NAME",
        "PROXMOX_TOKEN_VALUE",
        "PROXMOX_VERIFY_SSL",
        "PROXMOX_PORT",
    ):
        monkeypatch.delenv(var, raising=False)
    (tmp_path / ".env").write_text(
        "PROXMOX_HOST=192.168.1.5\n"
        "PROXMOX_USER=agent@pve\n"
        "PROXMOX_TOKEN_NAME=mcp\n"
        "PROXMOX_TOKEN_VALUE=from-dotenv\n",
        encoding="utf-8",
    )
    cfg = load_config()  # env is None -> should pick up the .env
    assert cfg.host == "192.168.1.5"
    assert cfg.token_value == "from-dotenv"


def test_os_environ_overrides_dotenv(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "PROXMOX_HOST=from-dotenv\n"
        "PROXMOX_USER=agent@pve\n"
        "PROXMOX_TOKEN_NAME=mcp\n"
        "PROXMOX_TOKEN_VALUE=dotenv-secret\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PROXMOX_HOST", "from-os-environ")
    monkeypatch.setenv("PROXMOX_USER", "agent@pve")
    monkeypatch.setenv("PROXMOX_TOKEN_NAME", "mcp")
    monkeypatch.setenv("PROXMOX_TOKEN_VALUE", "os-secret")
    cfg = load_config()
    # os.environ wins over the .env file
    assert cfg.host == "from-os-environ"
    assert cfg.token_value == "os-secret"
