#!/usr/bin/env python3
"""Quick connectivity test: send 'hi' to each model in config.json."""
import json, os, subprocess, sys, time, shlex
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parent


def resolve_docker():
    """Return (docker_mode, docker_prefix) or None if Docker unavailable."""
    try:
        r = subprocess.run(["docker", "ps"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=5)
        if r.returncode == 0:
            return ("list", ["docker"])
        r = subprocess.run(["sg", "docker", "-c", "docker ps"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        if r.returncode == 0:
            return ("shell", ["sg", "docker", "-c"])
    except Exception:
        pass
    return None


def test_model(model_cfg, image, docker_info):
    name = model_cfg["name"]
    settings = {k: v for k, v in model_cfg.items() if k != "name"}
    settings_path = BENCH_DIR / f".test_{name}.json"
    settings_path.write_text(json.dumps(settings))

    start = time.time()
    try:
        if docker_info is not None:
            docker_mode, docker_prefix = docker_info
            docker_args = [
                "run", "--rm",
                "--user", f"{os.getuid()}:{os.getgid()}",
                "-e", "HOME=/tmp",
                "-v", f"{settings_path}:/tmp/settings.json:ro",
                image,
                "claude", "-p", "--bare",
                "--dangerously-skip-permissions",
                "--settings", "/tmp/settings.json",
                "--output-format", "stream-json",
                "--verbose", "hi",
            ]
            if docker_mode == "shell":
                cmd = docker_prefix + ["docker " + shlex.join(docker_args)]
            else:
                cmd = docker_prefix + docker_args
        else:
            cmd = ["claude", "-p", "--bare",
                   "--dangerously-skip-permissions",
                   "--settings", str(settings_path),
                   "--output-format", "stream-json",
                   "--verbose", "hi"]

        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        elapsed = time.time() - start

        has_result = '"type":"result"' in proc.stdout
        ok = proc.returncode == 0 and has_result
        icon = "✅" if ok else "❌"
        extra = ""
        if not ok and not has_result:
            extra = proc.stdout.strip()[:150] or proc.stderr.strip()[:150]
        return icon, ok, elapsed, extra
    except subprocess.TimeoutExpired:
        return "⏱", False, 120, "timeout"
    except Exception as e:
        return "❌", False, time.time() - start, str(e)[:100]
    finally:
        settings_path.unlink(missing_ok=True)


def main():
    cfg = json.load(open(BENCH_DIR / "config.json"))
    models = cfg["models"]
    sandbox = cfg.get("sandbox", {})
    image = sandbox.get("image", "code-bench-sandbox:latest")

    docker_info = resolve_docker() if sandbox.get("enabled") else None
    mode = f"Docker ({' '.join(docker_info[1])})" if docker_info else "direct"

    print(f"Testing {len(models)} models ({mode})...\n")

    all_ok = True
    for m in models:
        name = m["name"]
        model_id = m["model"]
        url = m["env"]["ANTHROPIC_BASE_URL"]
        print(f"  {name} ({model_id}) → ", end="", flush=True)
        icon, ok, elapsed, extra = test_model(m, image, docker_info)
        print(f"{icon} {elapsed:.1f}s", end="")
        if extra:
            print(f"  [{extra}]", end="")
        print()
        if not ok:
            all_ok = False

    print(f"\n{'ALL OK ✅' if all_ok else 'SOME FAILED ❌'}")

if __name__ == "__main__":
    main()
