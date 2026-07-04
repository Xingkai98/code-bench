#!/usr/bin/env python3
"""Hidden eval for tiny-c-bugfix."""
import json
import shutil
import subprocess
import sys
from pathlib import Path


def run_check(results, name, weight, fn):
    try:
        fn()
        results[name] = {"passed": True, "weight": weight}
        return weight
    except Exception as exc:
        results[name] = {
            "passed": False,
            "weight": weight,
            "error": f"{type(exc).__name__}: {exc}",
        }
        return 0.0


def run(cmd, cwd, **kwargs):
    return subprocess.run(
        cmd,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=kwargs.pop("timeout", 25),
        **kwargs,
    )


def build_project(workdir):
    build_dir = workdir / ".eval_build"
    if build_dir.exists():
        shutil.rmtree(build_dir)
    proc = run(["cmake", "-S", ".", "-B", str(build_dir)], cwd=workdir)
    if proc.returncode != 0:
        raise AssertionError("cmake configure failed:\n" + (proc.stdout + proc.stderr)[-2000:])
    proc = run(["cmake", "--build", str(build_dir)], cwd=workdir)
    if proc.returncode != 0:
        raise AssertionError("cmake build failed:\n" + (proc.stdout + proc.stderr)[-2000:])
    exe = build_dir / "cparser"
    if not exe.exists():
        raise AssertionError("cparser executable missing after build")
    return exe


def invoke(exe, mode, input_text, timeout=5):
    return subprocess.run(
        [str(exe), mode],
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )


def assert_ok(proc, msg):
    if proc.returncode != 0:
        raise AssertionError(f"{msg}: rc={proc.returncode}, stderr={proc.stderr!r}, stdout={proc.stdout!r}")


def assert_fail(proc, msg):
    if proc.returncode == 0:
        raise AssertionError(f"{msg}: expected nonzero exit, stdout={proc.stdout!r}")
    if not proc.stderr.startswith("ERROR:"):
        raise AssertionError(f"{msg}: stderr must start with ERROR:, got {proc.stderr!r}")


def main():
    workdir = Path(sys.argv[1])
    results = {}
    score = 0.0
    exe_holder = {}

    def get_exe():
        if "exe" not in exe_holder:
            raise AssertionError("build_and_cli did not pass; executable unavailable")
        return exe_holder["exe"]

    def check_build_and_cli():
        exe_holder["exe"] = build_project(workdir)
        for mode in ["--tokens", "--parse", "--eval"]:
            proc = invoke(exe_holder["exe"], mode, "1")
            assert_ok(proc, f"{mode} should run")

    def check_visible_basic():
        proc = run([sys.executable, "test_basic.py"], cwd=workdir, timeout=35)
        if proc.returncode != 0:
            raise AssertionError((proc.stdout + proc.stderr)[-2000:])

    def check_tokens_core():
        exe = get_exe()
        source = "int main;\nfoo_1 = 42, bar!=7;"
        proc = invoke(exe, "--tokens", source)
        assert_ok(proc, "tokenize core source")
        lines = proc.stdout.strip().splitlines()
        expected = [
            "KEYWORD:int",
            "IDENT:main",
            "SEMI:;",
            "IDENT:foo_1",
            "OP:=",
            "INT:42",
            "COMMA:,",
            "IDENT:bar",
            "OP:!=",
            "INT:7",
            "SEMI:;",
        ]
        if lines != expected:
            raise AssertionError(f"token output mismatch:\n got {lines!r}\nwant {expected!r}")

    def check_whitespace_and_comments():
        exe = get_exe()
        source = "\t12 + /* hidden + 99 */\n3 // trailing\n* 4"
        proc = invoke(exe, "--eval", source)
        assert_ok(proc, "comments and whitespace")
        if proc.stdout.strip() != "24":
            raise AssertionError(f"expected 24, got {proc.stdout!r}")

        proc = invoke(exe, "--tokens", "return\t15\n// comment\n+ 2")
        assert_ok(proc, "tokenize with tabs and line comments")
        if proc.stdout.strip().splitlines() != ["KEYWORD:return", "INT:15", "OP:+", "INT:2"]:
            raise AssertionError(proc.stdout)

    def check_arithmetic_semantics():
        exe = get_exe()
        cases = {
            "2+3*4": "14",
            "(2+3)*4": "20",
            "8-3-2": "3",
            "8/4/2": "1",
            "42": "42",
            "18/(2+1)+5*2": "16",
            "100-4*6+8/2": "80",
        }
        for expr, expected in cases.items():
            proc = invoke(exe, "--eval", expr)
            assert_ok(proc, expr)
            if proc.stdout.strip() != expected:
                raise AssertionError(f"{expr}: got {proc.stdout!r}, expected {expected!r}")
            proc = invoke(exe, "--parse", expr)
            assert_ok(proc, f"parse {expr}")
            if proc.stdout.strip() != "OK":
                raise AssertionError(f"parse {expr}: expected OK, got {proc.stdout!r}")

    def check_malformed_inputs():
        exe = get_exe()
        bad_inputs = ["", "1+", "*2", "(1+2", "1 2", "1 + @", "/* never closed"]
        for text in bad_inputs:
            proc = invoke(exe, "--parse", text, timeout=3)
            assert_fail(proc, f"parse should reject {text!r}")

    def check_robustness_and_state_isolation():
        exe = get_exe()
        long_expr = "+".join(["1"] * 300)
        proc = invoke(exe, "--eval", long_expr, timeout=5)
        assert_ok(proc, "long expression")
        if proc.stdout.strip() != "300":
            raise AssertionError(f"long expression result mismatch: {proc.stdout!r}")

        for expr, expected in [("1+2", "3"), ("10*3", "30"), ("7-4", "3")]:
            proc = invoke(exe, "--eval", expr)
            assert_ok(proc, f"repeat {expr}")
            if proc.stdout.strip() != expected:
                raise AssertionError(f"state leaked for {expr}: {proc.stdout!r}")

    checks = [
        ("build_and_cli", 0.10, check_build_and_cli),
        ("visible_basic", 0.10, check_visible_basic),
        ("tokens_core", 0.15, check_tokens_core),
        ("whitespace_and_comments", 0.15, check_whitespace_and_comments),
        ("arithmetic_semantics", 0.25, check_arithmetic_semantics),
        ("malformed_inputs", 0.20, check_malformed_inputs),
        ("robustness_and_state_isolation", 0.05, check_robustness_and_state_isolation),
    ]

    for name, weight, fn in checks:
        score += run_check(results, name, weight, fn)

    passed = [name for name, item in results.items() if item["passed"]]
    failed = [name for name, item in results.items() if not item["passed"]]
    summary = f"passed {len(passed)}/{len(results)} checks"
    if failed:
        summary += f"; failed: {', '.join(failed)}"

    print(json.dumps({
        "score": round(score, 3),
        "details": results,
        "summary": summary,
    }))


if __name__ == "__main__":
    main()
