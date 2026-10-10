"""Explicit OBS recording boundaries; fake MCP responses, no real OBS mutations."""

import argparse
import asyncio
import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location(
    "obs_battle", Path(__file__).resolve().parents[1] / "scripts" / "obs_battle.py"
)
operator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(operator)


class FakeOBS:
    def __init__(self, tmp_path):
        self.active = False
        self.streaming = False
        self.scene = "Crossroads Battle"
        self.directory = str(tmp_path / "original-recording-directory")
        self.original = self.directory
        self.calls = []
        self.duration = 1000
        self.start_error = None
        self.restore_error = False
        self.start_delay_reads = 0
        self.pending_start_reads = 0
        self.output_mode = "normal"
        self.directory_error = False

    async def call(self, session, name, **arguments):
        self.calls.append((name, arguments))
        if name == "get_record_status":
            reported_active = self.active
            if self.pending_start_reads:
                self.pending_start_reads -= 1
                reported_active = False
            return {"outputActive": reported_active, "outputDuration": self.duration}
        if name == "get_stream_status":
            return {"outputActive": self.streaming}
        if name == "get_scene_list":
            return {"currentProgramSceneName": self.scene}
        if name == "get_record_directory":
            return {"recordDirectory": self.directory}
        if name == "set_record_directory":
            if self.restore_error and arguments["path"] == self.original:
                raise RuntimeError("A temporary restore failure")
            self.directory = arguments["path"]
            if self.directory_error and arguments["path"] != self.original:
                raise RuntimeError("Directory response was lost before start dispatch")
            return {}
        if name == "start_record":
            self.active = self.start_error != "before-start"
            self.pending_start_reads = self.start_delay_reads
            if self.start_error:
                raise RuntimeError("Start response was lost")
            return {}
        if name == "stop_record":
            self.active = False
            output = Path(self.directory) / "real-output.mkv"
            if self.output_mode == "outside":
                output = output.parent.parent / "another-recording.mkv"
            if self.output_mode != "missing":
                output.write_bytes(b"" if self.output_mode == "empty" else b"fake recording bytes")
            if self.output_mode == "invalid":
                return {"outputPath": None}
            return {"outputPath": str(output)}
        raise AssertionError(f"Unexpected OBS mutation or tool: {name}")


@pytest.fixture
def fake(monkeypatch, tmp_path):
    obs = FakeOBS(tmp_path)
    monkeypatch.setattr(operator, "call", obs.call)
    monkeypatch.setattr(operator, "RECORDINGS_ROOT", tmp_path / "recorded-demo")
    monkeypatch.setattr(operator, "START_STATUS_ATTEMPTS", 3)
    monkeypatch.setattr(operator, "START_STATUS_INTERVAL", 0)
    args = argparse.Namespace(scene="Crossroads Battle", record_dir=tmp_path / "recorded-demo" / "demo-1")
    return obs, args


def run(coroutine):
    return asyncio.run(coroutine)


def test_explicit_start_stop_verifies_output_and_restores_previous_directory(fake):
    obs, args = fake
    started = run(operator.start_recording(None, args))
    assert started["recording"] is True and obs.active
    journal = Path(started["journal"])
    assert journal.stat().st_mode & 0o777 == 0o600
    stopped = run(operator.stop_recording(None, args))
    assert stopped["recording"] is False and not obs.active
    assert Path(stopped["output_path"]).is_file() and stopped["bytes"] > 0
    assert stopped["previous_directory_restored"] and obs.directory == obs.original
    assert run(operator.stop_recording(None, args))["already_stopped"]
    assert [name for name, _ in obs.calls].count("start_record") == 1
    assert [name for name, _ in obs.calls].count("stop_record") == 1
    assert not any("stream" in name and name != "get_stream_status" for name, _ in obs.calls)
    assert not any(name.startswith("set_") and name != "set_record_directory" for name, _ in obs.calls)


@pytest.mark.parametrize("existing", ["recording", "streaming", "other-scene"])
def test_start_refuses_existing_outputs_and_unselected_scene(fake, existing):
    obs, args = fake
    obs.active = existing == "recording"
    obs.streaming = existing == "streaming"
    if existing == "other-scene":
        obs.scene = "Somebody else's scene"
    with pytest.raises(RuntimeError):
        run(operator.start_recording(None, args))
    assert all(name.startswith("get_") for name, _ in obs.calls)
    assert not args.record_dir.exists()


def test_setup_does_not_alter_a_running_recording(fake):
    obs, args = fake
    obs.active = True
    with pytest.raises(RuntimeError, match="already recording"):
        run(operator.setup(None, args))
    assert obs.calls == [("get_record_status", {})]


def test_recording_path_cannot_escape_ignored_workspace_directory(fake, tmp_path):
    obs, args = fake
    args.record_dir = tmp_path / "outside-recorded-demo"
    with pytest.raises(ValueError, match="dedicated subdirectory"):
        run(operator.start_recording(None, args))
    assert all(name.startswith("get_") for name, _ in obs.calls)


def test_stop_refuses_unknown_or_unowned_recording(fake):
    obs, args = fake
    obs.active = True
    with pytest.raises(ValueError, match="journal"):
        run(operator.stop_recording(None, args))
    assert obs.calls == []
    args.record_dir = None
    with pytest.raises(ValueError, match="requires --record-dir"):
        run(operator.stop_recording(None, args))
    assert obs.calls == []


@pytest.mark.parametrize("replacement", ["directory", "newer-recording", "already-stopped"])
def test_stop_refuses_a_recording_replaced_outside_this_helper(fake, replacement, tmp_path):
    obs, args = fake
    run(operator.start_recording(None, args))
    if replacement == "directory":
        obs.directory = str(tmp_path / "other-output")
    elif replacement == "newer-recording":
        journal = args.record_dir / operator.RECORDING_JOURNAL
        state = json.loads(journal.read_text())
        state["started_at"] = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        operator.write_recording_journal(args.record_dir, state)
        obs.duration = 1000
    else:
        obs.active = False
    with pytest.raises(RuntimeError):
        run(operator.stop_recording(None, args))
    assert not any(name == "stop_record" for name, _ in obs.calls)


def test_lost_start_response_preserves_explicit_stop_recovery(fake):
    obs, args = fake
    obs.start_error = "after-start"
    with pytest.raises(RuntimeError, match="lost"):
        run(operator.start_recording(None, args))
    assert obs.active and obs.directory == str(args.record_dir)
    state = json.loads((args.record_dir / operator.RECORDING_JOURNAL).read_text())
    assert state["state"] == "start_requested"
    assert run(operator.stop_recording(None, args))["output_path"].endswith(".mkv")


def test_unconfirmed_start_preserves_directory_without_stopping_anything(fake):
    obs, args = fake
    obs.start_error = "before-start"
    with pytest.raises(RuntimeError):
        run(operator.start_recording(None, args))
    assert not obs.active and obs.directory == str(args.record_dir)
    assert not any(name == "stop_record" for name, _ in obs.calls)
    state = json.loads((args.record_dir / operator.RECORDING_JOURNAL).read_text())
    assert state["state"] == "start_requested"


def test_async_start_waits_for_recording_confirmation(fake):
    obs, args = fake
    obs.start_delay_reads = 2
    result = run(operator.start_recording(None, args))
    assert result["recording"] and obs.active
    assert obs.directory == str(args.record_dir)
    assert json.loads((args.record_dir / operator.RECORDING_JOURNAL).read_text())["state"] == "recording"


def test_failure_before_start_dispatch_restores_directory_while_idle(fake):
    obs, args = fake
    obs.directory_error = True
    with pytest.raises(RuntimeError, match="before start dispatch"):
        run(operator.start_recording(None, args))
    assert not obs.active and obs.directory == obs.original
    assert not any(name in ("start_record", "stop_record") for name, _ in obs.calls)
    assert json.loads((args.record_dir / operator.RECORDING_JOURNAL).read_text())["state"] == "failed"


def test_start_poll_timeout_preserves_pending_recording_for_explicit_stop(fake):
    obs, args = fake
    obs.start_delay_reads = 10
    with pytest.raises(RuntimeError, match="not confirmed yet"):
        run(operator.start_recording(None, args))
    assert obs.directory == str(args.record_dir)
    assert (
        json.loads((args.record_dir / operator.RECORDING_JOURNAL).read_text())["state"] == "start_requested"
    )
    assert [name for name, _ in obs.calls].count("get_record_status") == 5
    obs.pending_start_reads = 0
    assert run(operator.stop_recording(None, args))["output_path"].endswith(".mkv")


def test_directory_restore_failure_does_not_lose_saved_video_and_can_retry(fake):
    obs, args = fake
    run(operator.start_recording(None, args))
    obs.restore_error = True
    stopped = run(operator.stop_recording(None, args))
    assert Path(stopped["output_path"]).is_file() and not stopped["previous_directory_restored"]
    obs.restore_error = False
    repeated = run(operator.stop_recording(None, args))
    assert repeated["already_stopped"] and repeated["previous_directory_restored"]
    assert [name for name, _ in obs.calls].count("stop_record") == 1


def test_same_recording_directory_is_not_reused(fake):
    obs, args = fake
    run(operator.start_recording(None, args))
    run(operator.stop_recording(None, args))
    with pytest.raises(ValueError, match="already has a recording journal"):
        run(operator.start_recording(None, args))
    assert [name for name, _ in obs.calls].count("start_record") == 1


@pytest.mark.parametrize("mode", ["outside", "missing", "empty", "invalid"])
def test_unverified_stopped_output_restores_directory_and_does_not_stop_a_new_recording(fake, mode):
    obs, args = fake
    run(operator.start_recording(None, args))
    obs.output_mode = mode
    with pytest.raises(RuntimeError, match="could not be verified"):
        run(operator.stop_recording(None, args))
    state = json.loads((args.record_dir / operator.RECORDING_JOURNAL).read_text())
    assert state["state"] == "stopped_unverified" and state["previous_directory_restored"]
    assert state["output_path"] is None and obs.directory == obs.original
    obs.active = True
    with pytest.raises(RuntimeError, match="no new recording was stopped"):
        run(operator.stop_recording(None, args))
    assert obs.active and [name for name, _ in obs.calls].count("stop_record") == 1


def test_repeated_successful_stop_does_not_stop_new_recording(fake):
    obs, args = fake
    run(operator.start_recording(None, args))
    first = run(operator.stop_recording(None, args))
    obs.active = True
    repeated = run(operator.stop_recording(None, args))
    assert repeated["output_path"] == first["output_path"] and repeated["already_stopped"]
    assert obs.active and [name for name, _ in obs.calls].count("stop_record") == 1


def test_malformed_journal_is_rejected_before_obs_calls(fake):
    obs, args = fake
    args.record_dir.mkdir(parents=True)
    (args.record_dir / operator.RECORDING_JOURNAL).write_text("[]")
    with pytest.raises(ValueError, match="journal does not match"):
        run(operator.stop_recording(None, args))
    assert not obs.calls


@pytest.mark.parametrize("failure", ["transport", "tool", "invalid-json", "nonobject-json"])
def test_mcp_errors_do_not_echo_arbitrary_sensitive_details(failure):
    secret = "DO-NOT-LOG-TEST-PASSWORD"

    async def tool(name, arguments):
        if failure == "transport":
            raise RuntimeError(secret)
        payload = json.dumps([secret]) if failure == "nonobject-json" else secret
        return SimpleNamespace(
            isError=failure == "tool", content=[SimpleNamespace(type="text", text=payload)]
        )

    with pytest.raises(RuntimeError) as error:
        run(operator.call(SimpleNamespace(call_tool=tool), "get_record_status"))
    assert secret not in str(error.value)


def test_malformed_private_configuration_is_not_echoed(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    config = tmp_path / "obs-studio"
    config.mkdir()
    secret = "DO-NOT-LOG-TEST-PASSWORD"
    (config / "global.ini").write_text(secret)
    with pytest.raises(ValueError, match="configuration could not be read") as error:
        operator.connection_environment()
    assert secret not in str(error.value)


def test_top_level_transport_exception_group_is_sanitized(monkeypatch, capsys):
    secret = "DO-NOT-LOG-TEST-PASSWORD"

    async def failed(args):
        raise ExceptionGroup("Transport failed", [RuntimeError(secret)])

    monkeypatch.setattr(operator, "operate", failed)
    monkeypatch.setattr(operator.sys, "argv", ["obs_battle.py", "status"])
    with pytest.raises(SystemExit) as error:
        operator.main()
    assert error.value.code == 1
    output = capsys.readouterr()
    assert "OBS MCP operation failed" in output.err and secret not in output.err + output.out
