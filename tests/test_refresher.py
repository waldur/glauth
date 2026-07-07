"""Unit tests for the pure helpers of the GLAuth config refresher."""

import hashlib
import pathlib

import pytest
import tomli_w

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


# The Waldur API serialises the export with tomli_w: a leading top-level inline
# ``groups`` array followed by the ``[[users]]`` records.
API_USERS_CONFIG = (
    "groups = [\n"
    '    { name = "alice", gidnumber = 8001 },\n'
    '    { name = "8501", gidnumber = 8501 },\n'
    "]\n\n"
    "[[users]]\n"
    'name = "alice"\n'
    "uidnumber = 7001\n"
    "primarygroup = 8001\n"
)


def test_merge_configs_preserves_api_groups(refresher, monkeypatch):
    monkeypatch.setattr(refresher, "TEMPLATE_PATH", str(PRECONFIG))
    preconfig = refresher.template_preconfig(ADMIN_ENV, "deadbeef")

    merged = refresher.merge_configs(preconfig, API_USERS_CONFIG)
    parsed = tomllib.loads(merged)

    # Preconfig admin and the API user coexist in one document.
    assert {"serviceuser", "alice"} <= {u["name"] for u in parsed["users"]}

    gids = {g["gidnumber"] for g in parsed["groups"]}
    # Admin group plus both API groups survive — in particular the standalone
    # project group (8501), which the old text concatenation silently dropped.
    assert {5502, 8001, 8501} <= gids


def test_merge_configs_handles_empty_users_config(refresher, monkeypatch):
    monkeypatch.setattr(refresher, "TEMPLATE_PATH", str(PRECONFIG))
    preconfig = refresher.template_preconfig(ADMIN_ENV, "deadbeef")

    merged = refresher.merge_configs(preconfig, "")
    parsed = tomllib.loads(merged)
    assert parsed["users"][0]["name"] == "serviceuser"


# A second offering's export, with a non-overlapping uid/gid range.
API_USERS_CONFIG_2 = (
    "groups = [\n"
    '    { name = "bob", gidnumber = 9002 },\n'
    "]\n\n"
    "[[users]]\n"
    'name = "bob"\n'
    "uidnumber = 9002\n"
    "primarygroup = 9002\n"
)


def test_parse_offering_uuids_single(refresher):
    assert refresher.parse_offering_uuids({"WALDUR_OFFERING_UUID": "abc123"}) == [
        "abc123"
    ]


def test_parse_offering_uuids_list_trims_and_dedupes(refresher):
    result = refresher.parse_offering_uuids({"WALDUR_OFFERING_UUID": " a , b ,, a ,c "})
    # Whitespace stripped, empty entries dropped, duplicates collapsed, order kept.
    assert result == ["a", "b", "c"]


def test_parse_offering_uuids_empty_exits(refresher):
    with pytest.raises(SystemExit):
        refresher.parse_offering_uuids({"WALDUR_OFFERING_UUID": " , "})


def test_merge_configs_multiple_offerings(refresher, monkeypatch):
    monkeypatch.setattr(refresher, "TEMPLATE_PATH", str(PRECONFIG))
    preconfig = refresher.template_preconfig(ADMIN_ENV, "deadbeef")

    merged = refresher.merge_configs(preconfig, [API_USERS_CONFIG, API_USERS_CONFIG_2])
    parsed = tomllib.loads(merged)

    # Users from both offerings plus the admin coexist in one directory.
    assert {"serviceuser", "alice", "bob"} <= {u["name"] for u in parsed["users"]}
    gids = {g["gidnumber"] for g in parsed["groups"]}
    assert {5502, 8001, 8501, 9002} <= gids


def test_merge_configs_skips_colliding_offerings(refresher, monkeypatch, caplog):
    monkeypatch.setattr(refresher, "TEMPLATE_PATH", str(PRECONFIG))
    preconfig = refresher.template_preconfig(ADMIN_ENV, "deadbeef")

    # Feeding the same offering export twice collides on every uid/gid/name;
    # the second copy must be skipped rather than producing duplicate records.
    merged = refresher.merge_configs(preconfig, [API_USERS_CONFIG, API_USERS_CONFIG])
    parsed = tomllib.loads(merged)

    alice_records = [u for u in parsed["users"] if u["name"] == "alice"]
    assert len(alice_records) == 1
    gid_8001 = [g for g in parsed["groups"] if g["gidnumber"] == 8001]
    assert len(gid_8001) == 1
    assert any("collision" in message.lower() for message in caplog.messages)


# A realistic single-offering users config as produced by the Waldur
# ``glauth_users_config`` action: two team members who share a resource-scope
# role group (``hpc-cluster_admin``, gid 60000) and a resource-project-scope
# role group (``hpc-cluster_a1b2c3d4_member``, gid 60001), each with a distinct
# uid and personal group, plus a legacy project-mapped group (gid 6001). Built
# with ``tomli_w`` — the exact serializer the API uses — so the on-the-wire
# format (``[[users]]`` / ``[[groups]]`` array-of-tables, nested
# customattributes) matches production rather than a hand-written approximation.
def _mastermind_users_config():
    def member(name, uidnumber, primarygroup):
        return {
            "name": name,
            "givenname": name.capitalize(),
            "sn": "Example",
            "mail": f"{name}@example.com",
            "uidnumber": uidnumber,
            "primarygroup": primarygroup,
            "otherGroups": [6001, 60000, 60001],
            "sshkeys": [],
            "loginShell": "/bin/bash",
            "homeDir": f"/home/{name}",
            "passsha256": "",
            "disabled": False,
            "customattributes": {"preferredUsername": [name]},
        }

    return tomli_w.dumps(
        {
            "users": [
                member("alice", 1001, 2001),
                member("bob", 1002, 2002),
            ],
            "groups": [
                {"name": "alice", "gidnumber": 2001},
                {"name": "bob", "gidnumber": 2002},
                {"name": "6001", "gidnumber": 6001},
                {"name": "hpc-cluster_admin", "gidnumber": 60000},
                {"name": "hpc-cluster_a1b2c3d4_member", "gidnumber": 60001},
            ],
        }
    )


MASTERMIND_USERS_CONFIG = _mastermind_users_config()


def _render(refresher, monkeypatch):
    """Run the full refresh pipeline (template + merge) and return parsed TOML."""
    monkeypatch.setattr(refresher, "TEMPLATE_PATH", str(PRECONFIG))
    preconfig = refresher.template_preconfig(ADMIN_ENV, "deadbeef")
    merged = refresher.merge_configs(preconfig, MASTERMIND_USERS_CONFIG)
    return tomllib.loads(merged)


def test_member_uid_and_primary_group_survive_merge(refresher, monkeypatch):
    """Each team member's uid and personal group reach the rendered config
    unchanged, and stay distinct per user."""
    users = {u["name"]: u for u in _render(refresher, monkeypatch)["users"]}
    assert users["alice"]["uidnumber"] == 1001
    assert users["alice"]["primarygroup"] == 2001
    assert users["bob"]["uidnumber"] == 1002
    assert users["bob"]["primarygroup"] == 2002
    assert users["alice"]["uidnumber"] != users["bob"]["uidnumber"]
    assert users["alice"]["primarygroup"] != users["bob"]["primarygroup"]
    # The templated admin account coexists untouched.
    assert users["serviceuser"]["uidnumber"] == 5003


def test_role_group_gids_survive_merge(refresher, monkeypatch):
    """Personal, project and both role groups reach the config with their
    gidnumbers intact — including the standalone role groups a naive text
    concatenation would drop."""
    gids = {
        g["name"]: g["gidnumber"] for g in _render(refresher, monkeypatch)["groups"]
    }
    assert gids["alice"] == 2001
    assert gids["bob"] == 2002
    assert gids["6001"] == 6001
    assert gids["hpc-cluster_admin"] == 60000
    assert gids["hpc-cluster_a1b2c3d4_member"] == 60001
    # The preconfig admin group survives alongside the API groups.
    assert 5502 in gids.values()


def test_member_othergroups_resolve_to_real_groups(refresher, monkeypatch):
    """ldapsearch-shaped invariant: every gid in a member's otherGroups resolves
    to a group that exists in the merged config, and both members carry the two
    shared role-group gids."""
    parsed = _render(refresher, monkeypatch)
    group_gids = {g["gidnumber"] for g in parsed["groups"]}
    users = {u["name"]: u for u in parsed["users"]}
    for name in ("alice", "bob"):
        for gid in users[name]["otherGroups"]:
            assert gid in group_gids, f"{name} otherGroups gid {gid} has no group"
        assert {60000, 60001}.issubset(set(users[name]["otherGroups"]))


def test_shared_role_gids_are_identical_across_members(refresher, monkeypatch):
    """The role groups are keyed per (offering, scope, role), so both members
    must reference the very same role-group gids, not per-user copies."""
    users = {u["name"]: u for u in _render(refresher, monkeypatch)["users"]}
    alice_role_gids = {g for g in users["alice"]["otherGroups"] if g >= 60000}
    bob_role_gids = {g for g in users["bob"]["otherGroups"] if g >= 60000}
    assert alice_role_gids == bob_role_gids == {60000, 60001}
