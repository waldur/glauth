"""Unit tests for the pure helpers of the GLAuth config refresher."""

import hashlib
import pathlib

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib

PRECONFIG = (
    pathlib.Path(__file__).resolve().parent.parent
    / "refresher"
    / "preconfig.cfg.template"
)

ADMIN_ENV = {
    "LDAP_ADMIN_USERNAME": "serviceuser",
    "LDAP_ADMIN_UIDNUMBER": "5003",
    "LDAP_ADMIN_EMAIL": "serviceuser@example.com",
    "LDAP_ADMIN_PGROUP": "5502",
}


def test_generate_password_digest(refresher):
    assert (
        refresher.generate_password_digest("passw0rd")
        == hashlib.sha256(b"passw0rd").hexdigest()
    )


def test_template_preconfig_renders_valid_toml(refresher, monkeypatch):
    monkeypatch.setattr(refresher, "TEMPLATE_PATH", str(PRECONFIG))
    rendered = refresher.template_preconfig(ADMIN_ENV, "deadbeef")

    # A stray ``$`` in the template would have raised; nothing must be left
    # unsubstituted either.
    assert "${" not in rendered
    assert 'name = "serviceuser"' in rendered
    assert 'passsha256 = "deadbeef"' in rendered

    # The rendered preconfig must be valid TOML on its own.
    parsed = tomllib.loads(rendered)
    assert parsed["users"][0]["name"] == "serviceuser"
    assert parsed["users"][0]["uidnumber"] == 5003
    assert parsed["groups"][0]["gidnumber"] == 5502


def test_read_config_requires_all_vars(refresher, monkeypatch):
    for var in refresher.REQUIRED_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(SystemExit):
        refresher.read_config()


def test_read_config_collects_env(refresher, monkeypatch):
    for var in refresher.REQUIRED_ENV_VARS:
        monkeypatch.setenv(var, "value")
    monkeypatch.setenv("WALDUR_STOMP_WS_PORT", "15674")
    config = refresher.read_config()
    assert set(refresher.REQUIRED_ENV_VARS) <= set(config)
    # Optional vars are carried through when present.
    assert config["WALDUR_STOMP_WS_PORT"] == "15674"
