#include "parser.h"

#include "lexer.h"

#include <cstdlib>

namespace {
size_t pos;
std::string parse_error;

bool at_end() {
    return pos >= g_token_texts.size();
}

bool match(const std::string& text) {
    if (!at_end() && g_token_texts[pos] == text) {
        ++pos;
        return true;
    }
    return false;
}

bool parse_expr(int& value);

bool parse_factor(int& value) {
    if (at_end()) {
        parse_error = "expected expression";
        return false;
    }
    if (match("(")) {
        if (!parse_expr(value)) return false;
        if (!match(")")) {
            parse_error = "expected ')'";
            return false;
        }
        return true;
    }
    if (g_token_types[pos] == "INT") {
        value = std::stoi(g_token_texts[pos++]);
        return true;
    }
    parse_error = "expected integer or '('";
    return false;
}

bool parse_term(int& value) {
    if (!parse_factor(value)) return false;
    while (!at_end() && (g_token_texts[pos] == "*" || g_token_texts[pos] == "/")) {
        std::string op = g_token_texts[pos++];
        int rhs = 0;
        if (!parse_factor(rhs)) return false;
        if (op == "*") {
            value *= rhs;
        } else {
            if (rhs == 0) {
                parse_error = "division by zero";
                return false;
            }
            value /= rhs;
        }
    }
    return true;
}

bool parse_expr(int& value) {
    if (!parse_term(value)) return false;
    while (!at_end() && (g_token_texts[pos] == "+" || g_token_texts[pos] == "-")) {
        std::string op = g_token_texts[pos++];
        int rhs = 0;
        if (!parse_term(rhs)) return false;
        if (op == "+") value += rhs;
        else value -= rhs;
    }
    return true;
}
}

Parser::Parser(const std::string& input) : input_(input) {}

bool Parser::eval(int& value) {
    Lexer lexer(input_);
    if (!lexer.lex()) {
        error_ = g_lexer_error;
        return false;
    }
    pos = 0;
    parse_error.clear();
    if (g_token_texts.empty()) {
        error_ = "empty input";
        return false;
    }
    if (!parse_expr(value)) {
        error_ = parse_error;
        return false;
    }
    if (!at_end()) {
        error_ = "unexpected token: " + g_token_texts[pos];
        return false;
    }
    return true;
}

bool Parser::parse() {
    int ignored = 0;
    return eval(ignored);
}

std::string Parser::error() const {
    return error_;
}
