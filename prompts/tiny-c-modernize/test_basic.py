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
        timeout=kwargs.pop("timeout", 25),
        **kwargs,
    )


def build():
    if BUILD.exists():
        shutil.rmtree(BUILD)
    for cmd in (["cmake", "-S", ".", "-B", str(BUILD)], ["cmake", "--build", str(BUILD)]):
        proc = run(cmd)
        assert proc.returncode == 0, proc.stdout + proc.stderr
    exe = BUILD / "cparser"
    assert exe.exists(), "build did not produce cparser"
    return exe


def invoke(exe, mode, text):
    return subprocess.run(
        [str(exe), mode],
        input=text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=5,
    )


def main():
    exe = build()

    proc = invoke(exe, "--tokens", "int main() {\n\treturn 12;\n}")
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.strip().splitlines()
    assert lines[:4] == [
        "KEYWORD:int:1:1",
        "IDENT:main:1:5",
        "LPAREN:(:1:9",
        "RPAREN:):1:10",
    ], lines
    assert "KEYWORD:return:2:2" in lines, lines
    assert "INT:12:2:9" in lines, lines

    proc = invoke(exe, "--eval", "-(2+3)*4")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "-20", proc.stdout

    proc = invoke(exe, "--parse", "int main() { int x = 1 + 2 * 3; return x; }")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK", proc.stdout

    proc = invoke(exe, "--parse", "int main() { return ; }")
    assert proc.returncode != 0, "malformed input should fail"
    assert proc.stderr.startswith("ERROR:"), proc.stderr
    assert ":" in proc.stderr[len("ERROR:"):], proc.stderr


if __name__ == "__main__":
    main()
