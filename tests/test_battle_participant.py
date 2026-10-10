"""Local participant launcher boundaries without starting an LLM or touching the API."""

import importlib.util
import json
import os
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "battle_participant", Path(__file__).resolve().parents[1] / "scripts" / "battle_participant.py"
)
participant = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(participant)


def test_participant_environment_drops_market_owner_cloud_and_database_credentials(monkeypatch, tmp_path):
    for key, value in {
        "MARKET_TOKEN": "opponent-token",
        "MARKET_WORKSPACE": "opponent-workspace",
        "MARKET_PROFILE": "owner",
        "MARKET_PASSWORD": "owner-password",
        "DATABASE_URL": "postgresql://private-password@private-database",
        "MINIO_SECRET_KEY": "private-storage-key",
        "OPENAI_API_KEY": "unintended-provider-key",
        "ANTHROPIC_API_KEY": "another-provider-key",
        "CODEX_HOME": str(tmp_path / "authenticated-runtime"),
        "PATH": "/usr/bin:/bin",
    }.items():
        monkeypatch.setenv(key, value)
    env = participant.participant_environment(tmp_path / "buyer", "/opt/market/bin/market")
    assert env["MARKET_CONFIG"] == str(tmp_path / "buyer" / "market-profile.json")
    assert env["MARKET_PROFILE"] == "default"
    assert env["PATH"].split(os.pathsep)[0] == "/opt/market/bin"
    assert env["CODEX_HOME"] == str(tmp_path / "authenticated-runtime")
    for forbidden in (
        "MARKET_TOKEN",
        "MARKET_WORKSPACE",
        "MARKET_PASSWORD",
        "DATABASE_URL",
        "MINIO_SECRET_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
    ):
        assert forbidden not in env
    assert "opponent" not in json.dumps(env)


@pytest.mark.parametrize("role", ["buyer", "seller"])
def test_role_prompt_contains_no_tokens_or_numeric_private_limits(role):
    prompt = participant.prompt_for(role, "room-placeholder")
    assert f"independent {role}" in prompt
    assert "awaiting_approval" in prompt and "actual photograph" in prompt
    assert "SAME payload/key" in prompt
    assert "never print or open" in prompt.lower()
    for private in ("10000", "120000", "private-buyer-token", "private-seller-token"):
        assert private not in prompt
    if role == "buyer":
        assert "Minimize the buyer" in prompt and "Maximize the seller" not in prompt
    else:
        assert "Maximize the seller" in prompt and "Minimize the buyer" not in prompt


@pytest.mark.parametrize("role", ["buyer", "seller"])
def test_launcher_passes_only_requested_role_profile_and_captures_private_logs(
    monkeypatch, tmp_path, capsys, role
):
    config = {
        "api_url": "http://127.0.0.1:8100",
        "room_id": "negotiation",
        "run_dir": str(tmp_path / "run"),
        "buyer": {"workspace_id": "buyer-workspace", "token": "private-buyer-token"},
        "seller": {"workspace_id": "seller-workspace", "token": "private-seller-token"},
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config))
    config_path.chmod(0o600)
    monkeypatch.setattr(participant.shutil, "which", lambda program: "/usr/local/bin/" + program)
    captured = {}

    class Child:
        pid = 123456
        returncode = 0

        def __init__(self, command, **kwargs):
            captured.update(command=command, **kwargs)

        def communicate(self, prompt, timeout):
            captured.update(prompt=prompt, timeout=timeout)

    monkeypatch.setattr(participant.subprocess, "Popen", Child)
    monkeypatch.setattr("sys.argv", ["participant", "--config", str(config_path), "--role", role])
    prior_mask = os.umask(0o022)
    try:
        with pytest.raises(SystemExit) as exit_info:
            participant.main()
    finally:
        os.umask(prior_mask)
    assert exit_info.value.code == 0
    directory = tmp_path / "participants" / role
    saved = json.loads((directory / "market-profile.json").read_text())
    own = saved["profiles"]["default"]
    assert own["token"] == config[role]["token"] and own["workspace"] == config[role]["workspace_id"]
    opposite = "seller" if role == "buyer" else "buyer"
    assert config[opposite]["token"] not in json.dumps(saved)
    assert config[opposite]["workspace_id"] not in json.dumps(saved)
    assert "private-" not in " ".join(captured["command"])
    assert config[opposite]["token"] not in json.dumps(captured["env"])
    assert "--ignore-user-config" in captured["command"] and "--ephemeral" in captured["command"]
    assert captured["command"][-1] == "-" and captured["start_new_session"] is True
    assert captured["env"]["MARKET_CONFIG"] == str(directory / "market-profile.json")
    assert directory.stat().st_mode & 0o777 == 0o700
    for name in ("market-profile.json", "agent.jsonl", "agent.stderr", "process.json"):
        assert (directory / name).stat().st_mode & 0o777 == 0o600
    public_output = capsys.readouterr()
    assert "private-buyer-token" not in public_output.out + public_output.err
    assert "private-seller-token" not in public_output.out + public_output.err


def test_launcher_refuses_nonprivate_config_before_starting_process(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text("{}")
    config_path.chmod(0o644)
    monkeypatch.setattr("sys.argv", ["participant", "--config", str(config_path), "--role", "buyer"])
    monkeypatch.setattr(
        participant.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("Must not start the participant")
    )
    with pytest.raises(SystemExit) as exit_info:
        participant.main()
    assert exit_info.value.code == 2
