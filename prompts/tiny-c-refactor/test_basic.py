#!/usr/bin/env python3
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BUILD = ROOT / ".test_build"


def build():
    if BUILD.exists():
        shutil.rmtree(BUILD)
    for cmd in (["cmake", "-S", ".", "-B", str(BUILD)], ["cmake", "--build", str(BUILD)]):
        proc = subprocess.run(cmd, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=25)
        assert proc.returncode == 0, proc.stdout + proc.stderr
    return BUILD / "cparser"


def invoke(exe, mode, text):
    return subprocess.run([str(exe), mode], input=text, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)


def main():
    exe = build()
    proc = invoke(exe, "--eval", "(2+3)*4")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "20", proc.stdout

    proc = invoke(exe, "--tokens", "int\tvalue = 12;")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().splitlines() == [
        "KEYWORD:int",
        "IDENT:value",
        "OP:=",
        "INT:12",
        "SEMI:;",
    ]

    proc = invoke(exe, "--parse", "1+")
    assert proc.returncode != 0
    assert proc.stderr.startswith("ERROR:"), proc.stderr


if __name__ == "__main__":
    main()
