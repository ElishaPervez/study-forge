from __future__ import annotations

import sys

import uvicorn

from backend.api.app import create_app
from backend.settings import load_settings

ANNOUNCE_PREFIX = "LESSON_GEN_PORT="


def main() -> int:
    try:
        settings = load_settings()
    except ValueError as error:
        print(f"startup failed: {error}", file=sys.stderr)
        return 2

    config = uvicorn.Config(
        create_app(settings),
        host="127.0.0.1",
        port=0,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    server.config.load()
    bound_socket = config.bind_socket()
    print(f"{ANNOUNCE_PREFIX}{bound_socket.getsockname()[1]}", flush=True)
    server.run(sockets=[bound_socket])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
