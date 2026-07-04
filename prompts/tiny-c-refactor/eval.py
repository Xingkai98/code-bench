#!/usr/bin/env python3
"""Hidden eval for tiny-c-refactor."""
import json
import re
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
    for cmd in (["cmake", "-S", ".", "-B", str(build_dir)], ["cmake", "--build", str(build_dir)]):
        proc = run(cmd, cwd=workdir)
        if proc.returncode != 0:
            raise AssertionError((proc.stdout + proc.stderr)[-2500:])
    exe = build_dir / "cparser"
    if not exe.exists():
        raise AssertionError("cparser executable missing")
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
        raise AssertionError(f"{msg}: expected nonzero exit")
    if not proc.stderr.startswith("ERROR:"):
        raise AssertionError(f"{msg}: stderr must start with ERROR:, got {proc.stderr!r}")


def read_sources(workdir):
    text = ""
    for pattern in ("*.cpp", "src/*.cpp", "include/*.h", "include/*.hpp"):
        for path in workdir.glob(pattern):
            text += f"\n// {path}\n" + path.read_text(errors="replace")
    return text


def main():
    workdir = Path(sys.argv[1])
    results = {}
    score = 0.0
    exe_holder = {}

    def check_build_and_visible():
        exe_holder["exe"] = build_project(workdir)
        proc = run([sys.executable, "test_basic.py"], cwd=workdir, timeout=35)
        if proc.returncode != 0:
            raise AssertionError((proc.stdout + proc.stderr)[-2000:])

    def check_behavior_compat():
        exe = exe_holder["exe"]
        token_proc = invoke(exe, "--tokens", "int value = 12;\nreturn value!=0;")
        assert_ok(token_proc, "tokens")
        expected = [
            "KEYWORD:int",
            "IDENT:value",
            "OP:=",
            "INT:12",
            "SEMI:;",
            "KEYWORD:return",
            "IDENT:value",
            "OP:!=",
            "INT:0",
            "SEMI:;",
        ]
        if token_proc.stdout.strip().splitlines() != expected:
            raise AssertionError(token_proc.stdout)

        cases = {
            "2+3*4": "14",
            "(2+3)*4": "20",
            "8-3-2": "3",
            "8/4/2": "1",
            "12 + /* comment */\n3*4": "24",
        }
        for expr, expected_value in cases.items():
            proc = invoke(exe, "--eval", expr)
            assert_ok(proc, expr)
            if proc.stdout.strip() != expected_value:
                raise AssertionError(f"{expr}: got {proc.stdout!r}, expected {expected_value}")
            proc = invoke(exe, "--parse", expr)
            assert_ok(proc, "parse " + expr)
            if proc.stdout.strip() != "OK":
                raise AssertionError(proc.stdout)

        for bad in ["", "1+", "1 2", "(1+2", "/* open"]:
            assert_fail(invoke(exe, "--parse", bad, timeout=3), f"reject {bad!r}")

    def check_token_api_source_shape():
        src = read_sources(workdir)
        if not re.search(r"enum\s+class\s+TokenType\b", src):
            raise AssertionError("enum class TokenType missing")
        if not re.search(r"\b(struct|class)\s+Token\b", src):
            raise AssertionError("Token struct/class missing")
        if not re.search(r"std::vector\s*<\s*Token\s*>\s+\w*tokenize\s*\(", src):
            raise AssertionError("Lexer tokenize API returning std::vector<Token> missing")

    def check_parser_api_compiles():
        snippet = workdir / ".eval_api_check.cpp"
        snippet.write_text(
            r'''
#include "lexer.h"
#include "parser.h"
#include <string>
#include <vector>

int main() {
    Lexer lexer;
    std::vector<Token> tokens = lexer.tokenize("1 + 2 * 3");
    if (tokens.empty()) return 2;
    Parser parser(tokens);
    if (!parser.parse()) return 3;
    if (parser.evaluate() != 7) return 4;
    return 0;
}
'''
        )
        proc = run(
            [
                "g++",
                "-std=c++17",
                "-I",
                "include",
                str(snippet),
                "src/lexer.cpp",
                "src/parser.cpp",
                "-o",
                ".eval_api_check",
            ],
            cwd=workdir,
            timeout=30,
        )
        if proc.returncode != 0:
            raise AssertionError((proc.stdout + proc.stderr)[-2500:])
        proc = run(["./.eval_api_check"], cwd=workdir, timeout=5)
        if proc.returncode != 0:
            raise AssertionError(f"API check executable returned {proc.returncode}")

    def check_no_global_or_brittle_state():
        src = read_sources(workdir)
        banned = [
            r"extern\s+.*g_token",
            r"\bg_token_(types|texts)\b",
            r"\bg_lexer_error\b",
            r"\bchar\s+\w+\s*\[\s*(50|100|1000|BUFFER_SIZE)\s*\]",
            r"system\s*\(",
            r"\bexit\s*\(",
        ]
        hits = [pat for pat in banned if re.search(pat, src)]
        if hits:
            raise AssertionError("banned brittle/global patterns found: " + ", ".join(hits))

    checks = [
        ("build_and_visible", 0.10, check_build_and_visible),
        ("behavior_compat", 0.25, check_behavior_compat),
        ("token_api_source_shape", 0.20, check_token_api_source_shape),
        ("parser_api_compiles", 0.30, check_parser_api_compiles),
        ("no_global_or_brittle_state", 0.15, check_no_global_or_brittle_state),
    ]

    for name, weight, fn in checks:
        score += run_check(results, name, weight, fn)

    if not results.get("behavior_compat", {}).get("passed", False):
        score = min(score, 0.45)
        results["behavior_cap"] = {
            "passed": False,
            "weight": 0.0,
            "cap": 0.45,
            "reason": "behavior_compat failed",
        }

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
