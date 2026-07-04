# Benchmark Case Design Research

日期：2026-07-04

这份文档整理两类调研结果：

- 本地参考项目 `/home/kai/code/benchmark/datasets` 里已经验证过的任务形态。
- 公开模型报告和经典 benchmark 暴露出的高区分度 case 方向。

目标不是复刻某个榜单，而是抽取能迁移到当前 `code-bench` 框架里的任务设计原则。

## 1. 本地 datasets 的可借鉴结构

`/home/kai/code/benchmark/datasets` 比当前 `code-bench` 更接近真实 agent benchmark。每个任务通常包含：

- `TASK.md`：自然语言任务说明。
- `repo_base/`：待修改代码库。
- `repo_test/`：隐藏测试、辅助脚本、参考输入。
- `Dockerfile`：隔离运行环境。

这些任务的共同点是：任务本身不只是补一个函数，而是要求模型在一个已有代码库里定位、修改、验证，且评分按行为维度拆分。

## 2. 本地任务类型观察

### C4 编译器迁移

对应任务：

- `01-refactor-go-c4`
- `02-refactor-rust-c4`

任务目标是把 C4 编译器/解释器移植或重构到另一门语言。测试覆盖多个语言特性，例如算术、指针、控制流、函数调用、表达式优先级。

可借鉴点：

- 这是强结构化任务，模型需要理解解释器状态机，而不是只修单点 bug。
- 隐藏测试天然可以按语言特性拆分，形成细粒度评分。
- Go/Rust 版本会额外引入语言约束，例如所有权、生命周期、错误处理、可变共享状态。

适合迁移到 `code-bench` 的轻量版本：

- `mini-c-interpreter-port`：给一个 Python 小解释器，要求实现 Go/Rust/TS 版本。
- `mini-vm-bytecode`：实现栈机、跳转、函数帧、错误恢复。

### Helpmate / Chess Solver

对应任务：

- `03-proj-chess`
- `04-proj-helpmate-stockfish`
- `05-proj-helpmate-d3`

任务目标是实现或修复象棋/国际象棋 helpmate 搜索。它的区分度来自领域规则、搜索剪枝、状态去重和性能边界。

可借鉴点：

- 很容易设计“看起来过了简单样例，但隐藏局面失败”的测试。
- 正确性不仅是最终答案，还包括最短解、合法走法、重复局面、超时。
- 可以用本地 reference library 生成隐藏 oracle，避免把答案暴露给任务 workspace。

适合迁移到 `code-bench` 的轻量版本：

- `search-solver`：迷宫/推箱子/棋盘覆盖，要求最短路径和去重。
- `rule-engine-solver`：给一组 DSL 规则，要求推导可达状态。

### Linux zstd module support

对应任务：`06-feat-linux6.1-zstdmod`

任务目标是在 Linux 6.1 内核模块加载路径中支持 `.ko.zst`，同时不能破坏已有 `.ko.gz`、`.ko.xz`、未压缩模块。

可借鉴点：

- 典型“新增格式 + 旧路径回归”任务。
- 评分覆盖构建、gzip、xz、未压缩、zstd、大文件、低内存、多次加载。
- 区分度来自系统集成位置、内存分配、错误路径和兼容性，而不是算法难度。

适合迁移到 `code-bench` 的轻量版本：

- `archive-loader`：在已有 loader 中新增 zstd/xz/gzip 自动识别。
- `plugin-decoder`：支持新 wire format，但保持旧格式、错误消息和流式读取行为。

### Babel multi-stage task

对应任务：`07-feat-babel7.28-multi`

任务分多个阶段：

- 阶段 1/2 要求只做设计和规划，不能修改代码。
- 阶段 3 才实现 `babel-node --experimental-repl-await` 相关能力。
- eval 会检查 plan-only 阶段是否真的没有改代码。

可借鉴点：

- 这是很好的 agent 行为约束测试，不只测代码能力。
- 能测模型是否遵守阶段性要求、是否能延迟实现、是否保留上下文。
- 测试覆盖 CLI flag、REPL、eval、config、无 flag 回归、错误处理。

适合迁移到 `code-bench` 的轻量版本：

- `toy-babel-cli`：实现 REPL await/config/eval 的小型 CLI。
- `multi-phase-refactor`：第一阶段只写 plan，第二阶段写 tests，第三阶段实现。

## 3. 外部 benchmark 和模型报告观察

### HumanEval / MBPP

HumanEval 和 MBPP 是经典函数级代码生成 benchmark。它们适合作为基础 sanity check，但对 frontier model 区分度已经偏弱。

对当前项目的启发：

- 不要只做“单文件、单函数、清晰规格”的题。
- visible smoke tests 可以像 HumanEval 一样简单，但隐藏 eval 必须覆盖边界、组合状态和回归。

参考：

- HumanEval: <https://github.com/openai/human-eval>
- MBPP: <https://github.com/google-research/google-research/tree/master/mbpp>

### APPS / CodeContests / LiveCodeBench

APPS 和 CodeContests 偏算法竞赛，难点是复杂题意、边界条件、效率和隐藏测试。LiveCodeBench 强调持续更新、降低污染，并包含代码生成、自修复、执行反馈等维度。

对当前项目的启发：

- 可以引入“新鲜题”或参数化生成隐藏测试，降低记忆污染。
- 对算法题不要只看 AC，而要拆成正确性、复杂度、鲁棒解析、错误输入。
- 适合做 `search-solver`、`scheduler-optimizer`、`stream-parser` 这类任务。

参考：

- APPS: <https://github.com/hendrycks/apps>
- CodeContests: <https://github.com/google-deepmind/code_contests>
- LiveCodeBench: <https://livecodebench.github.io/>

### RepoBench / Aider Polyglot

RepoBench 聚焦 repository-level code completion/retrieval。Aider Polyglot 用多语言真实项目修改来测 edit 能力，通常比单函数题更能区分模型。

对当前项目的启发：

- 区分度来自跨文件定位、调用链理解、局部修改而不破坏已有行为。
- 多语言任务能暴露模型对运行时、类型系统、包管理的真实掌握程度。
- 可以设计同构任务：同一业务逻辑分别给 Python、Go、Rust、TypeScript 版本，比较模型迁移能力。

参考：

- RepoBench: <https://github.com/Leolty/repobench>
- Aider benchmarks: <https://aider.chat/docs/benchmarks.html>

### SWE-bench 系列

SWE-bench 用真实 GitHub issue 测模型是否能在已有 repo 中修 bug。后续有 Verified、Lite、Multilingual、Multimodal、Pro 等变体。模型技术报告常引用这类结果，但公开报告也反复强调：旧 benchmark 容易被污染，真实 repo 环境和隐藏回归测试更重要。

对当前项目的启发：

- 任务说明最好像真实 issue：有现象、有约束，但不直接告诉根因文件。
- eval 要有 fail-to-pass 和 pass-to-pass 两类测试：既修目标 bug，也不能破坏旧行为。
- 不要把 reference implementation 放进 prompt workspace。

参考：

- SWE-bench: <https://www.swebench.com/>
- SWE-bench GitHub: <https://github.com/SWE-bench/SWE-bench>
- SWE-bench Pro: <https://scale.com/research/swe-bench-pro>

### SWT-Bench / SWE-smith / SWE-Lancer

这些 benchmark 把 SWE 任务进一步拆到不同方向：

- SWT-Bench：测模型写测试的能力，要求新测试能让旧代码失败、让修复后代码通过。
- SWE-smith：从真实 repo 合成软件工程任务，用来降低数据污染。
- SWE-Lancer：更贴近自由职业软件任务，强调完整交付而不是单个 patch。

对当前项目的启发：

- 可以增加“写测试本身也是交付物”的任务。
- 可以用现有小型 repo 自动合成 bug，再生成隐藏 oracle。
- 可以加入验收制评分：功能、测试、文档、迁移脚本各占一部分。

参考：

- SWT-Bench: <https://swtbench.com/>
- SWE-smith: <https://swesmith.com/>
- SWE-Lancer: <https://openai.com/index/swe-lancer/>

### Terminal-Bench / OSWorld / tau-bench

Terminal-Bench 测模型在真实终端环境中完成长任务，任务包括构建、调试、训练、反汇编等。OSWorld 测 GUI/计算机使用任务。tau-bench 测 agent 在工具、用户交互和业务 policy 下是否能保持状态一致。

对当前项目的启发：

- 仅跑单元测试不够，可以要求产出文件、CLI 行为、日志、迁移结果。
- 状态机和 policy 任务非常适合做隐藏评分，例如支付、退款、库存、审批流。
- 对 agent 能力的区分点包括：是否能查环境、是否能执行验证、是否能处理中间失败。

参考：

- Terminal-Bench: <https://www.tbench.ai/>
- Terminal-Bench GitHub: <https://github.com/laude-institute/terminal-bench>
- OSWorld: <https://os-world.github.io/>
- tau-bench: <https://github.com/sierra-research/tau-bench>

## 4. 高区分度 case 设计原则

### 真实区分度通常来自组合复杂度

当前 `code-bench` 里最容易失去区分度的题型是“明确告诉接口 + 一两个核心分支 + visible test 暗示完整解法”。强模型和中等模型都能写出差不多的实现。

更好的做法：

- 至少 3 个相互影响的行为维度。
- hidden eval 覆盖维度交叉，而不是只测单点边界。
- visible test 只给基本契约，不泄露隐藏边界。

### 要测“定位”，不只测“填空”

如果所有模板都只留下 `NotImplementedError`，模型主要是在做规格转代码。更难的任务可以给一个已有 repo，让模型从错误现象定位根因。

适合评分的维度：

- 是否找到正确模块。
- 是否避免无关重写。
- 是否保留旧 API。
- 是否补全关键回归测试。

### 要有 pass-to-pass 回归

新增功能题如果只测新路径，模型可以硬编码或破坏旧行为。高质量 eval 应该始终包含：

- 新功能通过。
- 旧功能不退化。
- 错误路径可解释。
- 并发/重复/乱序输入稳定。

### 评分要能部分得分

高区分度不等于全或无。更好的评分结构：

- API/可运行：10%
- visible smoke：10%
- 核心 happy path：15%-20%
- 边界和错误路径：20%-30%
- 回归：15%-25%
- 随机/并发/性能：10%-20%

这样能区分“会写主流程的模型”和“能守住系统语义的模型”。

## 5. 可落地的新 case 方向

### A. toy-babel-cli

灵感来源：`07-feat-babel7.28-multi`、Babel/CLI 类真实任务。

任务形态：

- 给一个小型 JS/TS CLI 或 Python CLI。
- 已有 `run`、`eval`、`repl`、`config` 几个入口。
- 要新增一个 flag，例如 `--await-expr` 或 `--transform-imports`。

隐藏测试：

- flag 被 CLI 接受。
- REPL 路径生效。
- eval 路径生效。
- config 显式启用/禁用。
- 无 flag 时旧行为不变。
- 错误输入给稳定报错。

区分度：

- 强模型会沿现有 CLI/config/repl 边界改。
- 弱模型容易只改一个入口，漏 eval 或 config。
- 容易发现“只过 visible test”的实现。

### B. archive-loader

灵感来源：`06-feat-linux6.1-zstdmod`。

任务形态：

- 给一个已有资源加载器，已支持 plain/gzip。
- 要新增 xz/zstd 或 framed chunk 格式。
- 不能破坏 streaming、错误消息、旧格式自动识别。

隐藏测试：

- magic number 检测。
- 文件扩展名和内容不一致。
- truncated frame。
- 大文件流式读取。
- 多次连续加载。
- 旧 gzip/plain 回归。

区分度：

- 强模型会抽象格式探测和错误处理。
- 弱模型容易用文件名判断、一次性读入、吞掉异常。

### C. config-migration-with-regressions

灵感来源：真实 SaaS/CLI 配置迁移和 pass-to-pass 回归。

任务形态：

- 给旧版本配置 schema。
- 要实现 v1 -> v2 -> v3 迁移。
- 要保持未知字段、注释/顺序或至少 round-trip 语义。

隐藏测试：

- 重复迁移幂等。
- 部分已迁移配置。
- unknown fields 保留。
- deprecated 字段映射。
- 冲突配置报错。
- 大量配置性能。

区分度：

- 强模型会明确 schema、版本、幂等和错误策略。
- 弱模型容易覆盖用户字段或只处理样例。

### D. test-writer / regression-test task

灵感来源：SWT-Bench。

任务形态：

- 给一个小 bug 和已有实现。
- 要求模型只写测试，不改业务代码。
- eval 用旧代码跑测试应失败，用 reference fixed code 跑测试应通过。

隐藏测试：

- 检查业务代码没有改。
- 检查测试能失败旧实现。
- 检查测试不过度绑定实现细节。
- 检查测试包含至少一个边界场景。

区分度：

- 能区分模型是否真的理解 bug。
- 能抓出“写一个永远 pass 的测试”或“直接改实现”的 agent。

### E. tool-state-machine

灵感来源：tau-bench、支付/库存/审批系统。

任务形态：

- 给一个有状态服务，例如订单、库存、退款、审批。
- 输入是乱序事件、重复事件、并发事件。
- 要求最终状态满足 policy。

隐藏测试：

- 幂等 event id。
- 非法状态转移。
- out-of-order 事件。
- 并发重复请求。
- 金额/库存上限。
- 审计日志顺序。

区分度：

- 强模型会建立状态机和锁/事务边界。
- 弱模型容易只靠 if/else happy path。

## 6. 对当前已做两个 case 的定位

### ttl-lru-cache

这个 case 测的是“小而密”的数据结构实现：

- fake clock 注入。
- TTL 边界。
- LRU refresh on get。
- overwrite 是否刷新 TTL 和 LRU。
- expired key 是否占容量。
- capacity zero。
- randomized reference comparison。

区分度来源：

- 弱模型容易写成普通 dict + TTL，漏 LRU 或 overwrite。
- 中等模型能过主流程，但容易在过期清理和容量交互上错。
- 强模型会把时间、容量、顺序维护作为一致性问题处理。

### webhook-idempotency

这个 case 测的是“业务状态机 + 幂等 + 并发”：

- event id 去重。
- authorize/capture/refund/void 状态转移。
- capture/refund 金额上限。
- out-of-order 事件。
- concurrent duplicate event。
- concurrent refund ceiling。

区分度来源：

- 弱模型容易只写 happy path。
- 中等模型会做 event id 去重，但漏并发原子性。
- 强模型会把 payment 状态、event log、金额累计和锁边界统一起来。

## 7. 推荐优先级

短期最值得继续做：

1. `toy-babel-cli`：最贴近真实 repo 修改，能测 CLI/config/REPL 多入口一致性。
2. `archive-loader`：新增格式 + 老格式回归，评分清晰，隐藏测试好写。
3. `config-migration-with-regressions`：很适合业务系统，能测幂等和兼容性。
4. `test-writer`：补足“只写测试/不改实现”的能力维度。

如果想把 benchmark 从“代码补全”升级到“agent 工程能力”，下一步应优先增加：

- 多文件已有 repo。
- plan-only 阶段。
- pass-to-pass 回归。
- 隐藏 reference oracle。
- 随机化或参数化 eval。

