# tiny-c-modernize Scoring Notes

这份文档解释 `prompts/tiny-c-modernize/eval.py` 当前每个评分项具体测什么，以及它和 `prompt.txt` 里的需求如何对应。

当前版本只调整 hidden eval 和评分权重，没有修改 prompt。已有 rollout 可以直接用新版 eval 重新打分。

## 分数分布

```text
build_and_visible             0.05
token_contract_basic          0.05
token_positions               0.05
expression_fixed              0.05
expression_randomized         0.05
comments_whitespace_edge      0.05

malformed_errors_easy         0.07
malformed_errors_medium       0.12
malformed_errors_hard         0.16

c_like_parse_easy             0.07
c_like_parse_medium           0.12
c_like_parse_hard             0.16
```

基础 sanity checks 合计 `0.30`。核心 hidden checks 合计 `0.70`。没有 cap 逻辑，最终分数是各单项直接相加。所有 case-group 项都按 case 通过比例计分。

## 基础项

### build_and_visible，0.05

检查 `cmake -S . -B .eval_build`、`cmake --build .eval_build`、是否生成 `cparser`，以及 `python3 test_basic.py` 是否通过。

对应 prompt：CMake 项目必须能构建，产物必须包含 `cparser`，visible smoke check 应通过。

### token_contract_basic，0.05

用 `int main() { return value!=0, x<=12; }` 检查 `--tokens` 的 token type 和 lexeme 序列，覆盖 keyword、identifier、括号、花括号、多字符运算符、整数、逗号和分号。

对应 prompt：`--tokens` 输出 token，支持指定 token 类型、关键字和多字符运算符。

### token_positions，0.05

用带 newline 和 tab 的输入检查 `TYPE:lexeme:line:column` 是否精确，line/column 从 1 开始，tab 按 1 个 column 计。

对应 prompt：token 必须包含 line/column，且位置规则明确。

### expression_fixed，0.05

固定检查 8 个 `--eval` 表达式，覆盖优先级、括号、左结合、一元 `+/-` 和整数除法。

对应 prompt：eval 模式必须支持整数算术表达式。

### expression_randomized，0.05

用固定随机种子生成 80 个表达式，并用 oracle 对比结果。覆盖多层括号、一元符号、四则运算、随机空白、tab/newline 和 C++ 风格整数除法向 0 截断。

对应 prompt：表达式语法、优先级、左结合、空白处理和整数除法。

### comments_whitespace_edge，0.05

检查 eval 和 tokens 两条路径上的 `\r`、tab、`//` 行注释、`/* ... */` 跨行块注释。

对应 prompt：空白和两类注释必须被正确忽略，未闭合块注释必须报错。

## malformed_errors_easy，0.07

基础错误处理。case 包括：

- 空 eval 输入。
- 非法字符。
- 未闭合块注释。
- 表达式缺操作数。
- 括号不匹配。
- 相邻整数。
- 直接除以 0。
- eval 模式遇到 identifier。
- return 缺表达式。
- 声明缺 identifier、initializer 或分号。

每个 case 要求非 0 退出、stderr 以 `ERROR:` 开头、包含 `line:column`，并且不能卡死。

对应 prompt：格式错误、非法字符、括号不匹配、缺少分号、未闭合注释、除以 0 都必须失败。

## malformed_errors_medium，0.12

中等复杂错误处理。case 包括：

- parse/eval 中跨模式错误。
- 分组或嵌套表达式除以 0。
- 函数体中的坏 return、坏 initializer、缺分号。
- 函数/嵌套 block 未闭合。
- 多余函数右括号。
- void 函数体中的坏声明或空 return。
- 双一元符号缺操作数。

对应 prompt：parser 要消费 token stream，不能只处理 happy path；错误必须稳定返回，不应 hang。

## malformed_errors_hard，0.16

高难错误处理。case 包括：

- `int main( { ... }`、`void f( { }` 这类容易让递归下降 parser 卡死的函数头错误。
- 嵌套 block 缺外层右花括号。
- initializer/return 中深层括号错误。
- 逗号误用。
- 函数体非法字符。
- 函数后尾随垃圾 token。
- 多行输入中的错误位置。
- CRLF 后的非法字符。
- 多行未闭合块注释。
- 注释后隐藏的除以 0。

少数 case 会检查具体 `line:column` 片段，用来区分“只随便报一个位置”和“真正维护 token 位置”的实现。

对应 prompt：失败信息必须包含出错位置，不能卡死。

## c_like_parse_easy，0.07

基础 parse-only 能力。valid case 包括：

- 单个裸表达式，例如 `42`、`x`、`2+3*4`、`(x + 1) * (y - 2)`。
- 顶层声明、顶层 return。
- 简单 block。
- 简单 `int main()` / `void f()` 函数。

invalid case 包括声明缺名字、return 缺表达式、initializer 缺表达式、函数缺右括号/右花括号等。

对应 prompt：`--parse` 除了能解析 C-like 结构，还必须能解析单个表达式。

## c_like_parse_medium，0.12

中等 parse-only 能力。valid case 包括：

- 多行裸表达式。
- 带注释的裸表达式。
- identifier 表达式。
- 嵌套 block。
- 多声明。
- 多函数。
- void 函数内声明和嵌套 return。
- 表达式语句和复杂 initializer。

invalid case 包括函数头错误、void 函数头错误、缺 return 分号、未闭合 block、空 return、表达式语句缺右操作数、尾随垃圾和未闭合注释。

对应 prompt：tiny C 子集应能处理声明、return、表达式语句、block、简单函数定义，以及注释和空白。

## c_like_parse_hard，0.16

高难 parse-only 能力，专门聚焦复杂“单个裸表达式”输入。case 包括：

- 深层括号算术表达式。
- 多行 identifier 表达式。
- 表达式中穿插块注释和行注释。
- 长左结合链。
- 重复一元符号。
- CRLF 表达式。
- 多行块注释后的表达式。
- 冗余括号 identifier。

这个项故意不被大量 statement case 稀释，因为现有强模型样本常见问题是：statement parser 写得不错，但忘了 prompt 明确要求的“`--parse` 也要接受单个表达式”。

对应 prompt：`--parse` 除了能解析 C-like 结构，还必须能解析单个表达式；表达式支持 identifier、括号、一元 `+/-`、二元 `+ - * /`、优先级、左结合和空白/注释。
