#!/usr/bin/env python3
"""Hidden eval for tiny-c-modernize."""
import json
import random
import re
import shutil
import subprocess
import sys
from pathlib import Path


def run_check(results, name, weight, fn):
    try:
        extra = fn()
        results[name] = {"passed": True, "weight": weight}
        if isinstance(extra, dict):
            results[name].update(extra)
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
        timeout=kwargs.pop("timeout", 30),
        **kwargs,
    )


def build_project(workdir):
    build_dir = workdir / ".eval_build"
    if build_dir.exists():
        shutil.rmtree(build_dir)
    for cmd in (["cmake", "-S", ".", "-B", str(build_dir)], ["cmake", "--build", str(build_dir)]):
        proc = run(cmd, cwd=workdir, timeout=40)
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
        raise AssertionError(f"{msg}: expected nonzero exit, stdout={proc.stdout!r}")
    if not proc.stderr.startswith("ERROR:"):
        raise AssertionError(f"{msg}: stderr must start with ERROR:, got {proc.stderr!r}")
    if not re.search(r"\b\d+:\d+\b", proc.stderr):
        raise AssertionError(f"{msg}: stderr must contain line:column, got {proc.stderr!r}")


def parse_token_lines(output):
    tokens = []
    for line in output.strip().splitlines():
        parts = line.split(":")
        if len(parts) != 4:
            raise AssertionError(f"bad token line {line!r}; expected TYPE:lexeme:line:column")
        typ, lexeme, line_no, col_no = parts
        try:
            line_i = int(line_no)
            col_i = int(col_no)
        except ValueError as exc:
            raise AssertionError(f"bad token position in {line!r}") from exc
        tokens.append((typ, lexeme, line_i, col_i))
    return tokens


def cxx_div(a, b):
    if b == 0:
        raise ZeroDivisionError
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b >= 0) else -q


def gen_expr(rng, depth=0):
    if depth >= 4 or rng.random() < 0.28:
        value = rng.randint(0, 40)
        expr = str(value)
        if rng.random() < 0.18:
            expr = f"+{expr}"
        if rng.random() < 0.15:
            expr = f"-{expr}"
            value = -value
        return expr, value

    if rng.random() < 0.18:
        inner, value = gen_expr(rng, depth + 1)
        return f"-({inner})", -value

    left, left_value = gen_expr(rng, depth + 1)
    right, right_value = gen_expr(rng, depth + 1)
    op = rng.choice(["+", "-", "*", "/"])
    if op == "/" and right_value == 0:
        right, right_value = "1", 1

    if op == "+":
        value = left_value + right_value
    elif op == "-":
        value = left_value - right_value
    elif op == "*":
        value = left_value * right_value
    else:
        value = cxx_div(left_value, right_value)

    spacer = rng.choice([" ", "  ", "\t", "\n"])
    return f"({left}){spacer}{op}{spacer}({right})", value


def main():
    workdir = Path(sys.argv[1])
    results = {}
    score = 0.0
    exe_holder = {}

    def get_exe():
        if "exe" not in exe_holder:
            raise AssertionError("build_and_visible did not pass; executable unavailable")
        return exe_holder["exe"]

    def check_build_and_visible():
        exe_holder["exe"] = build_project(workdir)
        proc = run([sys.executable, "test_basic.py"], cwd=workdir, timeout=45)
        if proc.returncode != 0:
            raise AssertionError((proc.stdout + proc.stderr)[-2500:])

    def check_token_contract_basic():
        exe = get_exe()
        src = "int main() { return value!=0, x<=12; }"
        proc = invoke(exe, "--tokens", src)
        assert_ok(proc, "token contract")
        got = [(t, x) for t, x, _, _ in parse_token_lines(proc.stdout)]
        expected = [
            ("KEYWORD", "int"),
            ("IDENT", "main"),
            ("LPAREN", "("),
            ("RPAREN", ")"),
            ("LBRACE", "{"),
            ("KEYWORD", "return"),
            ("IDENT", "value"),
            ("OP", "!="),
            ("INT", "0"),
            ("COMMA", ","),
            ("IDENT", "x"),
            ("OP", "<="),
            ("INT", "12"),
            ("SEMI", ";"),
            ("RBRACE", "}"),
        ]
        if got != expected:
            raise AssertionError(f"token stream mismatch:\n got {got!r}\nwant {expected!r}")

    def check_token_positions():
        exe = get_exe()
        src = "int main() {\n\treturn 12 + 3;\n}\n"
        proc = invoke(exe, "--tokens", src)
        assert_ok(proc, "token positions")
        tokens = parse_token_lines(proc.stdout)
        expected = [
            ("KEYWORD", "int", 1, 1),
            ("IDENT", "main", 1, 5),
            ("LPAREN", "(", 1, 9),
            ("RPAREN", ")", 1, 10),
            ("LBRACE", "{", 1, 12),
            ("KEYWORD", "return", 2, 2),
            ("INT", "12", 2, 9),
            ("OP", "+", 2, 12),
            ("INT", "3", 2, 14),
            ("SEMI", ";", 2, 15),
            ("RBRACE", "}", 3, 1),
        ]
        if tokens != expected:
            raise AssertionError(f"position mismatch:\n got {tokens!r}\nwant {expected!r}")

    def check_expression_fixed():
        exe = get_exe()
        cases = {
            "2+3*4": "14",
            "(2+3)*4": "20",
            "8-3-2": "3",
            "8/4/2": "1",
            "-(2+3)*4": "-20",
            "+7 + -3 * 2": "1",
            "100 - 4 * (6 + 2) / 4": "92",
            "18/(2+1)+5*2": "16",
        }
        for expr, expected in cases.items():
            proc = invoke(exe, "--eval", expr)
            assert_ok(proc, expr)
            if proc.stdout.strip() != expected:
                raise AssertionError(f"{expr}: got {proc.stdout!r}, expected {expected!r}")
            parse_proc = invoke(exe, "--parse", expr)
            assert_ok(parse_proc, "parse " + expr)
            if parse_proc.stdout.strip() != "OK":
                raise AssertionError(f"parse {expr}: expected OK, got {parse_proc.stdout!r}")

    def check_expression_randomized():
        exe = get_exe()
        rng = random.Random(20260704)
        checked = []
        for _ in range(80):
            expr, expected_value = gen_expr(rng)
            proc = invoke(exe, "--eval", expr, timeout=5)
            assert_ok(proc, f"random eval {expr!r}")
            got = proc.stdout.strip()
            expected = str(expected_value)
            if got != expected:
                raise AssertionError(f"random expr {expr!r}: got {got!r}, expected {expected!r}")
            checked.append(expr)
        return {"count": len(checked)}

    def check_comments_whitespace_edge():
        exe = get_exe()
        expr = "\r\n12\t+\t/* line1\nline2 */\n3 // ignored\n* 4"
        proc = invoke(exe, "--eval", expr)
        assert_ok(proc, "comments and whitespace")
        if proc.stdout.strip() != "24":
            raise AssertionError(f"expected 24, got {proc.stdout!r}")

        src = "int/*a*/x=1;\n// skip this\nreturn\t/*b*/x;"
        proc = invoke(exe, "--tokens", src)
        assert_ok(proc, "tokens around comments")
        got = [(t, x) for t, x, _, _ in parse_token_lines(proc.stdout)]
        expected = [
            ("KEYWORD", "int"),
            ("IDENT", "x"),
            ("OP", "="),
            ("INT", "1"),
            ("SEMI", ";"),
            ("KEYWORD", "return"),
            ("IDENT", "x"),
            ("SEMI", ";"),
        ]
        if got != expected:
            raise AssertionError(f"comment token mismatch: {got!r}")

    def check_malformed_errors():
        exe = get_exe()
        bad_cases = [
            ("", "--parse"),
            ("1+", "--eval"),
            ("*2", "--parse"),
            ("(1+2", "--parse"),
            ("1 2", "--eval"),
            ("1 + @", "--tokens"),
            ("/* never closed", "--parse"),
            ("10 / (3 - 3)", "--eval"),
            ("int main() { return ; }", "--parse"),
        ]
        for text, mode in bad_cases:
            proc = invoke(exe, mode, text, timeout=3)
            assert_fail(proc, f"{mode} should reject {text!r}")

    def check_c_like_parse_subset():
        exe = get_exe()
        ok_cases = [
            "int main() { return 1 + 2 * 3; }",
            "void f() { int x; int y = x + 12; { return y; } }",
            "int x = 12; return x;",
            "{ int a = 1; a + 2; }",
            "int main(){ int a=1; int b = a + 2; return b; }",
        ]
        for src in ok_cases:
            proc = invoke(exe, "--parse", src)
            assert_ok(proc, "parse C-like " + src)
            if proc.stdout.strip() != "OK":
                raise AssertionError(f"expected OK for {src!r}, got {proc.stdout!r}")

        bad_cases = [
            "int main( { return 1; }",
            "int = 1;",
            "return ;",
            "int main(){ int x = ; }",
            "int main(){ return (1+2; }",
            "int main(){ int x = 1 }",
        ]
        for src in bad_cases:
            proc = invoke(exe, "--parse", src, timeout=3)
            assert_fail(proc, "reject C-like " + src)

    checks = [
        ("build_and_visible", 0.05, check_build_and_visible),
        ("token_contract_basic", 0.07, check_token_contract_basic),
        ("token_positions", 0.11, check_token_positions),
        ("expression_fixed", 0.10, check_expression_fixed),
        ("expression_randomized", 0.22, check_expression_randomized),
        ("comments_whitespace_edge", 0.08, check_comments_whitespace_edge),
        ("malformed_errors", 0.17, check_malformed_errors),
        ("c_like_parse_subset", 0.20, check_c_like_parse_subset),
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
