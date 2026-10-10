"""Confinement and lifecycle regressions; no model calls or live Docker required."""

import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location(
    "container_participant", Path(__file__).resolve().parents[1] / "scripts/container_participant.py"
)
participant = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(participant)
CID = "a" * 64


@pytest.fixture
def settings(tmp_path):
    config = tmp_path / "config.json"
    participant.private_json(
        config,
        {
            "room_id": "room-one",
            "run_dir": str(tmp_path / "run"),
            "buyer": {"token": "private-buyer-token", "workspace_id": "buyer-workspace"},
            "seller": {"token": "private-seller-token", "workspace_id": "seller-workspace"},
        },
    )
    auth = tmp_path / "auth.json"
    participant.private_json(auth, {"test-auth": "private-runtime-token"})
    return SimpleNamespace(
        config=config,
        role="buyer",
        auth_file=auth,
        check=False,
        image=participant.IMAGE,
        network=participant.NETWORK,
        model="gpt-6-astra",
        timeout=900,
        wait_for_guidance=False,
    )


def test_each_container_gets_only_own_identity_and_workdir(settings, monkeypatch):
    monkeypatch.setattr(participant.os, "getuid", lambda: 1000)
    monkeypatch.setattr(participant.os, "getgid", lambda: 1000)
    folders = []
    for role in ("buyer", "seller"):
        _, folder, work, profile, auth = participant.prepare(settings.config, role, settings.auth_file)
        folders.append(folder)
        own = json.loads(profile.read_text())["profiles"]["default"]
        assert own == {
            "token": f"private-{role}-token",
            "workspace": f"{role}-workspace",
            "base_url": "http://api:8100",
        }
        command = participant.docker_command(work, profile, auth, "participant-test")
        mounts = [command[i + 1] for i, value in enumerate(command) if value == "--mount"]
        assert len(mounts) == 3
        assert str(work) in mounts[0] and "dst=/workspace" in mounts[0]
        assert str(profile) in mounts[1] and mounts[1].endswith(",readonly")
        assert str(auth) in mounts[2] and mounts[2].endswith(",readonly")
        assert str(settings.config) not in " ".join(command)
        assert str(settings.auth_file) not in " ".join(command)
        assert "private-" not in " ".join(command)
        assert "docker.sock" not in " ".join(command)
        assert "--read-only" in command and "--cap-drop=ALL" in command
        assert "--security-opt=no-new-privileges:true" in command
        assert command[command.index("--user") + 1] == "1000:1000"
        assert command[command.index("--network") + 1] != "host"
        assert folder != work and folder not in [Path(m.split("src=")[1].split(",")[0]) for m in mounts]
        assert profile.stat().st_mode & 0o777 == 0o600
        assert auth.stat().st_mode & 0o777 == 0o600
    assert folders[0] != folders[1]


def test_refuses_changed_identity_and_unsafe_paths(settings, tmp_path):
    _, _, _, profile, _ = participant.prepare(settings.config, "buyer", settings.auth_file)
    profile.write_text('{"profiles": {}}')
    with pytest.raises(ValueError, match="different credentials"):
        participant.prepare(settings.config, "buyer", settings.auth_file)
    settings.config.chmod(0o644)
    with pytest.raises(ValueError, match="private regular"):
        participant.prepare(settings.config, "seller", settings.auth_file)
    symlink = tmp_path / "linked-auth.json"
    symlink.symlink_to(settings.auth_file)
    with pytest.raises(ValueError, match="private regular"):
        participant.private_file(symlink)


def test_model_runs_inside_container_without_inherited_config():
    command = participant.model_command("chosen-model")
    assert command[:2] == ["codex", "exec"]
    for option in ("--ignore-user-config", "--ignore-rules", "--ephemeral", "--json"):
        assert option in command
    assert command[command.index("--model") + 1] == "chosen-model"
    assert command[command.index("-C") + 1] == "/workspace"
    assert command[command.index("--sandbox") + 1] == "danger-full-access"


def test_check_reads_role_and_photo_without_starting_a_model(settings, monkeypatch, capsys):
    settings.check = True
    calls = []
    monkeypatch.setattr(participant.os, "getuid", lambda: 1000)
    monkeypatch.setattr(participant.os, "getgid", lambda: 1000)

    def capture(command, **kwargs):
        calls.append(command)
        result = (
            CID
            if command[1] == "create"
            else json.dumps(
                {
                    "ready": True,
                    "role": "buyer",
                    "codex_version": "codex-cli 0.162.1",
                    "image_count": 1,
                    "uid": 1000,
                }
            )
        )
        return SimpleNamespace(stdout=result)

    monkeypatch.setattr(participant, "run_capture", capture)
    monkeypatch.setattr(participant.subprocess, "run", lambda command, **kwargs: calls.append(command))
    monkeypatch.setattr(participant.subprocess, "Popen", lambda *a, **kw: pytest.fail("Model must not start"))
    assert participant.execute(settings) == 0
    assert participant.CHECK_CODE in calls[0]
    assert '"login", "status"' in participant.CHECK_CODE
    assert '"images", "read"' in participant.CHECK_CODE
    assert 'identity["credential"]["kind"] == "agent"' in participant.CHECK_CODE
    assert calls[-1] == ["docker", "rm", "--force", CID]
    output = json.loads(capsys.readouterr().out)
    assert output["event"] == "container_ready" and output["container_id"] == CID
    assert "private-" not in json.dumps(output)
    assert not (settings.config.parent / "participants/buyer/process.json").exists()


@pytest.mark.parametrize("timeout", [False, True])
def test_live_attachment_keeps_model_output_private_and_stops_container(
    settings, monkeypatch, capsys, timeout
):
    calls = []
    monkeypatch.setattr(participant.os, "getuid", lambda: 1000)
    monkeypatch.setattr(participant.os, "getgid", lambda: 1000)

    def capture(command, **kwargs):
        result = (
            json.dumps({"Running": True, "StartedAt": "2026-10-10T18:00:00Z"})
            if command[1] == "inspect"
            else CID
        )
        return SimpleNamespace(stdout=result)

    monkeypatch.setattr(participant, "run_capture", capture)
    monkeypatch.setattr(participant.subprocess, "run", lambda command, **kwargs: calls.append(command))
    monkeypatch.setattr(participant, "prompt_for", lambda *args: "public commercial instructions")

    class Child:
        pid = 1234
        returncode = 0

        def __init__(self, command, **kwargs):
            assert command == ["docker", "start", "--attach", "--interactive", CID]
            assert "env" not in kwargs  # Docker does not forward arbitrary host env.
            kwargs["stdout"].write("PRIVATE MODEL OUTPUT")
            kwargs["stderr"].write("PRIVATE MODEL ERROR")

        def communicate(self, prompt, **kwargs):
            if timeout:
                raise subprocess.TimeoutExpired("docker", 900)

        def wait(self, **kwargs):
            return 0

        def poll(self):
            return self.returncode

    monkeypatch.setattr(participant.subprocess, "Popen", Child)
    assert participant.execute(settings) == (124 if timeout else 0)
    public = capsys.readouterr().out
    assert "PRIVATE" not in public and "private-" not in public
    assert [json.loads(line)["event"] for line in public.splitlines()] == [
        "container_started",
        "container_exited",
    ]
    directory = settings.config.parent / "participants/buyer"
    assert (directory / "agent.jsonl").read_text() == "PRIVATE MODEL OUTPUT"
    assert (directory / "agent.jsonl").stat().st_mode & 0o777 == 0o600
    assert json.loads((directory / "process.json").read_text())["container_id"] == CID
    assert calls[-1] == ["docker", "rm", "--force", CID]
    if timeout:
        assert ["docker", "stop", "--time", "10", CID] in calls
    with pytest.raises(ValueError, match="already ran"):
        participant.execute(settings)


def test_refuses_root_host_uid(monkeypatch, tmp_path):
    monkeypatch.setattr(participant.os, "getuid", lambda: 0)
    with pytest.raises(ValueError, match="non-root"):
        participant.docker_command(tmp_path, tmp_path / "profile", tmp_path / "auth", "test")


def test_started_event_waits_for_real_running_state(monkeypatch):
    states = iter(
        [
            {"Running": False, "Status": "created", "StartedAt": "0001-01-01T00:00:00Z"},
            {"Running": True, "Status": "running", "StartedAt": "2026-10-10T18:00:00.123456Z"},
        ]
    )
    sleeps = []
    monkeypatch.setattr(
        participant, "run_capture", lambda *a, **kw: SimpleNamespace(stdout=json.dumps(next(states)))
    )
    monkeypatch.setattr(participant.time, "sleep", sleeps.append)
    assert participant.wait_started(CID, SimpleNamespace(poll=lambda: None)) == "2026-10-10T18:00:00.123456Z"
    assert sleeps == [0.1]


def test_failed_start_never_reports_running(monkeypatch):
    monkeypatch.setattr(
        participant,
        "run_capture",
        lambda *a, **kw: SimpleNamespace(
            stdout=json.dumps(
                {
                    "Running": False,
                    "Status": "exited",
                    "StartedAt": "2026-10-10T18:00:00Z",
                }
            )
        ),
    )
    with pytest.raises(ValueError, match="exited before starting"):
        participant.wait_started(CID, SimpleNamespace(poll=lambda: 1))
