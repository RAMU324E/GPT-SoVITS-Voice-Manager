import json
import os
import subprocess
import sys
import time
from pathlib import Path

import psutil
import requests


ROOT_DIR = Path(__file__).resolve().parent
STATE_DIR = ROOT_DIR / "local_state"
LOG_DIR = ROOT_DIR / "local_logs"
PID_PATH = STATE_DIR / "gateway.pid"
CONFIG_PATH = ROOT_DIR / "configs" / "proxy" / "voices.json"


def load_service_ports() -> tuple[int, int]:
    if not CONFIG_PATH.exists():
        return 9880, 9889
    try:
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        gateway_port = int(payload.get("gateway", {}).get("port", 9880))
        backend_port = int(payload.get("backend", {}).get("port", 9889))
        return gateway_port, backend_port
    except (json.JSONDecodeError, ValueError, TypeError):
        return 9880, 9889


def get_health_url() -> str:
    gateway_port, _ = load_service_ports()
    return f"http://127.0.0.1:{gateway_port}/health"


def get_control_url() -> str:
    gateway_port, _ = load_service_ports()
    return f"http://127.0.0.1:{gateway_port}/control"


def ensure_dirs() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def load_pid() -> int:
    if not PID_PATH.exists():
        return 0
    try:
        payload = json.loads(PID_PATH.read_text(encoding="utf-8"))
        return int(payload.get("pid", 0))
    except (json.JSONDecodeError, ValueError):
        return 0


def save_pid(pid: int) -> None:
    PID_PATH.write_text(json.dumps({"pid": pid}, ensure_ascii=False, indent=2), encoding="utf-8")


def clear_pid() -> None:
    if PID_PATH.exists():
        PID_PATH.unlink()


def is_gateway_healthy() -> bool:
    try:
        response = requests.get(get_health_url(), timeout=2)
        return response.ok
    except requests.RequestException:
        return False


def wait_for_health(timeout_seconds: int = 60) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if is_gateway_healthy():
            return True
        time.sleep(1)
    return False


def cleanup_stale_backends() -> None:
    backend_script = str((ROOT_DIR / "proxy_api.py").resolve()).lower()
    _, backend_port = load_service_ports()
    for process in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmdline = process.info.get("cmdline") or []
            command_text = " ".join(cmdline).lower()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if backend_script in command_text and f" -p {backend_port}" in command_text:
            try:
                process.terminate()
                process.wait(timeout=10)
            except (psutil.TimeoutExpired, psutil.NoSuchProcess):
                try:
                    process.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass


def start_gateway() -> int:
    ensure_dirs()
    cleanup_stale_backends()
    gateway_port, _ = load_service_ports()
    if is_gateway_healthy():
        print("Gateway is already running.")
        return 0

    python_executable = Path(sys.executable)
    gateway_script = ROOT_DIR / "local_tts_gateway.py"
    log_path = LOG_DIR / "gateway.log"
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["NO_PROXY"] = "127.0.0.1,localhost"
    creationflags = 0
    for flag_name in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP", "CREATE_NO_WINDOW"):
        creationflags |= getattr(subprocess, flag_name, 0)

    with log_path.open("w", encoding="utf-8") as log_handle:
        process = subprocess.Popen(
            [str(python_executable), str(gateway_script)],
            cwd=str(ROOT_DIR),
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )
    save_pid(process.pid)
    if not wait_for_health():
        print("Gateway failed to start. Check local_logs\\gateway.log")
        return 1
    print(f"Gateway started on http://127.0.0.1:{gateway_port}")
    return 0


def force_kill(pid: int) -> None:
    if pid <= 0:
        return
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def stop_gateway() -> int:
    pid = load_pid()
    if is_gateway_healthy():
        try:
            requests.get(get_control_url(), params={"command": "exit"}, timeout=2)
        except requests.RequestException:
            pass
        deadline = time.time() + 15
        while time.time() < deadline:
            if not is_gateway_healthy():
                break
            time.sleep(1)

    if is_gateway_healthy():
        force_kill(pid)
        time.sleep(2)

    cleanup_stale_backends()
    clear_pid()
    print("Gateway stopped.")
    return 0


def status_gateway() -> int:
    if not is_gateway_healthy():
        print("Gateway is not running.")
        return 1
    response = requests.get(get_health_url(), timeout=2)
    print(response.text)
    return 0


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in {"start", "stop", "status"}:
        print("Usage: local_tts_service.py [start|stop|status]")
        return 1

    command = sys.argv[1]
    if command == "start":
        return start_gateway()
    if command == "stop":
        return stop_gateway()
    return status_gateway()


if __name__ == "__main__":
    raise SystemExit(main())
