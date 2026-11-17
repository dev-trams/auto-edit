"""한 번에 하나만 실행: 새로 켤 때 이미 켜져 있는 auto-edit를 찾아 끈다.

업데이트한 뒤 예전 버전이 계속 떠 있거나, 창을 닫지 않고 다시 실행한 경우를 정리한다.
먼저 정상 종료를 요청하고, 응답이 없으면 그 포트를 쓰는 프로세스를 강제로 끝낸다.
auto-edit라고 응답한 서버만 대상으로 한다.
"""
import json
import os
import re
import secrets
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request

from . import config

LOCK = config.APP_DIR / "instance.json"
SCAN = 6  # 기본 포트부터 몇 개까지 살펴볼지


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if os.name != "nt":
            # 방금 닫힌 포트를 바로 다시 쓸 수 있게 한다 (윈도우에서는 이 옵션의 뜻이 달라 쓰지 않는다)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _request(port: int, path: str, data=None, token: str = ""):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data,
                                 headers={"X-Autoedit-Token": token, "Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 프록시 설정을 타지 않게
    with opener.open(req, timeout=2) as resp:
        return json.loads(resp.read() or b"{}")


def _is_autoedit(port: int) -> bool:
    try:
        state = _request(port, "/api/state")
        return isinstance(state, dict) and "presets" in state and "settings" in state
    except (OSError, ValueError):
        return False


def _pid_on_port(port: int):
    try:
        if os.name == "nt":
            out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, errors="replace").stdout
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 5 and parts[1].endswith(f":{port}") and parts[3].upper() == "LISTENING":
                    return int(parts[4])
        else:
            out = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"], capture_output=True, text=True).stdout
            if out.strip():
                return int(out.split()[0])
    except (OSError, ValueError):
        pass
    if os.name != "nt":
        try:
            out = subprocess.run(["ss", "-H", "-ltnp", f"sport = :{port}"], capture_output=True, text=True).stdout
            m = re.search(r"pid=(\d+)", out)
            if m:
                return int(m.group(1))
        except OSError:
            pass
    return None


def _kill(pid: int):
    if pid == os.getpid():
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        else:
            os.kill(pid, signal.SIGTERM)
            time.sleep(1.0)
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    except (OSError, ProcessLookupError):
        pass


def _wait_free(port: int, seconds: float) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if port_free(port):
            return True
        time.sleep(0.2)
    return port_free(port)


def stop_existing(base_port: int) -> list:
    """켜져 있는 auto-edit를 끈다. 끈 포트 목록을 돌려준다."""
    lock = {}
    try:
        lock = json.loads(LOCK.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    ports = []
    for p in [lock.get("port")] + list(range(base_port, base_port + SCAN)):
        if isinstance(p, int) and p not in ports:
            ports.append(p)
    stopped = []
    for port in ports:
        if port_free(port) or not _is_autoedit(port):
            continue
        try:
            _request(port, "/api/shutdown", data=b"{}", token=lock.get("token", "") if lock.get("port") == port else "")
        except (OSError, ValueError):
            pass  # 예전 버전이거나 토큰이 다르면 아래에서 강제로 끝낸다
        if not _wait_free(port, 4):
            pid = _pid_on_port(port)
            if pid:
                _kill(pid)
            _wait_free(port, 4)
        if port_free(port):
            stopped.append(port)
    return stopped


def register(port: int) -> str:
    token = secrets.token_hex(16)
    config.APP_DIR.mkdir(parents=True, exist_ok=True)
    LOCK.write_text(json.dumps({"pid": os.getpid(), "port": port, "token": token}), encoding="utf-8")
    return token


def unregister():
    try:
        if json.loads(LOCK.read_text(encoding="utf-8")).get("pid") == os.getpid():
            LOCK.unlink()
    except (OSError, ValueError):
        pass
