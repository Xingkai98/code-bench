#!/usr/bin/env python3
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BUILD = ROOT / ".test_build"


def run(cmd, **kwargs):
    return subprocess.run(
        cmd,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=kwargs.pop("timeout", 20),
        **kwargs,
    )


def build():
    if BUILD.exists():
        shutil.rmtree(BUILD)
    proc = run(["cmake", "-S", ".", "-B", str(BUILD)])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    proc = run(["cmake", "--build", str(BUILD)])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    exe = BUILD / "cparser"
    assert exe.exists(), "build did not produce cparser"
    return exe


def invoke(exe, mode, input_text):
    return subprocess.run(
        [str(exe), mode],
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=5,
    )


def main():
    exe = build()

    proc = invoke(exe, "--eval", "2+3*4\n")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "14", proc.stdout

    proc = invoke(exe, "--parse", "(2+3)*4")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK", proc.stdout

    proc = invoke(exe, "--tokens", "int main;\nvalue = 42;")
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.strip().splitlines()
    assert "KEYWORD:int" in lines, lines
    assert "IDENT:value" in lines, lines
    assert "INT:42" in lines, lines

    proc = invoke(exe, "--parse", "1+")
    assert proc.returncode != 0, "malformed input should fail"
    assert proc.stderr.startswith("ERROR:"), proc.stderr


if __name__ == "__main__":
    main()
