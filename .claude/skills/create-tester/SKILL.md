---
name: create-tester
description: >
  Interactive scaffolding of zigtester.yaml for any project. Detects existing test
  infrastructure (zig build, Python scripts, benchmark tools), asks targeted questions,
  and generates a tailored configuration with appropriate parsers, metrics, and
  thresholds. Covers all 5 fixnet project archetypes (Zig foundation lib, TUN device,
  proxy protocol, DNS component, app/orchestration layer) plus generic Python/Go/Rust
  projects. Uses zigtester's own zigtester init as the baseline, then customizes.
---

# create-tester — zigtester.yaml 手脚架生成

## 触发条件

以下任一情况加载本 skill：
- 用户说"创建 zigtester 配置" / "生成 zigtester.yaml" / "接入 zigtester"
- 用户使用 `/create-tester` 命令
- 用户在新项目中首次提到"测试框架"但没有 `zigtester.yaml`

## 手脚架方法论

### 核心原则

1. **先检测，后提问** — 不要问用户已能从项目文件中推断的信息
2. **只问关键决策** — 目标是 ≤5 个问题完成配置
3. **默认值优先** — 为检测到的模式提供合理默认值，用户确认即可
4. **即时验证** — 生成后用 `zigtester scan` 验证配置可解析

### 工作流

```
检测项目类型 → 发现现有测试 → 提问关键决策 → 生成配置 → 验证
```

---

## Phase 1: 项目检测

按以下顺序检测，第一个命中的类型即为项目主类型。不要问用户"你是什么类型的项目"。

### 检测步骤

1. **读取项目根目录文件列表** — `ls -la <project_dir>/`
2. **检查以下信号文件**：

| 信号 | 判定为 |
|------|--------|
| `build.zig.zon` 存在 | Zig 项目 |
| `pyproject.toml` / `setup.py` 存在 | Python 项目 |
| `go.mod` 存在 | Go 项目 |
| `Cargo.toml` 存在 | Rust 项目 |
| `CMakeLists.txt` 存在 | C/C++ 项目 |

3. **对 Zig 项目，进一步判定子类型**：

| 信号 | 子类型 | 典型项目 |
|------|--------|---------|
| `build.zig` 中 `root_module = b.createModule(...)` 且无 `b.addExecutable` | **Zig 库项目** | zigfoundation, libxev |
| `build.zig` 中有 `tun`/`lwip`/`utun` 引用 | **TUN 设备项目** | zigtun |
| `build.zig` 中有 `xev` 引用 + 代理协议逻辑 | **代理协议项目** | zigproxy, zigoutbounds |
| `build.zig` 中有 `xev` 引用 + DNS 逻辑 | **DNS 组件项目** | zigdns |
| `build.zig` 中有 `xev` + 配置解析 + 多模块编排 | **编排层项目** | zigbox |

4. **对 Python 项目**：

| 信号 | 子类型 |
|------|--------|
| `src/` 下有 MCP Server 入口点 | MCP 工具项目 |
| `tests/` 下有 `test_*.py` | 测试框架项目 |
| `pyproject.toml` 中 `[project.scripts]` 有 CLI | CLI 工具项目 |

### 发现现有测试基础设施

自动扫描以下模式，无需询问用户：

| 检测目标 | 方法 | 对应套件 |
|----------|------|---------|
| `zig build test` 是否可用 | 检查 `build.zig` 中是否有 `b.addTest` 或 `test` step | → `unit` 套件, parser=`zig_test` |
| Python 测试脚本 | `find <dir> -name 'test_*.py' -not -path '*/.venv/*'` | → `functional` 套件 |
| 压测/benchmark 脚本 | `find <dir> -name '*bench*.py'` | → `performance` 套件 |
| 是否有需要 sudo 的测试 | grep `sudo`/`TUN`/`utun` in test scripts | → `sudo: true` |
| 是否有协议测试 | `find <dir> -name '*protocol*' -o -name '*e2e*'` | → parser=`test_protocols` |
| 是否依赖外部服务 | grep `connect`/`http`/`dns`/`echo` in test scripts | → 标记到 description |
| 现有 CI 配置 | `.github/workflows/*.yml` | → 记录到 description 供 Phase 4 使用 |

---

## Phase 2: 提问关键决策

最多 5 个问题。每个问题提供推荐默认值。只问检测无法确定的事项。

### 标准问题流

**Q1: 项目描述**（仅当无 README 或 README 无描述时）
```
这个项目的简短描述是什么？
默认值：<从 README 首行或目录名推断>
```

**Q2: 构建命令**（仅当检测到构建系统但不确定具体命令时）
```
测试前需要执行的构建命令是什么？
默认值：zig build / make / (None)
```

**Q3: 超时偏好**（仅当项目类型暗示长测试时间 — 如 TUN/加密协议测试）
```
测试超时设置？默认 120 秒。
选项：60(快) / 120(标准) / 300(慢，含网络 IO) / 600(长编译)
```

**Q4: 确认功能测试命令**（仅当检测到多个候选脚本时）
```
检测到以下测试脚本，哪些应纳入 functional 层级？
<列出检测到的脚本，默认全选>
```

**Q5: 是否需要性能/压力测试**（仅当未检测到 benchmark 脚本但用户可能想添加时）
```
是否添加性能/压力测试套件？如果项目尚未有 benchmark 脚本，可以先留空模板。
```

**禁止问的问题**：
- "你想用哪个 parser？"（应根据命令自动判定：`zig build test`→`zig_test`、`test_protocols.py`→`test_protocols`、`test_bench.py`→`bench`、其他→`line_count`）
- "你需要什么层级？"（应根据检测结果自动填充）
- "sudo 需要吗？"（应 grep 脚本内容检测 TUN/sudo 引用）

---

## Phase 3: 生成配置

### 内置解析器自动匹配

```
命令关键字               → parser
─────────────────────────────────
zig build test           → zig_test
test_protocols           → test_protocols
test_bench / benchmark   → bench
其他一切                  → line_count
```

### 模板变体

根据检测到的项目类型，选用不同的模板结构。

#### 变体 A: Zig 基础库（如 zigfoundation、libxev）

```yaml
project: {name}
description: "{从 README 提取的简短描述}"
settings:
  work_dir: "."
  build_command: "zig build"
  timeout_default: 120
levels:
  unit:
    - name: "all-tests"
      command: "zig build test"
      parser: zig_test
      timeout: 120
```

#### 变体 B: Zig 代理/TUN 项目（如 zigtun、zigproxy）

```yaml
project: {name}
description: "..."
settings:
  work_dir: "."
  build_command: "zig build"
  timeout_default: 120
levels:
  unit:
    - name: "all-tests"
      command: "zig build test"
      parser: zig_test
      timeout: 120
  functional:
    - name: "{detected_test_name}"
      command: "python3 {detected_test_path}"
      timeout: 60
      sudo: {detected_sudo}
```

#### 变体 C: Zig 出站协议项目（如 zigoutbounds）

```yaml
project: {name}
description: "..."
settings:
  work_dir: "."
  build_command: "zig build"
  timeout_default: 180
levels:
  unit:
    - name: "all-tests"
      command: "zig build test"
      parser: zig_test
      timeout: 180
  functional:
    - name: "crypto-only"
      command: "python3 {test_script} --crypto-only"
      timeout: 60
    - name: "e2e"
      command: "python3 {test_script} --e2e"
      timeout: 120
      depends_on: ["unit.all-tests"]
  performance:
    - name: "bench"
      command: "python3 {bench_script} -c 4 -n 100"
      timeout: 120
      parser: bench
```

#### 变体 D: 编排层项目（如 zigbox）

```yaml
project: {name}
description: "..."
settings:
  work_dir: "."
  build_command: "zig build"
  timeout_default: 120
levels:
  unit:
    - name: "all-tests"
      command: "zig build test"
      parser: zig_test
      timeout: 180
  functional:
    - name: "{protocol_test_name}"
      command: "python3 {protocol_test_path}"
      sudo: true
      timeout: 120
      parser: test_protocols
  performance:
    - name: "{bench_name}"
      command: "python3 {bench_path} --mode {mode} -c 10 -n 100"
      timeout: 120
      parser: bench
      metrics:
        - name: throughput_reqs_per_sec
          pattern: "吞吐: ([0-9.]+) req/s"
        - name: latency_p99_ms
          pattern: "p99: ([0-9.]+)ms"
      thresholds:
        throughput_reqs_per_sec:
          min: 50
```

#### 变体 E: 通用 Python/Go/Rust 项目

```yaml
project: {name}
description: "..."
settings:
  work_dir: "."
  build_command: "{build_command_or_null}"
  timeout_default: 120
levels:
  unit:
    - name: "all-tests"
      command: "{detected_test_command}"
      parser: line_count
      timeout: 120
```

### 生成位置

配置写入 `<project_dir>/zigtester.yaml`。如果已存在：
1. 读取现有配置
2. 提示用户："检测到已有 zigtester.yaml（X 层级 Y 套件），是否覆盖/合并/跳过？"
3. 合并模式：保留现有配置，仅添加检测到的新套件（去重 name）

---

## Phase 4: 验证

生成后立即验证：

```bash
# 1. 用 zigtester 的 scan 验证配置可解析
python3 -m zigtester scan --dir <project_dir> --no-recursive

# 2. 用 Python 直接校验（更快）
python3 -c "
from zigtester.config import parse_config, validate_config
import yaml
with open('<project_dir>/zigtester.yaml') as f:
    raw = yaml.safe_load(f)
errors = validate_config(raw)
if errors:
    for e in errors:
        print(f'❌ {e}')
else:
    cfg = parse_config('<project_dir>/zigtester.yaml')
    total = sum(len(l.suites) for l in cfg.levels.values())
    print(f'✅ 校验通过: {cfg.project} — {total} 套件')
"
```

如果验证失败：
1. 显示具体错误
2. 自动修复已知问题（project 名称不合规、parser 名称拼写错误等）
3. 重新验证

---

## Phase 5: 汇总输出

向用户展示：

```
✅ zigtester.yaml 已创建

项目: <name>
描述: <desc>
层级:
  unit:         <N> 套件 — <套件名列表>
  functional:   <N> 套件 — <套件名列表>
  performance:  <N> 套件 — <套件名列表>
  stress:       <N> 套件 — <套件名列表>

下一步:
  zigtester run <name> --level unit          # 运行单元测试
  zigtester run <name>                       # 运行全部测试
  zigtester list <name>                      # 查看所有套件
```

---

## 快捷模式：为已有 fixnet 兄弟项目生成

对于 `~/works/2025/fixnet/` 下的已知项目，可以直接使用预设模板，跳过检测阶段：

| 项目 | 快捷配置要点 |
|------|-------------|
| **zigfoundation** | 变体 A + 344 tests |
| **zigtun** | 变体 B + sudo: true |
| **zigproxy** | 变体 B + 协议检测测试 |
| **zigdns** | 变体 B + DNS 测试 |
| **zigoutbounds** | 变体 C + crypto-only → E2E 管道 + depends_on |
| **zigbox** | 变体 D + 3 层级 5 套件 |
| **zigtester** (自身) | 变体 E + Python pytest |

---

## 故障排查

| 问题 | 原因 | 解决 |
|------|------|------|
| `zig build test` 找不到 | 不在 zig 项目根目录 | 检查 `work_dir` 设置 |
| 测试脚本路径错误 | 相对路径基准是 `work_dir` | 确保相对 `zigtester.yaml` 所在目录 |
| parser 无法解析输出 | 命令输出格式与 parser 不匹配 | 先用 `line_count`，确认输出格式后再切换 |
| sudo 测试失败 | CI 环境无 sudo 权限 | 添加 `--skip-sudo` 或设为 `sudo: false` |

---

## 参考

- JSON Schema: `zigtester/schemas/zigtester.schema.json`
- 完整设计文档: `zigtester/DESIGN.md`
- 编码规范: `zigtester/CLAUDE.md`
- zigtester CLI 实现: `zigtester/src/cli.py` (cmd_init)
- 配置模板生成: `zigtester/src/config.py` (generate_template)
- 已有配置示例: `zigfoundation/zigtester.yaml`, `zigbox/zigtester.yaml`
