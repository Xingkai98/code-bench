# tiny-c-modernize Scoring Notes

这份文档解释 `prompts/tiny-c-modernize/eval.py` 当前每个评分项具体测什么，以及它和 `prompt.txt` 里的需求如何对应。

当前版本为了避免重新 rollout，只调整评分权重，并去掉原先较弱的 `architecture_api_state` 正则结构检查；也取消 cap 逻辑。也就是说，已有模型输出可以直接用新版 eval 重新打分。

## 当前评分项

```text
build_and_visible          0.05
token_contract_basic       0.07
token_positions            0.11
expression_fixed           0.10
expression_randomized      0.22
comments_whitespace_edge   0.08
malformed_errors           0.17
c_like_parse_subset        0.20
```

当前没有 cap 逻辑。最终分数是各单项分数直接相加。

## 1. build_and_visible，0.05

检查内容：

- `cmake -S . -B .eval_build`
- `cmake --build .eval_build`
- 是否生成 `cparser`
- `python3 test_basic.py`

对应 prompt：

- CMake 项目必须能构建。
- 构建结果必须包含 `cparser`。
- visible smoke check 应通过。

这是基础门槛，所以权重较低。

## 2. token_contract_basic，0.07

检查输入：

```c
int main() { return value!=0, x<=12; }
```

要求 `--tokens` 的 token type 和 lexeme 序列正确，覆盖：

- keyword
- identifier
- parentheses
- braces
- multi-char operators `!=` / `<=`
- integer
- comma
- semicolon

对应 prompt：

- `--tokens` 每行输出 token。
- 支持指定 token 类型。
- 支持关键字和多字符运算符。

这是基础 lexer 契约，权重低于位置、随机表达式和 C-like parse。

## 3. token_positions，0.11

检查输入：

```c
int main() {
	return 12 + 3;
}
```

第二行开头是 tab。要求输出精确 `TYPE:lexeme:line:column`，例如：

```text
KEYWORD:int:1:1
IDENT:main:1:5
KEYWORD:return:2:2
INT:12:2:9
RBRACE:}:3:1
```

对应 prompt：

- `--tokens` 输出格式是 `TYPE:lexeme:line:column`。
- line/column 从 1 开始。
- tab 按 1 个 column 计。

这个项容易抓 off-by-one、tab、新行和 token 起始位置错误。

## 4. expression_fixed，0.10

固定表达式检查：

```text
2+3*4                  -> 14
(2+3)*4                -> 20
8-3-2                  -> 3
8/4/2                  -> 1
-(2+3)*4               -> -20
+7 + -3 * 2            -> 1
100 - 4 * (6 + 2) / 4  -> 92
18/(2+1)+5*2           -> 16
```

每个表达式要求：

- `--eval` 输出正确整数。
- `--parse` 输出 `OK`。

对应 prompt：

- 支持整数 literal、括号、一元 `+/-`、二元 `+ - * /`。
- 正常优先级和左结合。
- 整数除法。

这是表达式主线的固定样例，权重低于随机表达式。

## 5. expression_randomized，0.22

用固定随机种子生成 80 个表达式，并和 Python oracle 对比。

覆盖：

- 多层括号
- 一元正负
- `+ - * /`
- 随机空格、tab、换行
- C++ 风格整数除法向 0 截断

对应 prompt：

- 表达式语法完整性。
- 空白处理。
- 优先级和左结合。
- 整数除法。

这是当前最重要的区分项之一。它用于防止模型只覆盖 visible/fixed case。

## 6. comments_whitespace_edge，0.08

第一部分检查 eval：

```c
\r
12	+	/* line1
line2 */
3 // ignored
* 4
```

结果必须是 `24`。

第二部分检查 tokens：

```c
int/*a*/x=1;
// skip this
return	/*b*/x;
```

期望 token 序列忽略注释，并正确保留 `int`、`x`、`=`、`1`、`;`、`return`、`x`、`;`。

对应 prompt：

- 空白包括空格、tab、`\r`、`\n`。
- `//` 行注释和 `/* ... */` 块注释必须被忽略。
- 块注释可能跨多行。

这个项和 token/position 有重叠，所以权重中等偏低。

## 7. malformed_errors，0.17

要求以下输入失败：

```text
""                         --parse
"1+"                       --eval
"*2"                       --parse
"(1+2"                     --parse
"1 2"                      --eval
"1 + @"                    --tokens
"/* never closed"          --parse
"10 / (3 - 3)"             --eval
"int main() { return ; }"  --parse
```

每个失败都要求：

- 退出码非 0。
- stderr 以 `ERROR:` 开头。
- stderr 包含 `line:column`。
- 不能卡死。

对应 prompt：

- 格式错误、非法字符、括号不匹配、缺少分号、未闭合注释、除以 0 都必须失败。
- 失败时不能卡死。
- 错误信息包含位置。

这是高区分度项。很多实现能过 happy path，但会漏错误码、错误格式、错误位置或超时。

## 8. c_like_parse_subset，0.20

要求 `--parse` 接受：

```c
int main() { return 1 + 2 * 3; }
void f() { int x; int y = x + 12; { return y; } }
int x = 12; return x;
{ int a = 1; a + 2; }
int main(){ int a=1; int b = a + 2; return b; }
```

要求 `--parse` 拒绝：

```c
int main( { return 1; }
int = 1;
return ;
int main(){ int x = ; }
int main(){ return (1+2; }
int main(){ int x = 1 }
```

对应 prompt：

- `--parse` 除了表达式，还必须解析 tiny C 子集。
- 变量声明。
- 初始化声明。
- return 语句。
- 表达式语句。
- block。
- 简单函数定义。

这是 modernize 任务区别于普通表达式 parser 的核心项，所以权重较高。

## 为什么去掉 architecture_api_state

原来的 `architecture_api_state` 主要依赖 regex 检查源码中是否出现 `Token`、`TokenType`、`tokenize` 等标记。这个信号较弱：

- 可以被表面定义骗过。
- 不能证明 lexer/parser 真能复用。
- 和行为正确性的关系不够直接。

如果不想重新 rollout，最稳妥的做法是去掉它，把分数分配给可观察行为项。

长期更好的方案是：下一轮任务把 prompt 和 eval 一起升级为真实 C++ API compile test，但那需要重新 rollout。

## 重打分说明

当前修改只影响 eval 权重和评分项，没有改变 prompt 对模型的输入要求。因此已有 `tiny-c-modernize` workspace 可以直接用新版 eval 重新打分。

如果之后再次修改 prompt，就需要重新 rollout，不能只重打分。

