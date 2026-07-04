#!/usr/bin/env python3
"""Hidden eval for tiny-c-test-suite.

The submitted tests are run against a hidden fixed implementation and several
hidden mutants. A mutant is killed when the submitted pytest suite fails on it.
"""
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


PROTECTED_HASHES = {
    "CMakeLists.txt": "132c3ad3b5802490d47d9c2f6a3d2b45e0c751045bc50d2c403921ea4a1096e9",
    "main.cpp": "ead1bd739887a6620f7708132cc16d8d1c76c8d69558735a6f18db0cc85794b7",
}


CMAKE_TEXT = """\
cmake_minimum_required(VERSION 3.10)
project(cparser VERSION 0.1.0)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_executable(cparser main.cpp)
"""


MAIN_CPP = r'''
#include <cctype>
#include <iostream>
#include <set>
#include <string>
#include <vector>

struct Token {
    std::string type;
    std::string text;
};

static bool is_space(char c) {
#ifdef MUTANT_NO_TAB_WHITESPACE
    return c == ' ' || c == '\n';
#else
    return std::isspace(static_cast<unsigned char>(c));
#endif
}

static bool is_keyword(const std::string& text) {
    static const std::set<std::string> keywords = {
        "int", "return", "if", "else", "while", "for", "void",
    };
    return keywords.count(text) != 0;
}

static bool tokenize(const std::string& input, std::vector<Token>& tokens, std::string& error) {
    for (size_t i = 0; i < input.size();) {
        char c = input[i];
        if (is_space(c)) {
            ++i;
            continue;
        }
        if (c == '/' && i + 1 < input.size() && input[i + 1] == '/') {
#ifdef MUTANT_NO_LINE_COMMENTS
            tokens.push_back({"OP", "/"});
            ++i;
#else
            i += 2;
            while (i < input.size() && input[i] != '\n') ++i;
#endif
            continue;
        }
        if (c == '/' && i + 1 < input.size() && input[i + 1] == '*') {
            i += 2;
            bool closed = false;
            while (i + 1 < input.size()) {
                if (input[i] == '*' && input[i + 1] == '/') {
                    i += 2;
                    closed = true;
                    break;
                }
                ++i;
            }
            if (!closed) {
#ifdef MUTANT_UNCLOSED_COMMENT_OK
                return true;
#else
                error = "unclosed block comment";
                return false;
#endif
            }
            continue;
        }
        if (std::isdigit(static_cast<unsigned char>(c))) {
            size_t start = i++;
            while (i < input.size() && std::isdigit(static_cast<unsigned char>(input[i]))) ++i;
#ifdef MUTANT_MULTIDIGIT_FIRST
            tokens.push_back({"INT", input.substr(start, 1)});
#else
            tokens.push_back({"INT", input.substr(start, i - start)});
#endif
            continue;
        }
        if (std::isalpha(static_cast<unsigned char>(c)) || c == '_') {
            size_t start = i++;
            while (i < input.size() &&
                   (std::isalnum(static_cast<unsigned char>(input[i])) || input[i] == '_')) {
                ++i;
            }
            std::string text = input.substr(start, i - start);
            tokens.push_back({is_keyword(text) ? "KEYWORD" : "IDENT", text});
            continue;
        }
#ifndef MUTANT_SINGLE_CHAR_OPERATORS
        if (i + 1 < input.size()) {
            std::string two = input.substr(i, 2);
            if (two == "==" || two == "<=" || two == ">=" || two == "!=") {
                tokens.push_back({"OP", two});
                i += 2;
                continue;
            }
        }
#endif
        std::string one(1, c);
        if (one == "+" || one == "-" || one == "*" || one == "/" ||
            one == "=" || one == "<" || one == ">") {
            tokens.push_back({"OP", one});
        } else if (one == "(") {
            tokens.push_back({"LPAREN", one});
        } else if (one == ")") {
            tokens.push_back({"RPAREN", one});
        } else if (one == "{") {
            tokens.push_back({"LBRACE", one});
        } else if (one == "}") {
            tokens.push_back({"RBRACE", one});
        } else if (one == ";") {
            tokens.push_back({"SEMI", one});
        } else if (one == ",") {
            tokens.push_back({"COMMA", one});
        } else {
            error = "unexpected character";
            return false;
        }
        ++i;
    }
    return true;
}

struct Parser {
    const std::vector<Token>& tokens;
    size_t pos = 0;
    std::string error;

    bool at_end() const { return pos >= tokens.size(); }
    bool match(const std::string& text) {
        if (!at_end() && tokens[pos].text == text) {
            ++pos;
            return true;
        }
        return false;
    }

    bool factor(int& value) {
        if (at_end()) {
            error = "expected expression";
            return false;
        }
        if (match("(")) {
            if (!expr(value)) return false;
            if (!match(")")) {
                error = "expected ')'";
                return false;
            }
            return true;
        }
        if (tokens[pos].type == "INT") {
            value = std::stoi(tokens[pos++].text);
            return true;
        }
        error = "expected integer or '('";
        return false;
    }

    bool term(int& value) {
        if (!factor(value)) return false;
        while (!at_end() && (tokens[pos].text == "*" || tokens[pos].text == "/")) {
            std::string op = tokens[pos++].text;
            int rhs = 0;
            if (!factor(rhs)) return false;
            if (op == "*") value *= rhs;
            else {
                if (rhs == 0) {
                    error = "division by zero";
                    return false;
                }
                value /= rhs;
            }
        }
        return true;
    }

    bool expr(int& value) {
#ifdef MUTANT_FLAT_PRECEDENCE
        if (!factor(value)) return false;
        while (!at_end() && (tokens[pos].text == "+" || tokens[pos].text == "-" ||
                             tokens[pos].text == "*" || tokens[pos].text == "/")) {
            std::string op = tokens[pos++].text;
            int rhs = 0;
            if (!factor(rhs)) return false;
            if (op == "+") value += rhs;
            else if (op == "-") value -= rhs;
            else if (op == "*") value *= rhs;
            else {
                if (rhs == 0) {
                    error = "division by zero";
                    return false;
                }
                value /= rhs;
            }
        }
        return true;
#else
        if (!term(value)) return false;
        while (!at_end() && (tokens[pos].text == "+" || tokens[pos].text == "-")) {
            std::string op = tokens[pos++].text;
            int rhs = 0;
            if (!term(rhs)) return false;
            if (op == "+") value += rhs;
            else value -= rhs;
        }
        return true;
#endif
    }
};

int main(int argc, char** argv) {
    if (argc != 2) {
        std::cerr << "ERROR: expected mode\n";
        return 2;
    }
    std::string mode = argv[1];
    std::string input((std::istreambuf_iterator<char>(std::cin)), std::istreambuf_iterator<char>());
    std::vector<Token> tokens;
    std::string error;
    if (!tokenize(input, tokens, error)) {
        std::cerr << "ERROR: " << error << "\n";
#ifdef MUTANT_BAD_ERROR_EXIT
        return 0;
#else
        return 1;
#endif
    }

    if (mode == "--tokens") {
        for (const auto& token : tokens) {
            std::cout << token.type << ":" << token.text << "\n";
        }
        return 0;
    }

    Parser parser{tokens};
    int value = 0;
    bool ok = !tokens.empty() && parser.expr(value) && parser.at_end();
    if (!ok) {
        std::cerr << "ERROR: " << (parser.error.empty() ? "malformed input" : parser.error) << "\n";
#ifdef MUTANT_BAD_ERROR_EXIT
        return 0;
#else
        return 1;
#endif
    }
    if (mode == "--parse") {
        std::cout << "OK\n";
        return 0;
    }
    if (mode == "--eval") {
        std::cout << value << "\n";
        return 0;
    }
    std::cerr << "ERROR: unknown mode\n";
    return 2;
}
'''


MUTANTS = {
    "no_tab_whitespace": "MUTANT_NO_TAB_WHITESPACE",
    "multidigit_first": "MUTANT_MULTIDIGIT_FIRST",
    "flat_precedence": "MUTANT_FLAT_PRECEDENCE",
    "unclosed_comment_ok": "MUTANT_UNCLOSED_COMMENT_OK",
    "bad_error_exit": "MUTANT_BAD_ERROR_EXIT",
    "no_line_comments": "MUTANT_NO_LINE_COMMENTS",
    "single_char_operators": "MUTANT_SINGLE_CHAR_OPERATORS",
}


def run_check(results, name, weight, fn):
    try:
        value = fn()
        results[name] = {"passed": True, "weight": weight}
        if isinstance(value, dict):
            results[name].update(value)
        return weight
    except Exception as exc:
        results[name] = {
            "passed": False,
            "weight": weight,
            "error": f"{type(exc).__name__}: {exc}",
        }
        return 0.0


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def copy_tests(workdir, target):
    tests_dir = workdir / "tests"
    if not tests_dir.exists():
        raise AssertionError("tests/ directory missing")
    files = list(tests_dir.rglob("test_*.py"))
    if not files:
        raise AssertionError("no tests/test_*.py files found")
    shutil.copytree(tests_dir, target / "tests")
    conftest = workdir / "conftest.py"
    if conftest.exists():
        shutil.copy2(conftest, target / "conftest.py")


def make_impl(target, macro=None):
    target.mkdir(parents=True, exist_ok=True)
    cmake = CMAKE_TEXT
    if macro:
        cmake += f"\nadd_compile_definitions({macro})\n"
    (target / "CMakeLists.txt").write_text(cmake)
    (target / "main.cpp").write_text(MAIN_CPP)


def run_pytest(project_dir):
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tests"],
        cwd=project_dir,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
    )


def tests_text(workdir):
    tests_dir = workdir / "tests"
    if not tests_dir.exists():
        return ""
    return "\n".join(path.read_text(errors="replace") for path in tests_dir.rglob("*.py"))


def main():
    workdir = Path(sys.argv[1])
    results = {}
    score = 0.0

    def check_tests_discoverable():
        proc = subprocess.run(
            [sys.executable, "test_basic.py"],
            cwd=workdir,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
        )
        if proc.returncode != 0:
            raise AssertionError((proc.stdout + proc.stderr)[-2000:])

    def check_source_untouched():
        for rel, expected in PROTECTED_HASHES.items():
            path = workdir / rel
            if not path.exists():
                raise AssertionError(f"{rel} missing")
            actual = sha256(path)
            if actual != expected:
                raise AssertionError(f"{rel} was modified")

    def check_quality_gate():
        text = tests_text(workdir)
        if not text.strip():
            raise AssertionError("no test content found")
        banned = ["time.sleep", "requests.", "urllib.", "http://", "https://", "/home/"]
        hits = [item for item in banned if item in text]
        if hits:
            raise AssertionError("banned test patterns: " + ", ".join(hits))

    fixed_pass_holder = {"passed": False}

    def check_fixed_pass():
        with tempfile.TemporaryDirectory(prefix="tiny-c-fixed-") as tmp:
            project = Path(tmp)
            make_impl(project)
            copy_tests(workdir, project)
            proc = run_pytest(project)
            if proc.returncode != 0:
                raise AssertionError((proc.stdout + proc.stderr)[-3000:])
            fixed_pass_holder["passed"] = True

    def check_mutation_kill_rate():
        killed = {}
        if not fixed_pass_holder["passed"]:
            raise AssertionError("fixed implementation did not pass; mutation score unavailable")
        with tempfile.TemporaryDirectory(prefix="tiny-c-mutants-") as tmp:
            root = Path(tmp)
            for name, macro in MUTANTS.items():
                project = root / name
                make_impl(project, macro)
                copy_tests(workdir, project)
                proc = run_pytest(project)
                killed[name] = proc.returncode != 0
        killed_count = sum(1 for value in killed.values() if value)
        if killed_count == 0:
            raise AssertionError("no mutants killed")
        return {"killed": killed, "killed_count": killed_count, "total": len(MUTANTS)}

    score += run_check(results, "tests_discoverable", 0.10, check_tests_discoverable)
    score += run_check(results, "source_untouched", 0.10, check_source_untouched)
    score += run_check(results, "quality_gate", 0.10, check_quality_gate)
    score += run_check(results, "fixed_pass", 0.25, check_fixed_pass)

    try:
        detail = check_mutation_kill_rate()
        killed_count = detail["killed_count"]
        mutation_score = 0.45 * (killed_count / len(MUTANTS))
        results["mutation_kill_rate"] = {
            "passed": killed_count > 0,
            "weight": 0.45,
            "earned": round(mutation_score, 3),
            **detail,
        }
        score += mutation_score
    except Exception as exc:
        results["mutation_kill_rate"] = {
            "passed": False,
            "weight": 0.45,
            "earned": 0.0,
            "error": f"{type(exc).__name__}: {exc}",
        }

    passed = [name for name, item in results.items() if item["passed"]]
    failed = [name for name, item in results.items() if not item["passed"]]
    summary = f"passed {len(passed)}/{len(results)} checks"
    if "mutation_kill_rate" in results and "killed_count" in results["mutation_kill_rate"]:
        summary += f"; killed {results['mutation_kill_rate']['killed_count']}/{len(MUTANTS)} mutants"
    if failed:
        summary += f"; failed: {', '.join(failed)}"

    print(json.dumps({
        "score": round(score, 3),
        "details": results,
        "summary": summary,
    }))


if __name__ == "__main__":
    main()
