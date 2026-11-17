"""Decide whether the test machine may replace dj-shim.

The running container is the source of truth. shim.env on this machine only
holds secrets and often has no OPEN16688_BASE_URL. A URL that is present in
the file must still match the container. The allowed upstream is the exact
host name mock16688, not a substring and not any 16688.com.cn name.
"""
from urllib.parse import urlparse

ALLOWED_MOCK_HOSTS = frozenset({"mock16688"})
_ON = frozenset({"1", "true", "yes"})


def hostname_of(url: str) -> str:
    return (urlparse(url or "").hostname or "").lower().rstrip(".")


def is_official_host(host: str) -> bool:
    host = (host or "").lower().rstrip(".")
    return host == "16688.com.cn" or host.endswith(".16688.com.cn")


def _flag(env: dict, name: str) -> bool:
    return str(env.get(name, "")).strip().lower() in _ON


def confirm_test_upstream(container_env: dict, file_env: dict | None = None) -> str:
    """Return the mock host, or raise SystemExit before any container is replaced.

    file_env may omit OPEN16688_BASE_URL. If the key is present and non-empty,
    its host must be the same exact host as the running container.
    """
    file_env = file_env or {}
    run_host = hostname_of(container_env.get("OPEN16688_BASE_URL", ""))
    if not run_host:
        raise SystemExit("停止：运行中的容器没有 OPEN16688_BASE_URL。没有改容器。")
    if is_official_host(run_host) or run_host not in ALLOWED_MOCK_HOSTS:
        raise SystemExit(f"停止：上游主机不是 mock16688（看到的主机名是 {run_host}）。没有改容器。")
    raw = str(file_env.get("OPEN16688_BASE_URL", "")).strip()
    if raw:
        file_host = hostname_of(raw)
        if file_host != run_host:
            raise SystemExit("停止：shim.env 里的上游主机和运行中的容器不一致。没有改容器。")
    for env in (container_env, file_env):
        if _flag(env, "SHIM_AUTHORIZE_FORMAL_PURCHASE"):
            raise SystemExit("停止：SHIM_AUTHORIZE_FORMAL_PURCHASE 已打开。测试机不允许正式采购。")
    return run_host


def mode_allows_keep(mode: str) -> bool:
    """The test machine is LIVE=1 against mock16688. That mode is mock, not formal.

    simulated means the live flag was dropped. formal and formal_blocked mean
    the process is aimed at the official host. None of those may stay up.
    """
    return mode == "mock"


def load_env(path: str) -> dict:
    out = {}
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k] = v.strip().strip('"').strip("'")
    return out


def plan_run(info: dict, env_path: str, image: str, name: str) -> list[str]:
    """docker run argv that keeps the old container's env, mounts, network and command.

    env_path must be the captured container environment, not the secrets-only shim.env.
    Published ports are forced onto loopback.
    """
    hc = info.get("HostConfig") or {}
    cfg = info.get("Config") or {}
    args = ["docker", "run", "-d", "--name", name, "--env-file", env_path]
    user = cfg.get("User") or ""
    if user:
        args += ["--user", user]
    entry = cfg.get("Entrypoint") or []
    if entry:
        args += ["--entrypoint", entry[0]]
    restart = (hc.get("RestartPolicy") or {}).get("Name") or "unless-stopped"
    if restart and restart != "no":
        args += ["--restart", restart]
    nets = list((info.get("NetworkSettings") or {}).get("Networks") or {})
    if nets:
        args += ["--network", nets[0]]
    for mount in info.get("Mounts") or []:
        if mount.get("Type") == "bind" and mount.get("Source") and mount.get("Destination"):
            spec = f"{mount['Source']}:{mount['Destination']}"
            if not mount.get("RW", True):
                spec += ":ro"
            args += ["-v", spec]
        elif mount.get("Type") == "volume" and mount.get("Name") and mount.get("Destination"):
            args += ["-v", f"{mount['Name']}:{mount['Destination']}"]
    if hc.get("ReadonlyRootfs"):
        args.append("--read-only")
    for path, opts in (hc.get("Tmpfs") or {}).items():
        args += ["--tmpfs", f"{path}:{opts}" if opts else path]
    if hc.get("Memory"):
        args += ["--memory", str(hc["Memory"])]
    if hc.get("NanoCpus"):
        args += ["--cpus", str(hc["NanoCpus"] / 1e9)]
    if hc.get("PidsLimit"):
        args += ["--pids-limit", str(hc["PidsLimit"])]
    for cap in hc.get("CapDrop") or []:
        args += ["--cap-drop", cap]
    for opt in hc.get("SecurityOpt") or []:
        args += ["--security-opt", opt]
    args += ["-p", "127.0.0.1:13360:3000", "-p", "127.0.0.1:13361:3001", image]
    args += list(entry[1:])
    args += list(cfg.get("Cmd") or ["dujiao-shim"])
    return args
