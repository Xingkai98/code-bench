#!/usr/bin/env python3
"""Hidden eval for tiny-c-modernize."""
import json
import random
import re
import shutil
import subprocess
import sys
from pathlib import Path


WEIGHTS = {
    "build_and_visible": 0.05,
    "token_contract_basic": 0.05,
    "token_positions": 0.05,
    "expression_fixed": 0.05,
    "expression_randomized": 0.05,
    "comments_whitespace_edge": 0.05,
    "malformed_errors_easy": 0.07,
    "malformed_errors_medium": 0.12,
    "malformed_errors_hard": 0.16,
    "c_like_parse_easy": 0.07,
    "c_like_parse_medium": 0.12,
    "c_like_parse_hard": 0.16,
}

if round(sum(WEIGHTS.values()), 10) != 1.0:
    raise RuntimeError(f"tiny-c-modernize eval weights must sum to 1.0, got {sum(WEIGHTS.values())}")


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


def run_case_group(results, name, weight, cases, fn):
    passed_cases = 0
    failed_cases = []
    for index, case in enumerate(cases, start=1):
        label = case.get("label", f"case_{index}") if isinstance(case, dict) else f"case_{index}"
        try:
            fn(case)
            passed_cases += 1
        except Exception as exc:
            if len(failed_cases) < 12:
                failed_cases.append({
                    "case": label,
                    "error": f"{type(exc).__name__}: {exc}",
                })

    total_cases = len(cases)
    earned = weight * (passed_cases / total_cases if total_cases else 0.0)
    results[name] = {
        "passed": passed_cases == total_cases,
        "weight": weight,
        "earned": round(earned, 3),
        "passed_cases": passed_cases,
        "total_cases": total_cases,
    }
    if failed_cases:
        results[name]["failed_cases"] = failed_cases
    return earned


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
    workdir = workdir.resolve()
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


def assert_fail_at(proc, msg, expected_pos):
    assert_fail(proc, msg)
    if expected_pos not in proc.stderr:
        raise AssertionError(f"{msg}: stderr must contain {expected_pos}, got {proc.stderr!r}")


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
    workdir = Path(sys.argv[1]).resolve()
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

    def check_malformed_errors_easy():
        exe = get_exe()
        bad_cases = [
            {"mode": "--eval", "input": "", "label": "empty eval"},
            {"mode": "--tokens", "input": "1 + @", "label": "illegal char"},
            {"mode": "--tokens", "input": "int x = 1 $ 2;", "label": "illegal dollar"},
            {"mode": "--tokens", "input": "/* never closed", "label": "unclosed block comment tokens"},
            {"mode": "--parse", "input": "1+", "label": "trailing plus parse"},
            {"mode": "--eval", "input": "1+", "label": "trailing plus eval"},
            {"mode": "--parse", "input": "*2", "label": "leading star"},
            {"mode": "--eval", "input": "/2", "label": "leading slash"},
            {"mode": "--parse", "input": "(1+2", "label": "missing right paren"},
            {"mode": "--parse", "input": "1+2)", "label": "extra right paren"},
            {"mode": "--eval", "input": "1 2", "label": "adjacent ints"},
            {"mode": "--eval", "input": "10/0", "label": "division by zero direct"},
            {"mode": "--eval", "input": "x + 1", "label": "identifier in eval"},
            {"mode": "--parse", "input": "return ;", "label": "return without expr"},
            {"mode": "--parse", "input": "int = 1;", "label": "declaration missing ident"},
            {"mode": "--parse", "input": "int 123abc;", "label": "declaration numeric ident"},
            {"mode": "--parse", "input": "int x = ;", "label": "declaration missing initializer"},
            {"mode": "--parse", "input": "int x = 1", "label": "declaration missing semicolon"},
        ]

        def run_bad_case(case):
            proc = invoke(exe, case["mode"], case["input"], timeout=1.0)
            assert_fail(proc, f"{case['mode']} should reject {case['input']!r}")

        return run_case_group(results, "malformed_errors_easy", WEIGHTS["malformed_errors_easy"], bad_cases, run_bad_case)

    def check_malformed_errors_medium():
        exe = get_exe()
        bad_cases = [
            {"mode": "--parse", "input": "/* never closed", "label": "unclosed block comment parse"},
            {"mode": "--eval", "input": "10 / (3 - 3)", "label": "division by zero grouped"},
            {"mode": "--eval", "input": "1 / (2 - 2) + 3", "label": "division by zero nested"},
            {"mode": "--eval", "input": "int x = 1;", "label": "statement in eval"},
            {"mode": "--parse", "input": "int main() { return ; }", "label": "function return without expr"},
            {"mode": "--parse", "input": "int main()) { return 1; }", "label": "extra function right paren"},
            {"mode": "--parse", "input": "int main() {", "label": "unclosed function block"},
            {"mode": "--parse", "input": "int main() }", "label": "unexpected right brace"},
            {"mode": "--parse", "input": "{ int a = 1; ", "label": "unclosed nested block"},
            {"mode": "--parse", "input": "int main(){ int x = ; }", "label": "function bad initializer"},
            {"mode": "--parse", "input": "int main(){ int x = 1 }", "label": "function missing declaration semicolon"},
            {"mode": "--parse", "input": "int main(){ return (1+2; }", "label": "return missing expr paren"},
            {"mode": "--parse", "input": "int main(){ (1+2; }", "label": "expr stmt missing paren"},
            {"mode": "--parse", "input": "int main(){ 1 + ; }", "label": "expr stmt missing rhs"},
            {"mode": "--parse", "input": "int main(){ 1 + 2 }", "label": "expr stmt missing semicolon"},
            {"mode": "--parse", "input": "void f() { int ; }", "label": "void function bad declaration"},
            {"mode": "--parse", "input": "void f() { return; }", "label": "void function empty return"},
            {"mode": "--eval", "input": "--", "label": "double unary missing operand"},
        ]

        def run_bad_case(case):
            proc = invoke(exe, case["mode"], case["input"], timeout=1.0)
            assert_fail(proc, f"{case['mode']} should reject {case['input']!r}")

        return run_case_group(results, "malformed_errors_medium", WEIGHTS["malformed_errors_medium"], bad_cases, run_bad_case)

    def check_malformed_errors_hard():
        exe = get_exe()
        bad_cases = [
            {"mode": "--parse", "input": "int main( { return 1; }", "label": "missing function right paren no hang"},
            {"mode": "--parse", "input": "void f( { }", "label": "void function missing right paren no hang"},
            {"mode": "--parse", "input": "void f() { { return 1; }", "label": "nested block missing outer brace"},
            {"mode": "--parse", "input": "int main(){ int a = (1+2; return a; }", "label": "initializer missing paren"},
            {"mode": "--parse", "input": "int main(){ int a = 1,,2; }", "label": "double comma"},
            {"mode": "--parse", "input": "int main(){ return 1,,2; }", "label": "comma in return expr"},
            {"mode": "--parse", "input": "int main(){ return 1 @ 2; }", "label": "illegal char in function", "position": "1:22"},
            {"mode": "--parse", "input": "int main(){ /* open", "label": "unclosed comment in function"},
            {"mode": "--parse", "input": "int main(){ return 1; }}", "label": "extra brace after function"},
            {"mode": "--parse", "input": "int main(){ int a = 1; return a", "label": "missing return semicolon and brace"},
            {"mode": "--parse", "input": "int main(){ int a = 1; return a; } trailing", "label": "trailing identifier"},
            {"mode": "--parse", "input": "main int() { return 1; }", "label": "function wrong order"},
            {"mode": "--parse", "input": "int main() { return 1; } int", "label": "trailing type without decl"},
            {"mode": "--parse", "input": "int main(){\n  int a = 1;\n  return a + ;\n}", "label": "multiline missing rhs", "position": "3:"},
            {"mode": "--parse", "input": "int main(){\r\n\tint a = 1;\r\n\treturn a;\r\n} $", "label": "crlf trailing illegal char"},
            {"mode": "--tokens", "input": "int x;\n/* unterminated\ncomment", "label": "multiline unclosed comment"},
            {"mode": "--eval", "input": "1 + /* hidden */ (2 / (3 - 3))", "label": "division by zero behind comment"},
            {"mode": "--parse", "input": "int main(){ int a = 1; { int b = 2; return a + b; }", "label": "missing function brace after nested block"},
        ]

        def run_bad_case(case):
            proc = invoke(exe, case["mode"], case["input"], timeout=1.0)
            if "position" in case:
                assert_fail_at(proc, f"{case['mode']} should reject {case['input']!r}", case["position"])
            else:
                assert_fail(proc, f"{case['mode']} should reject {case['input']!r}")

        return run_case_group(results, "malformed_errors_hard", WEIGHTS["malformed_errors_hard"], bad_cases, run_bad_case)

    def check_c_like_parse_easy():
        exe = get_exe()
        ok_cases = [
            {"input": "42", "label": "bare int expr"},
            {"input": "(1)", "label": "bare grouped int expr"},
            {"input": "x", "label": "bare identifier only expr"},
            {"input": "+1", "label": "bare unary plus expr"},
            {"input": "-x", "label": "bare unary identifier expr"},
            {"input": "2+3*4", "label": "bare precedence expr"},
            {"input": "-(2+3)*4", "label": "bare unary grouped expr"},
            {"input": "x + 1", "label": "bare identifier expr"},
            {"input": "(x + 1) * (y - 2)", "label": "bare multi identifier expr"},
            {"input": "int x;", "label": "top decl no init"},
            {"input": "int x = 12;", "label": "top decl init"},
            {"input": "return 1 + 2 * 3;", "label": "top return"},
            {"input": "{ int a = 1; a + 2; }", "label": "block decl expr"},
            {"input": "int main() { return 1 + 2 * 3; }", "label": "int main return"},
            {"input": "int main(){ int a=1; int b = a + 2; return b; }", "label": "int main decls return"},
            {"input": "int main(){ 1 + 2; return 3; }", "label": "int main expr stmt"},
            {"input": "int main(){ return -1 + +2; }", "label": "return unary signs"},
            {"input": "int main(){ return 8/4/2; }", "label": "return left assoc div"},
            {"input": "void f() { }", "label": "empty void function"},
            {"input": "void f() { int x; }", "label": "void decl"},
        ]
        bad_cases = [
            {"input": "int = 1;", "label": "declaration missing ident"},
            {"input": "return ;", "label": "return without expr"},
            {"input": "int main(){ int x = ; }", "label": "bad initializer"},
            {"input": "int main(){ return (1+2; }", "label": "return missing paren"},
            {"input": "int main(){ int x = 1 }", "label": "missing decl semicolon"},
            {"input": "int main(){", "label": "unclosed function"},
            {"input": "int main()) { return 1; }", "label": "extra paren"},
            {"input": "int main(){ return 1; }}", "label": "extra brace"},
            {"input": "int 123abc;", "label": "numeric identifier"},
            {"input": "int main(){ int ; }", "label": "missing declaration name"},
        ]

        cases = [{"expected": "ok", **case} for case in ok_cases] + [
            {"expected": "fail", **case} for case in bad_cases
        ]

        def run_parse_case(case):
            proc = invoke(exe, "--parse", case["input"], timeout=1.0)
            if case["expected"] == "ok":
                assert_ok(proc, "parse C-like " + case["input"])
                if proc.stdout.strip() != "OK":
                    raise AssertionError(f"expected OK for {case['input']!r}, got {proc.stdout!r}")
            else:
                assert_fail(proc, "reject C-like " + case["input"])

        return run_case_group(results, "c_like_parse_easy", WEIGHTS["c_like_parse_easy"], cases, run_parse_case)

    def check_c_like_parse_medium():
        exe = get_exe()
        ok_cases = [
            {"input": "x*y+z", "label": "bare identifier precedence expr"},
            {"input": "(a+b)/(c-d)", "label": "bare grouped identifier division expr"},
            {"input": "1\n+\n2", "label": "bare multiline expr"},
            {"input": "/*c*/ 1 + 2 //x", "label": "bare expr with comments"},
            {"input": "((x))", "label": "bare nested grouped identifier"},
            {"input": "a_1 + b_2 * 3", "label": "bare underscore identifiers expr"},
            {"input": "-(x + 1) * +2", "label": "bare mixed unary expr"},
            {"input": "8/4/2", "label": "bare left associative division"},
            {"input": "int alpha_1 = 12; return alpha_1;", "label": "top decl return"},
            {"input": "{ { { return 1; } } }", "label": "triple nested block"},
            {"input": "int main(){ { int a = 1; return a; } }", "label": "int main nested block"},
            {"input": "int main(){ int a = (1 + 2) * 3; return a; }", "label": "initializer grouped expr"},
            {"input": "int main(){ int a; int b; int c = a + b * 2; return c; }", "label": "multiple decls"},
            {"input": "void f() { int x; int y = x + 12; { return y; } }", "label": "void nested return"},
            {"input": "void f(){ { int x = 1; } { int y = 2; } }", "label": "sibling blocks"},
            {"input": "int f() { return (1); } void g() { int x; }", "label": "two functions"},
            {"input": "int x = 1; int y = x + 2; return y;", "label": "top multiple statements"},
            {"input": "{ return (1 + 2) * (3 + 4); }", "label": "block complex return"},
            {"input": "int main(){ int _x1 = -1 + +2 * (3 + 4); _x1 + 5; return _x1; }", "label": "identifier unary initializer and expr stmt"},
            {"input": "void f(){ { } { int x = 1; x + 2; } }", "label": "empty and nonempty sibling blocks"},
        ]
        bad_cases = [
            {"input": "int main( { return 1; }", "label": "missing function right paren"},
            {"input": "int main(){ return 1 }", "label": "missing return semicolon"},
            {"input": "{ int a = 1; ", "label": "unclosed block"},
            {"input": "void f( { }", "label": "void missing right paren"},
            {"input": "void f() { return; }", "label": "empty return"},
            {"input": "int main(){ 1 + ; }", "label": "expr stmt missing rhs"},
            {"input": "int main(){ (1+2; }", "label": "expr stmt missing paren"},
            {"input": "int main(){ int a = (1+2; return a; }", "label": "initializer missing paren"},
            {"input": "int main(){ int a = 1,,2; }", "label": "double comma initializer"},
            {"input": "main int() { return 1; }", "label": "wrong function order"},
            {"input": "int main() { return 1; } int", "label": "trailing incomplete decl"},
            {"input": "int main(){ return 1; } trailing", "label": "trailing identifier"},
            {"input": "int main(){ /* open", "label": "unclosed comment"},
        ]
        cases = [{"expected": "ok", **case} for case in ok_cases] + [
            {"expected": "fail", **case} for case in bad_cases
        ]

        def run_parse_case(case):
            proc = invoke(exe, "--parse", case["input"], timeout=1.0)
            if case["expected"] == "ok":
                assert_ok(proc, "parse C-like " + case["input"])
                if proc.stdout.strip() != "OK":
                    raise AssertionError(f"expected OK for {case['input']!r}, got {proc.stdout!r}")
            else:
                assert_fail(proc, "reject C-like " + case["input"])

        return run_case_group(results, "c_like_parse_medium", WEIGHTS["c_like_parse_medium"], cases, run_parse_case)

    def check_c_like_parse_hard():
        exe = get_exe()
        ok_cases = [
            {"input": "(((1 + 2) * (3 + 4)) - 5) / 2", "label": "bare deep arithmetic expr"},
            {"input": "\n\talpha_1 +\n\tbeta_2 * (gamma_3 - 4)", "label": "bare multiline identifier expr"},
            {"input": "/* before */ -(a + b) /* mid */ * (c + +2)", "label": "bare commented unary expr"},
            {"input": "a + b + c + d + e", "label": "bare left associative addition chain"},
            {"input": "a * b / c * d / e", "label": "bare multiplicative chain"},
            {"input": "-(-(-1))", "label": "bare repeated unary int"},
            {"input": "-(+identifier_123)", "label": "bare repeated unary identifier"},
            {"input": "(a + (b * (c + (d))))", "label": "bare nested identifier groups"},
            {"input": "1\r\n+\r\n2\r\n*\r\n3", "label": "bare crlf expression"},
            {"input": "/*line1\nline2*/ x + 1", "label": "bare multiline comment expr"},
            {"input": "((a_0)) + ((b_1))", "label": "bare redundant parens identifiers"},
            {"input": "1 + 2 * 3 - 4 / 2", "label": "bare mixed precedence expr"},
            {"input": "x /* comment */", "label": "bare identifier trailing comment"},
            {"input": "(x + y)\n*\n(z - w)", "label": "bare multiline grouped identifiers"},
            {"input": "0", "label": "bare zero expr"},
            {"input": "+(+1)", "label": "bare nested unary plus"},
            {"input": "a + /* left */ b * /* right */ c", "label": "bare expression with infix comments"},
            {"input": "(((alpha))) + -((beta))", "label": "bare redundant groups with unary identifier"},
            {"input": "1 + 2 + 3 + 4 + 5 + 6", "label": "bare long additive chain"},
            {"input": "1 * 2 * 3 / 4 / 5", "label": "bare long multiplicative chain"},
            {"input": "((a + b) * (c + d)) / (e - f)", "label": "bare nested identifier arithmetic"},
            {"input": "\t\t-1 +\t+2", "label": "bare tabbed unary expression"},
            {"input": "/* only leading comment */\n((1))", "label": "bare expression after leading comment line"},
            {"input": "x + y // trailing comment", "label": "bare expression with line comment"},
        ]
        cases = [{"expected": "ok", **case} for case in ok_cases]

        def run_parse_case(case):
            proc = invoke(exe, "--parse", case["input"], timeout=1.0)
            if case["expected"] == "ok":
                assert_ok(proc, "parse C-like " + case["input"])
                if proc.stdout.strip() != "OK":
                    raise AssertionError(f"expected OK for {case['input']!r}, got {proc.stdout!r}")
            else:
                assert_fail(proc, "reject C-like " + case["input"])

        return run_case_group(results, "c_like_parse_hard", WEIGHTS["c_like_parse_hard"], cases, run_parse_case)

    checks = [
        ("build_and_visible", check_build_and_visible),
        ("token_contract_basic", check_token_contract_basic),
        ("token_positions", check_token_positions),
        ("expression_fixed", check_expression_fixed),
        ("expression_randomized", check_expression_randomized),
        ("comments_whitespace_edge", check_comments_whitespace_edge),
    ]

    for name, fn in checks:
        score += run_check(results, name, WEIGHTS[name], fn)

    for name, fn in [
        ("malformed_errors_easy", check_malformed_errors_easy),
        ("malformed_errors_medium", check_malformed_errors_medium),
        ("malformed_errors_hard", check_malformed_errors_hard),
        ("c_like_parse_easy", check_c_like_parse_easy),
        ("c_like_parse_medium", check_c_like_parse_medium),
        ("c_like_parse_hard", check_c_like_parse_hard),
    ]:
        try:
            score += fn()
        except Exception as exc:
            results[name] = {
                "passed": False,
                "weight": WEIGHTS[name],
                "earned": 0.0,
                "error": f"{type(exc).__name__}: {exc}",
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
