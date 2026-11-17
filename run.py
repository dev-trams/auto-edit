"""auto-edit 실행: PC 안에서 서버를 띄우고 브라우저를 연다."""
import argparse
import multiprocessing
import socket
import sys
import threading
import webbrowser


def free_port(start: int) -> int:
    for port in range(start, start + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return start


def main():
    ap = argparse.ArgumentParser(description="auto-edit 프로토타입")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--selftest", action="store_true", help="설치 확인만 하고 끝낸다")
    args = ap.parse_args()
    if args.selftest:
        from autoedit import selftest
        sys.exit(selftest.main())

    import uvicorn
    from autoedit.server import app

    port = free_port(args.port)
    url = f"http://127.0.0.1:{port}"
    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    print(f"auto-edit 실행 중: {url}")
    print("끝내려면 이 창을 닫거나 Ctrl+C를 누르세요.")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    for stream in (sys.stdout, sys.stderr):  # 한글 출력이 깨져 멈추지 않게
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass
    main()
