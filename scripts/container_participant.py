"""Run one optional Codex participant inside an isolated Docker container.

Build once with --build-image; --check verifies login, role, API and photo access
without calling a model. A normal invocation starts a model, so use it only after
the operator has started the recording. Only lifecycle JSON is public stdout.
"""

import argparse
import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "crossroads-participant:local"
NETWORK = "crossroads_default"

# The check deliberately captures all child output: never echo an auth response,
# private room context, CLI exception payload or token to the recording terminal.
CHECK_CODE = r"""
import json, os, subprocess, sys, urllib.request
def command(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=35)
    if result.returncode:
        raise RuntimeError("readiness command failed")
    return result.stdout
try:
    role, room = sys.argv[1:]
    status = subprocess.run(["codex", "login", "status"], capture_output=True, text=True, timeout=15)
    assert status.returncode == 0 and "Logged in" in status.stdout + status.stderr
    version = command(["codex", "--version"]).strip()
    with urllib.request.urlopen("http://api:8100/api/v1/health", timeout=5) as response:
        assert response.status == 200
    identity = json.loads(command(["market", "auth", "whoami", "--json"]))
    assert identity["workspace"]["role"] == role
    assert identity["credential"]["kind"] == "agent" and identity["can_approve"] is False
    context = json.loads(command(["market", "negotiations", "context", room, "--json"]))
    product_id = context["product_id"]
    photos = json.loads(command(["market", "products", "images", "read", product_id,
                                "--all", "--output-dir", "/workspace/images", "--json"]))
    assert photos["images"] and all(os.path.isfile(p["path"]) for p in photos["images"])
    assert os.getuid() != 0
    with open(os.path.join(os.environ["CODEX_HOME"], "readiness.tmp"), "w") as marker:
        marker.write("ready")
    os.unlink(os.path.join(os.environ["CODEX_HOME"], "readiness.tmp"))
    print(json.dumps({"ready": True, "role": role, "codex_version": version,
                      "image_count": len(photos["images"]), "uid": os.getuid()}))
except Exception:
    print("Readiness failed: inspect API reachability, agent role/scopes and Codex login locally.", file=sys.stderr)
    sys.exit(1)
"""


def run_capture(command, **kwargs):
    """Capture failure details locally; callers never print subprocess payloads."""
    return subprocess.run(command, capture_output=True, text=True, check=True, **kwargs)


def private_json(path, value):
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, indent=2)


def private_log(path):
    return os.fdopen(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600), "w")


def private_file(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
        raise ValueError("Configuration/auth must be private regular files (chmod 600)")
    return path.resolve()


def private_directory(path):
    if path.is_symlink():
        raise ValueError("Participant directories cannot be symlinks")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.stat().st_mode & 0o077:
        raise ValueError("Participant directories must be private (chmod 700)")
    return path.resolve()


def prepare(config_path, role, auth_path):
    config = json.loads(private_file(config_path).read_text())
    private_file(auth_path)
    # Never mount config: it contains both identities. Each readonly profile has
    # only its selected role, and logs/metadata stay outside the mounted workspace.
    directory = private_directory(Path(config["run_dir"]).parent / "participants" / role)
    workspace = private_directory(directory / "workspace")
    profile = directory / "market-profile.json"
    selected = {
        "profiles": {
            "default": {
                "base_url": "http://api:8100",
                "token": config[role]["token"],
                "workspace": config[role]["workspace_id"],
            }
        }
    }
    if profile.exists():
        if json.loads(private_file(profile).read_text()) != selected:
            raise ValueError(
                "Existing participant belongs to different credentials; use a fresh run directory"
            )
    else:
        private_json(profile, selected)
    auth = directory / "codex-auth.json"
    if not auth.exists():
        fd = os.open(auth, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as target, auth_path.open("rb") as source:
            shutil.copyfileobj(source, target)
    private_file(auth)
    return config, directory, workspace, profile, auth


def docker_command(workspace, profile, auth, name, image=IMAGE, network=NETWORK):
    uid, gid = os.getuid(), os.getgid()
    if uid == 0:
        raise ValueError("Run the launcher as a non-root host user")
    paths = (workspace, profile, auth)
    if any("," in str(path) or "\n" in str(path) for path in paths):
        raise ValueError("Docker bind paths cannot contain commas or newlines")
    return [
        "docker",
        "create",
        "--name",
        name,
        "--interactive",
        "--init",
        "--network",
        network,
        "--user",
        f"{uid}:{gid}",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges:true",
        "--pids-limit",
        "128",
        "--memory",
        "2g",
        "--cpus",
        "2",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,size=256m,mode=1777",
        "--tmpfs",
        f"/home/participant:rw,nosuid,nodev,size=256m,mode=700,uid={uid},gid={gid}",
        "--tmpfs",
        f"/home/participant/.codex:rw,nosuid,nodev,size=256m,mode=700,uid={uid},gid={gid}",
        "--mount",
        f"type=bind,src={workspace},dst=/workspace",
        "--mount",
        f"type=bind,src={profile},dst=/run/market-profile.json,readonly",
        "--mount",
        f"type=bind,src={auth},dst=/home/participant/.codex/auth.json,readonly",
        image,
    ]


def model_command(model):
    # Docker is the security boundary. No privileged host Codex process is used.
    return [
        "codex",
        "exec",
        "--ignore-user-config",
        "--ignore-rules",
        "--ephemeral",
        "--skip-git-repo-check",
        "--sandbox",
        "danger-full-access",
        "-c",
        'approval_policy="never"',
        "--json",
        "--color",
        "never",
        "--model",
        model,
        "-C",
        "/workspace",
        "-",
    ]


def prompt_for(*args):
    spec = importlib.util.spec_from_file_location(
        "battle_participant", ROOT / "scripts/battle_participant.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.prompt_for(*args)


def lifecycle(event, container_id, role, **extra):
    print(json.dumps({"event": event, "container_id": container_id, "role": role, **extra}), flush=True)


def stop_container(container_id):
    subprocess.run(["docker", "stop", "--time", "10", container_id], capture_output=True, timeout=20)


def wait_started(container_id, child):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        state = json.loads(
            run_capture(
                [
                    "docker",
                    "inspect",
                    "--format",
                    "{{json .State}}",
                    container_id,
                ],
                timeout=5,
            ).stdout
        )
        if state.get("Running") is True and re.fullmatch(r"[0-9T:.Z+-]+", state.get("StartedAt", "")):
            return state["StartedAt"]
        if child.poll() is not None or state.get("Status") in ("exited", "dead"):
            raise ValueError("Container exited before starting")
        time.sleep(0.1)
    raise ValueError("Container did not start within ten seconds")


def execute(args):
    config, directory, workspace, profile, auth = prepare(args.config, args.role, args.auth_file)
    if not args.check and (directory / "process.json").exists():
        raise ValueError("Participant already ran; inspect its private logs and use a fresh run directory")
    name = f"crossroads-{args.role}-{'check-' if args.check else ''}{uuid4().hex[:12]}"
    command = docker_command(workspace, profile, auth, name, args.image, args.network)
    command += (
        ["python", "-c", CHECK_CODE, args.role, config["room_id"]]
        if args.check
        else model_command(args.model)
    )
    container_id = run_capture(command, timeout=30).stdout.strip()
    if not re.fullmatch(r"[a-f0-9]{64}", container_id):
        raise ValueError("Docker returned an invalid container identifier")
    child = None
    try:
        if args.check:
            result = run_capture(["docker", "start", "--attach", container_id], timeout=120)
            readiness = json.loads(result.stdout)
            if readiness.get("ready") is not True or readiness.get("role") != args.role:
                raise ValueError("Container failed readiness validation")
            lifecycle(
                "container_ready",
                container_id,
                args.role,
                **{k: readiness[k] for k in ("codex_version", "image_count", "uid")},
            )
            return 0
        with (
            private_log(directory / "agent.jsonl") as output,
            private_log(directory / "agent.stderr") as errors,
        ):
            child = subprocess.Popen(
                ["docker", "start", "--attach", "--interactive", container_id],
                stdin=subprocess.PIPE,
                stdout=output,
                stderr=errors,
                text=True,
            )
            private_json(
                directory / "process.json",
                {
                    "pid": child.pid,
                    "launcher_pid": os.getpid(),
                    "container_id": container_id,
                    "container_name": name,
                    "role": args.role,
                    "room_id": config["room_id"],
                    "model": args.model,
                },
            )
            try:
                started_at = wait_started(container_id, child)
                lifecycle("container_started", container_id, args.role, started_at=started_at)
                child.communicate(
                    prompt_for(args.role, config["room_id"], args.wait_for_guidance, args.timeout),
                    timeout=args.timeout,
                )
            except (subprocess.TimeoutExpired, KeyboardInterrupt):
                stop_container(container_id)
                try:
                    child.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
                lifecycle("container_exited", container_id, args.role, exit_code=124)
                return 124
            lifecycle("container_exited", container_id, args.role, exit_code=child.returncode)
            return child.returncode
    finally:
        # Stop the workload, not merely the Docker attachment process, on every
        # exceptional path (including an interrupted preflight).
        subprocess.run(["docker", "rm", "--force", container_id], capture_output=True, timeout=20)
        if child is not None and child.poll() is None:
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


def build_image(cli_dir, codex_vendor, image):
    """Send an allowlisted temporary build context, never a repository or home."""
    cli_dir, codex_vendor = cli_dir.resolve(), codex_vendor.resolve()
    with tempfile.TemporaryDirectory(prefix="crossroads-participant-build-") as temporary:
        context = Path(temporary)
        shutil.copy2(ROOT / "docker/Participant.Dockerfile", context / "Dockerfile")
        for source, target in (
            (codex_vendor / "bin/codex", "codex"),
            (codex_vendor / "bin/codex-code-mode-host", "codex-code-mode-host"),
            (codex_vendor / "codex-path/rg", "rg"),
        ):
            shutil.copy2(source, context / target)
        run_capture(
            [
                "uv",
                "export",
                "--frozen",
                "--no-dev",
                "--no-emit-project",
                "--no-hashes",
                "--output-file",
                str(context / "requirements.txt"),
            ],
            cwd=cli_dir,
            timeout=60,
        )
        run_capture(["uv", "build", "--wheel", "--out-dir", str(context / "wheels")], cwd=cli_dir, timeout=60)
        subprocess.run(["docker", "build", "--pull=false", "--tag", image, str(context)], check=True)


def default_vendor():
    binary = shutil.which("codex")
    if not binary:
        return Path("/missing-codex-vendor")
    return (
        Path(binary).resolve().parents[1]
        / "node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--role", choices=("buyer", "seller"))
    parser.add_argument(
        "--model", default="gpt-6-astra", help="Explicit participant model; ignores host user config"
    )
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--wait-for-guidance", action="store_true")
    parser.add_argument(
        "--check", action="store_true", help="Read-only readiness check; does not call a model"
    )
    parser.add_argument(
        "--build-image", action="store_true", help="Build image only; does not start a participant"
    )
    parser.add_argument("--image", default=IMAGE)
    parser.add_argument("--network", default=NETWORK)
    parser.add_argument(
        "--auth-file",
        type=Path,
        default=Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "auth.json",
    )
    parser.add_argument("--cli-dir", type=Path, default=ROOT.parent / "cli")
    parser.add_argument("--codex-vendor", type=Path, default=default_vendor())
    args = parser.parse_args()
    os.umask(0o077)

    def interrupted(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    try:
        if args.build_image:
            build_image(args.cli_dir, args.codex_vendor, args.image)
            return
        if not args.config or not args.role:
            parser.error("--config and --role are required unless building the image")
        if not 30 <= args.timeout <= 3600:
            parser.error("--timeout must be 30..3600 seconds")
        raise SystemExit(execute(args))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        # Exception strings may contain child stdout, private config or credentials.
        parser.exit(
            1, "Container participant failed; check private configuration, Docker, API and login readiness.\n"
        )


if __name__ == "__main__":
    main()
