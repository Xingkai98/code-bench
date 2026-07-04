#include "lexer.h"
#include "parser.h"

#include <iostream>
#include <iterator>
#include <string>

int main(int argc, char** argv) {
    if (argc != 2) {
        std::cerr << "ERROR: expected --tokens, --parse, or --eval\n";
        return 2;
    }
    std::string mode = argv[1];
    std::string input((std::istreambuf_iterator<char>(std::cin)), std::istreambuf_iterator<char>());

    if (mode == "--tokens") {
        Lexer lexer(input);
        if (!lexer.lex()) {
            std::cerr << "ERROR: " << g_lexer_error << "\n";
            return 1;
        }
        std::cout << lexer.formatted_tokens();
        return 0;
    }

    Parser parser(input);
    if (mode == "--parse") {
        if (!parser.parse()) {
            std::cerr << "ERROR: " << parser.error() << "\n";
            return 1;
        }
        std::cout << "OK\n";
        return 0;
    }
    if (mode == "--eval") {
        int value = 0;
        if (!parser.eval(value)) {
            std::cerr << "ERROR: " << parser.error() << "\n";
            return 1;
        }
        std::cout << value << "\n";
        return 0;
    }

    std::cerr << "ERROR: unknown mode\n";
    return 2;
}
