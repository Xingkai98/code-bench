#ifndef TINY_C_PARSER_H
#define TINY_C_PARSER_H

#include <string>

class Parser {
public:
    explicit Parser(const std::string& input);
    bool parse();
    bool eval(int& value);
    std::string error() const;

private:
    std::string input_;
    std::string error_;
};

#endif
