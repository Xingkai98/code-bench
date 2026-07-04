#include <cctype>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

struct Token {
    std::string type;
    std::string text;
};

static std::vector<Token> tokenize(const std::string& input, std::string& error) {
    std::vector<Token> tokens;
    for (size_t i = 0; i < input.size();) {
        char c = input[i];
        if (c == ' ' || c == '\n') {
            ++i;
            continue;
        }
        if (c == '/' && i + 1 < input.size() && input[i + 1] == '/') {
            break;
        }
        if (c == '/' && i + 1 < input.size() && input[i + 1] == '*') {
            i += 2;
            while (i + 1 < input.size() && !(input[i] == '*' && input[i + 1] == '/')) {
                ++i;
            }
            if (i + 1 >= input.size()) {
                return tokens;
            }
            i += 2;
            continue;
        }
        if (std::isdigit(static_cast<unsigned char>(c))) {
            tokens.push_back({"INT", std::string(1, c)});
            ++i;
            while (i < input.size() && std::isdigit(static_cast<unsigned char>(input[i]))) {
                ++i;
            }
            continue;
        }
        if (std::isalpha(static_cast<unsigned char>(c)) || c == '_') {
            size_t start = i++;
            while (i < input.size() &&
                   (std::isalnum(static_cast<unsigned char>(input[i])) || input[i] == '_')) {
                ++i;
            }
            std::string text = input.substr(start, i - start);
            std::string type = (text == "int" || text == "return") ? "KEYWORD" : "IDENT";
            tokens.push_back({type, text});
            continue;
        }
        if (std::string("+-*/=(){};,<>").find(c) != std::string::npos) {
            std::string text(1, c);
            std::string type = "OP";
            if (c == '(') type = "LPAREN";
            else if (c == ')') type = "RPAREN";
            else if (c == '{') type = "LBRACE";
            else if (c == '}') type = "RBRACE";
            else if (c == ';') type = "SEMI";
            else if (c == ',') type = "COMMA";
            tokens.push_back({type, text});
            ++i;
            continue;
        }
        error = "bad character";
        return {};
    }
    return tokens;
}

struct Parser {
    std::vector<Token> tokens;
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
        error = "expected integer";
        return false;
    }
    bool expr(int& value) {
        if (!factor(value)) return false;
        while (!at_end() && std::string("+-*/").find(tokens[pos].text) != std::string::npos) {
            std::string op = tokens[pos++].text;
            int rhs = 0;
            if (!factor(rhs)) return false;
            if (op == "+") value += rhs;
            else if (op == "-") value -= rhs;
            else if (op == "*") value *= rhs;
            else if (op == "/") value /= rhs;
        }
        return true;
    }
};

int main(int argc, char** argv) {
    if (argc != 2) {
        std::cerr << "ERROR: expected mode\n";
        return 2;
    }
    std::string mode = argv[1];
    std::string input((std::istreambuf_iterator<char>(std::cin)), std::istreambuf_iterator<char>());
    std::string error;
    auto tokens = tokenize(input, error);
    if (!error.empty()) {
        std::cerr << "ERROR: " << error << "\n";
        return 1;
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
        return 1;
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
