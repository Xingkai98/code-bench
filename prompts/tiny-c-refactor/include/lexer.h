#ifndef TINY_C_LEXER_H
#define TINY_C_LEXER_H

#include <string>
#include <vector>

extern std::vector<std::string> g_token_types;
extern std::vector<std::string> g_token_texts;
extern std::string g_lexer_error;

class Lexer {
public:
    explicit Lexer(const std::string& input);
    bool lex();
    std::string formatted_tokens() const;

private:
    std::string input_;
};

#endif
