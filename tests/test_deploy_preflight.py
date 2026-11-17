"""The test machine's shim.env has only secrets. Upstream lives on the container."""
import importlib.util
from pathlib import Path

import pytest

from dujiao_shim.suppliers import Client16688, Supplier16688

ROOT = Path(__file__).resolve().parents[1]
PRE = ROOT / "deploy" / "sandbox" / "preflight.py"
SCRIPT = ROOT / "deploy" / "sandbox" / "update-dj-shim.sh"


def load_preflight():
    spec = importlib.util.spec_from_file_location("shim_preflight", PRE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


P = load_preflight()

# The file on the sandbox. No upstream URL.
SECRETS_ONLY = {"SHIM_API_SECRET": "s" * 32, "SHIM_ADMIN_TOKEN": "t" * 32}

# What `docker inspect dj-shim` actually had: host mock16688, live on, formal unset.
RUNNING = {
    "OPEN16688_BASE_URL": "http://mock16688:9000",
    "OPEN16688_APP_ID": "app",
    "OPEN16688_SECRET": "sec",
    "SHIM_LIVE_PURCHASE": "1",
    "SHIM_API_KEY": "gg-upstream",
    "SHIM_API_SECRET": "s" * 32,
    "SHIM_ADMIN_TOKEN": "t" * 32,
    "SHIM_DB": "/data/shim.db",
    "SHIM_CATALOG": "/data/catalog.json",
    "SHIM_DUJIAO_URL": "http://dj-app:8080",
    "SHIM_REDEEM_URL": "http://127.0.0.1:13361/",
}


def test_real_layout_accepts_secrets_file_without_upstream_url():
    assert P.confirm_test_upstream(RUNNING, SECRETS_ONLY) == "mock16688"


def test_live_mock_is_not_formal_and_is_kept():
    sup = Supplier16688(Client16688("app", "sec", RUNNING["OPEN16688_BASE_URL"]), live=True,
                        min_balance=0, authorize_formal=False)
    assert sup.purchase_mode() == "mock"
    assert P.mode_allows_keep(sup.purchase_mode()) is True
    official = Supplier16688(Client16688("app", "sec", "https://www.16688.com.cn"), live=True,
                             min_balance=0, authorize_formal=False)
    assert official.purchase_mode() == "formal_blocked"
    assert P.mode_allows_keep(official.purchase_mode()) is False
    authorized = Supplier16688(Client16688("app", "sec", "https://www.16688.com.cn"), live=True,
                               min_balance=0, authorize_formal=True)
    assert authorized.purchase_mode() == "formal"
    assert P.mode_allows_keep("formal") is False
    assert P.mode_allows_keep("formal_blocked") is False
    assert P.mode_allows_keep("simulated") is False


def test_official_and_lookalike_hosts_are_refused():
    refused = [
        "https://www.16688.com.cn",
        "https://16688.com.cn/openApi",
        "http://api.16688.com.cn",
        "http://mock16688.16688.com.cn",
        "http://mock16688.16688.com.cn:9000",
        "http://evil-mock16688",
        "http://mock16688.example.com",
        "http://notmock16688",
        "http://prefix-mock16688-suffix.test",
        "http://16688.com.cn.mock16688.evil.test",
    ]
    for url in refused:
        with pytest.raises(SystemExit):
            P.confirm_test_upstream({"OPEN16688_BASE_URL": url, "SHIM_LIVE_PURCHASE": "1"}, SECRETS_ONLY)


def test_missing_container_url_and_formal_flag_and_file_conflict():
    with pytest.raises(SystemExit):
        P.confirm_test_upstream(dict(SECRETS_ONLY), SECRETS_ONLY)
    with pytest.raises(SystemExit):
        P.confirm_test_upstream({**RUNNING, "SHIM_AUTHORIZE_FORMAL_PURCHASE": "1"}, SECRETS_ONLY)
    with pytest.raises(SystemExit):
        P.confirm_test_upstream(RUNNING, {**SECRETS_ONLY, "OPEN16688_BASE_URL": "https://www.16688.com.cn"})
    with pytest.raises(SystemExit):
        P.confirm_test_upstream(RUNNING, {**SECRETS_ONLY, "SHIM_AUTHORIZE_FORMAL_PURCHASE": "yes"})
    assert P.confirm_test_upstream(RUNNING, {**SECRETS_ONLY, "OPEN16688_BASE_URL": "http://MOCK16688:9000"}) == "mock16688"


def test_recreate_keeps_container_env_mounts_network_and_command():
    info = {
        "Config": {"User": "app", "Entrypoint": None, "Cmd": ["dujiao-shim"]},
        "HostConfig": {
            "RestartPolicy": {"Name": "unless-stopped"},
            "ReadonlyRootfs": True,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges:true"],
            "Memory": 134217728,
            "NanoCpus": 500000000,
            "PidsLimit": 64,
            "Tmpfs": {"/tmp": "rw,noexec,nosuid,size=16m"},
        },
        "NetworkSettings": {"Networks": {"djtest": {}, "extra": {}}},
        "Mounts": [
            {"Type": "bind", "Source": "/data/workspace/dujiao-test/shimdata", "Destination": "/data", "RW": True},
        ],
    }
    args = P.plan_run(info, "/tmp/backup/container.env", "gsx/16688-mcp:test", "dj-shim")
    assert args[0:3] == ["docker", "run", "-d"]
    assert args[args.index("--env-file") + 1] == "/tmp/backup/container.env"
    assert "shim.env" not in args
    assert args[args.index("--network") + 1] == "djtest"
    assert "/data/workspace/dujiao-test/shimdata:/data" in args
    assert "--user" in args and args[args.index("--user") + 1] == "app"
    assert "127.0.0.1:13360:3000" in args and "127.0.0.1:13361:3001" in args
    assert args[-1] == "dujiao-shim"
    assert "dj-app" not in args and "aihot" not in args and "redis" not in args
    joined = " ".join(args)
    assert "0.0.0.0:" not in joined


def test_update_script_does_not_use_a_substring_host_check():
    text = SCRIPT.read_text()
    assert "mock16688\" not in" not in text
    assert "not in file_host" not in text
    assert "confirm_test_upstream" in text
    assert "mode_allows_keep" in text
    assert "shim.env 未修改" in text
