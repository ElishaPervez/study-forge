from __future__ import annotations

import pytest


class BoundSocket:
    def getsockname(self) -> tuple[str, int]:
        return "127.0.0.1", 53124


def test_main_announces_the_bound_socket_before_serving(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import backend.api.__main__ as entrypoint

    bound_socket = BoundSocket()
    events: list[object] = []

    class FakeConfig:
        def __init__(self, app, *, host: str, port: int, log_level: str) -> None:
            events.append(("config", app, host, port, log_level))

        def load(self) -> None:
            events.append("load")

        def bind_socket(self) -> BoundSocket:
            events.append("bind")
            return bound_socket

    class FakeServer:
        def __init__(self, config: FakeConfig) -> None:
            self.config = config
            events.append(("server", config))

        def run(self, *, sockets: list[BoundSocket]) -> None:
            events.append(("run", sockets))

    monkeypatch.setattr(entrypoint, "load_settings", lambda: object())
    monkeypatch.setattr(entrypoint, "create_app", lambda settings: "app")
    monkeypatch.setattr(entrypoint.uvicorn, "Config", FakeConfig)
    monkeypatch.setattr(entrypoint.uvicorn, "Server", FakeServer)

    assert entrypoint.main() == 0

    assert capsys.readouterr().out == "STUDY_FORGE_PORT=53124\n"
    assert events[-1] == ("run", [bound_socket])
    assert events.index("bind") < events.index(("run", [bound_socket]))


def test_main_returns_two_without_printing_to_stdout_when_settings_fail(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import backend.api.__main__ as entrypoint

    monkeypatch.setattr(
        entrypoint,
        "load_settings",
        lambda: (_ for _ in ()).throw(ValueError("missing key")),
    )

    assert entrypoint.main() == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "startup failed: missing key\n"
