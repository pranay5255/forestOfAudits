from __future__ import annotations

import pytest

from evmbench.agents.backend.base import (
    AUDIT_DIR,
    LOGS_DIR,
    ComputerError,
    ComputerIdentity,
    NullSideTarget,
)
from evmbench.agents.backend.local import LocalProcessComputer
from evmbench.agents.backend.modal import ModalComputer, ModalImageSpec, sandbox_name


def test_local_computer_exec_upload_download_list(tmp_path):
    computer = LocalProcessComputer.create(root=tmp_path, stage="branch", worker_id="w1")
    try:
        result = computer.exec("pwd && echo hello")
        assert result.ok
        assert "hello" in result.stdout
        assert str(tmp_path) in result.stdout

        computer.upload_text("payload", f"{AUDIT_DIR}/note.txt")
        assert computer.download_text(f"{AUDIT_DIR}/note.txt") == "payload"
        assert computer.exists(f"{AUDIT_DIR}/note.txt")
        assert computer.list(f"{AUDIT_DIR}") == ["note.txt"]

        probe = computer.exec("exit 3")
        assert probe.exit_code == 3
        assert not probe.ok
    finally:
        computer.terminate()

    assert computer.terminated
    with pytest.raises(ComputerError):
        computer.exec("echo nope")


def test_local_computer_maps_logs_and_agent_dirs(tmp_path):
    computer = LocalProcessComputer.create(root=tmp_path)
    try:
        computer.upload_text("x", f"{LOGS_DIR}/a.txt")
        assert (tmp_path / "logs" / "a.txt").read_text() == "x"
        with pytest.raises(ComputerError):
            computer.download(f"{LOGS_DIR}/missing.txt")
    finally:
        computer.terminate()


def test_null_side_target_rejects_operations():
    target = NullSideTarget("chain")
    with pytest.raises(ComputerError):
        target.exec("true")
    with pytest.raises(ComputerError):
        target.upload(b"x", "/tmp/x")
    with pytest.raises(ComputerError):
        target.download("/tmp/x")
    with pytest.raises(ComputerError):
        target.list("/tmp")
    target.terminate()
    assert target.terminated


def test_modal_sandbox_name_is_deterministic_and_safe():
    name = sandbox_name("CMP/1", "2024-01-canto", 0, "branch", "Token Flow/Branch 01")
    assert name == sandbox_name("CMP/1", "2024-01-canto", 0, "branch", "Token Flow/Branch 01")
    assert name.startswith("evmbench-cmp-1-2024-01-canto-a0-branch")
    assert "/" not in name and " " not in name
    assert len(name) <= 63


class _FakeStream:
    def __init__(self, value: str) -> None:
        self.value = value

    def read(self) -> str:
        return self.value


class _FakeProcess:
    def __init__(self, stdout: str, stderr: str, exit_code: int) -> None:
        self.stdout = _FakeStream(stdout)
        self.stderr = _FakeStream(stderr)
        self._exit_code = exit_code

    def wait(self) -> int:
        return self._exit_code


class _FakeFilesystem:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    def write_bytes(self, data: bytes, remote_path: str) -> None:
        self.files[remote_path] = data

    def read_bytes(self, remote_path: str) -> bytes:
        if remote_path not in self.files:
            raise FileNotFoundError(remote_path)
        return self.files[remote_path]


class _FakeSandbox:
    def __init__(self) -> None:
        self.filesystem = _FakeFilesystem()
        self.terminated = False
        self.object_id = "sb-123"
        self.commands: list[str] = []

    def exec(self, *_args, **_kwargs):
        command = _args[2] if len(_args) > 2 else ""
        self.commands.append(command)
        if command.startswith("ls -A"):
            return _FakeProcess("\n".join(sorted(self.filesystem.files)), "", 0)
        return _FakeProcess("ok", "", 0)

    def terminate(self) -> None:
        self.terminated = True


def test_modal_computer_contract_against_fake_sandbox():
    sandbox = _FakeSandbox()
    computer = ModalComputer(
        sandbox,
        ComputerIdentity(backend="modal", sandbox_id="sb-123", image="img:tag"),
    )
    result = computer.exec("echo hi")
    assert result.ok and result.stdout == "ok"
    computer.upload(b"data", "/home/agent/a.txt")
    assert computer.download("/home/agent/a.txt") == b"data"
    assert computer.list("/home/agent") == ["/home/agent/a.txt"]
    computer.terminate()
    computer.terminate()
    assert sandbox.terminated


def test_modal_image_spec_pins_digest():
    spec = ModalImageSpec(reference="ghcr.io/acme/evmbench/audit:2024-01-canto", digest="sha256:abc")
    assert spec.pinned_reference.endswith("@sha256:abc")
    already_pinned = ModalImageSpec(reference="repo@sha256:def", digest="sha256:abc")
    assert already_pinned.pinned_reference == "repo@sha256:def"
