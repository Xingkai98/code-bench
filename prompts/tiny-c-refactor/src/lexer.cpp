#include "lexer.h"

#include <cctype>
#include <set>
#include <sstream>

std::vector<std::string> g_token_types;
std::vector<std::string> g_token_texts;
std::string g_lexer_error;

Lexer::Lexer(const std::string& input) : input_(input) {}

bool Lexer::lex() {
    g_token_types.clear();
    g_token_texts.clear();
    g_lexer_error.clear();
    static const std::set<std::string> keywords = {
        "int", "return", "if", "else", "while", "for", "void",
    };

    for (size_t i = 0; i < input_.size();) {
        unsigned char ch = static_cast<unsigned char>(input_[i]);
        if (std::isspace(ch)) {
            ++i;
            continue;
        }
        if (input_[i] == '/' && i + 1 < input_.size() && input_[i + 1] == '/') {
            i += 2;
            while (i < input_.size() && input_[i] != '\n') ++i;
            continue;
        }
        if (input_[i] == '/' && i + 1 < input_.size() && input_[i + 1] == '*') {
            i += 2;
            bool closed = false;
            while (i + 1 < input_.size()) {
                if (input_[i] == '*' && input_[i + 1] == '/') {
                    i += 2;
                    closed = true;
                    break;
                }
                ++i;
            }
            if (!closed) {
                g_lexer_error = "unclosed block comment";
                return false;
            }
            continue;
        }
        if (std::isalpha(ch) || input_[i] == '_') {
            size_t start = i++;
            while (i < input_.size()) {
                unsigned char next = static_cast<unsigned char>(input_[i]);
                if (!std::isalnum(next) && input_[i] != '_') break;
                ++i;
            }
            std::string text = input_.substr(start, i - start);
            g_token_types.push_back(keywords.count(text) ? "KEYWORD" : "IDENT");
            g_token_texts.push_back(text);
            continue;
        }
        if (std::isdigit(ch)) {
            size_t start = i++;
            while (i < input_.size() && std::isdigit(static_cast<unsigned char>(input_[i]))) ++i;
            g_token_types.push_back("INT");
            g_token_texts.push_back(input_.substr(start, i - start));
            continue;
        }
        if (i + 1 < input_.size()) {
            std::string two = input_.substr(i, 2);
            if (two == "==" || two == "<=" || two == ">=" || two == "!=") {
                g_token_types.push_back("OP");
                g_token_texts.push_back(two);
                i += 2;
                continue;
            }
        }
        std::string one(1, input_[i]);
        if (one == "+" || one == "-" || one == "*" || one == "/" ||
            one == "=" || one == "<" || one == ">") {
            g_token_types.push_back("OP");
            g_token_texts.push_back(one);
        } else if (one == "(") {
            g_token_types.push_back("LPAREN");
            g_token_texts.push_back(one);
        } else if (one == ")") {
            g_token_types.push_back("RPAREN");
            g_token_texts.push_back(one);
        } else if (one == "{") {
            g_token_types.push_back("LBRACE");
            g_token_texts.push_back(one);
        } else if (one == "}") {
            g_token_types.push_back("RBRACE");
            g_token_texts.push_back(one);
        } else if (one == ";") {
            g_token_types.push_back("SEMI");
            g_token_texts.push_back(one);
        } else if (one == ",") {
            g_token_types.push_back("COMMA");
            g_token_texts.push_back(one);
        } else {
            g_lexer_error = "unexpected character: " + one;
            return false;
        }
        ++i;
    }
    return true;
}

std::string Lexer::formatted_tokens() const {
    std::ostringstream out;
    for (size_t i = 0; i < g_token_types.size(); ++i) {
        out << g_token_types[i] << ":" << g_token_texts[i] << "\n";
    }
    return out.str();
}
