# MiniProt Virtual Lab（迷你蛋白虚拟实验室）

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**面向蛋白质与酶研究的 AI-人类协作框架。**

> [English Version（英文版）](README.md)

将 [Virtual Lab](https://github.com/zou-group/virtual-lab)（Zou Group, *Nature* 2025）的**多智能体会议架构**与 [MiniProt](https://github.com/SJTU-software-2026/enzyme_update) 的**生物信息学工具能力**相结合。人类研究员通过团队会议和单独工作会议，与一组专业 LLM 智能体进行协作。

![架构图](figures/architecture.png)

---

## 1. 核心特性

| 特性 | 描述 |
|------|------|
| **多智能体会议** | 团队会议（PI + 5 专家）+ 单独会议（专家 + 工具 + 审稿人） |
| **33 个生信工具** | UniProt、AlphaFold、AutoDock Vina、HMMER、MAFFT、Foldseek、PyMOL... |
| **YAML 配置文件** | 单一 `config/settings.yaml`，每个选项都有中英文注释 |
| **多 AI 提供商** | DeepSeek V4（默认）、OpenAI GPT-5.2、SJTU 服务器、自定义端点 |
| **单智能体 API 隔离** | 每个智能体可使用不同 API Key/模型，提升缓存命中率 |
| **结构化日志** | JSONL 事件流、API 追踪、工具追踪、智能体发言记录 |
| **科学审稿人** | 自动审查专家输出，检查正确性和完整性 |
| **会议历史** | 加载之前的会议作为上下文，智能体可继续之前的工作 |
| **工具按需下载** | Git submodule + 安装脚本，核心项目仅 ~1 MB |

![会议流程](figures/meeting_flow.png)

---

## 2. 快速开始

```bash
git clone https://github.com/SJTU-software-2026/miniprot_virtual_lab.git
cd miniprot_virtual_lab
pip install -r requirements.txt

# 1. 配置
cp config/settings.example.yaml config/settings.yaml
# 编辑 settings.yaml — 填入 API Key

# 2. 获取工具二进制（任选一种）
docker-compose build                                     # Docker — 全部预装
# 或: cp config/tool_paths.example.yaml config/tool_paths.yaml  # 指向本地工具
# 或: bash scripts/setup_tools.sh                              # 下载到 tools_src/

# 3. 运行
python run.py --demo
python run.py                          # 交互模式
```

---

## 3. 系统架构

虚拟实验室将研究组织为一系列**会议**：

```
人类研究员（提出问题）
      │
      ▼
┌─────────────────────────────────────────┐
│  团队会议：PI 召集 → 专家讨论 → PI 分配任务│
└─────────────────────────────────────────┘
      │
      ▼ （并行单独会议）
┌──────────┐ ┌──────────┐ ┌──────────┐
│   专家    │ │   专家    │ │   专家    │
│ 执行工具  │ │ 执行工具  │ │ 执行工具  │
│     │     │ │     │     │ │     │     │
│ 审稿人审查│ │ 审稿人审查│ │ 审稿人审查│
└──────────┘ └──────────┘ └──────────┘
      │            │            │
      ▼            ▼            ▼
┌─────────────────────────────────────────┐
│  团队会议：PI 审阅结果 → 最终报告        │
└─────────────────────────────────────────┘
```

![智能体角色](figures/agent_roles.png)

---

## 4. 安装

### 4.1 环境要求

- Python 3.10+（推荐 3.12）
- Git

### 4.2 克隆与安装

```bash
git clone https://github.com/SJTU-software-2026/miniprot_virtual_lab.git
cd miniprot_virtual_lab
pip install -r requirements.txt
```

所有生信工具实现已内置在 `src/miniprot_virtual_lab/vendor/` — 无需额外克隆 enzyme_update。

### 4.3 配置

```bash
cp config/settings.example.yaml config/settings.yaml
# 编辑 settings.yaml — 填入 API Key
```

**配置优先级（从高到低）：**

| 优先级 | 来源 | 示例 |
|--------|------|------|
| 1 | Agent 属性（Python 代码） | `Agent(..., api_key="sk-...")` |
| 2 | 环境变量 | `MINIPROT_DOCKING_SPECIALIST_API_KEY` |
| 3 | YAML `agents.<slug>` | `agents.docking_specialist.api_key` |
| 4 | YAML `global` | `global.api_key` |
| 5 | 提供商预设环境变量 | `MINIPROT_PROVIDER` |
| 6 | 全局环境变量 | `DEEPSEEK_API_KEY` |
| 7 | 硬编码默认值 | DeepSeek V4 |

切换提供商：`export MINIPROT_PROVIDER=openai`（deepseek | openai | sjtu | custom）。

> 如果 `settings.yaml` 缺失，程序自动回退到 `settings.example.yaml` 并打印醒目的改名提醒。

### 4.4 工具二进制文件 — 四种获取方式

#### A. 子模块 + 安装脚本（最轻量）

```bash
cd miniprot_virtual_lab                                 # 必须在仓库目录内
git submodule update --init tools_src/omegafold          # 可选：OmegaFold
bash scripts/setup_tools.sh                              # Linux/macOS：P2Rank + Java
powershell -File scripts/setup_tools.ps1                 # Windows：P2Rank + Java
```

核心项目 ~1 MB，工具按需下载，大文件不进 git。

#### B. Docker（全部预装）

```bash
docker-compose build
docker-compose run --rm miniprot-vlab python run.py --demo
```

25+ 工具预装。详见 [4.5 Docker 详情](#45-docker-详情)。

#### C. 本地安装

```bash
cp config/tool_paths.example.yaml config/tool_paths.yaml
# 编辑 tool_paths.yaml — 指向已有工具路径
```

#### D. 手动下载

下载到 gitignored 的 `tools_src/` 目录，然后配置 `tool_paths.yaml`：

| 工具 | 大小 | 来源 |
|------|------|------|
| P2Rank | ~260 MB | https://github.com/rdk/p2rank/releases |
| OmegaFold | ~5 MB (git) | `git submodule update --init` |
| OpenJDK 17 | ~180 MB | https://adoptium.net/download/ |

### 4.5 Docker 详情

镜像包含 25+ 工具（MAFFT、MMseqs2、CD-HIT、Foldseek、TM-align、AutoDock Vina、Open Babel、Meeko、FastTree、PyMOL、P2Rank、Java 17、ETE、ESMFold、OmegaFold...）。

```bash
docker-compose build --no-cache
docker-compose run --rm miniprot-vlab python run.py --demo
docker-compose run --rm miniprot-vlab                       # 交互模式

# 带环境变量
DEEPSEEK_API_KEY="sk-..." docker-compose run --rm miniprot-vlab
```

挂载卷：`./data`、`./meetings`、`./logs` 持久化到宿主机。`./config/settings.yaml` 以只读方式挂载。

### 4.6 验证

```bash
python run.py --providers     # 列出 AI 提供商
python run.py --demo          # 运行演示流水线
```

---

## 5. 使用方法

### 5.1 交互模式

```bash
python run.py
```

```
Virtual Lab> /team 设计一个在细菌中寻找新型脂肪酶的流程
Virtual Lab> /task search 在 UniProt 中搜索脂肪酶，只搜 reviewed
Virtual Lab> /task structure 获取 P12345 的 AlphaFold 结构
Virtual Lab> /history             # 列出已保存的会议
Virtual Lab> /load 01_planning    # 加载会议作为上下文
Virtual Lab> /agents              # 列出智能体及 API 配置
Virtual Lab> /demo                # 运行演示
```

### 5.2 命令行模式

```bash
python run.py --agenda "寻找胰岛素蛋白及其三维结构"
python run.py --provider openai --demo
python run.py --context meetings/01_planning.json --agenda "继续之前的项目..."
```

### 5.3 Python API

```python
from pathlib import Path
from miniprot_virtual_lab import (
    run_meeting, RunLogger,
    PRINCIPAL_INVESTIGATOR, PROTEIN_SEARCH_SPECIALIST, DEFAULT_TEAM,
)

rl = RunLogger(Path("./logs/experiment_1"))

# 团队会议
summary = run_meeting(
    meeting_type="team",
    agenda="识别和表征来自嗜热细菌的新型纤维素酶...",
    team_lead=PRINCIPAL_INVESTIGATOR,
    team_members=DEFAULT_TEAM,
    save_dir=Path("./meetings"),
    num_rounds=3, return_summary=True, run_logger=rl,
)

# 单独会议（带工具执行）
run_meeting(
    meeting_type="individual",
    agenda="搜索 UniProt 中的纤维素酶，下载 FASTA...",
    team_member=PROTEIN_SEARCH_SPECIALIST,
    save_dir=Path("./meetings"),
    enable_tools=True, run_logger=rl,
)

rl.finalize()
```

---

## 6. 智能体角色

| 智能体 | 工具 | 职责 |
|--------|------|------|
| **首席研究员 (PI)** | — | 领导团队、综合讨论、分配任务 |
| **蛋白质搜索专家** | `uniprot_search`, `ncbi_search` | 在 UniProt/NCBI 中搜索蛋白质 |
| **结构专家** | `alphafold`, `pdb`, `foldseek`, `tmalign`, `esmfold`, `omegafold`, `structure_from_fasta`, `structure_alignment_batch`, `similarity_matrix` | 获取/预测三维结构 |
| **化学专家** | `smiles` | 查找化合物、准备配体 |
| **对接专家** | `autodock_vina`, `pocket_picker`, `pocket_box`, `pdb_repair` | 分子对接 |
| **序列分析专家** | `sequence_alignment`, `hmmer`, `mmseqs2`, `cdhit`, `protein_properties`, `pymol`, `ete`, `merger`, `pdb_merge`, `fasta_convert`, `sequence_length_filter`, `sequence_similarity` | MSA、同源搜索、系统发育 |
| **科学审稿人** | — | 审查输出，检查正确性和完整性 |

完整 33 工具参考见 [TOOL_GUIDE.md](src/miniprot_virtual_lab/tools/TOOL_GUIDE.md)。

---

## 7. 会议类型

### 7.1 团队会议

```
第 1 轮：PI 开场 → 专家 1 → ... → PI 综合
第 2..N 轮：专家发言 → PI 综合
最终轮：PI 产出总结 + 任务分配
```

### 7.2 单独会议（带工具执行）

```
专家接收任务 → 调用工具（JSON action block）
  → 审稿人审查 → 专家修改（如有需要）
```

工具调用格式：`{"action": "run_tool", "tool": "uniprot_search", "args": {"query": "insulin", "limit": 5}}`

### 7.3 从之前的会议继续

```bash
Virtual Lab> /history                    # 列出已保存的会议
Virtual Lab> /load 01_team_planning      # 加载为上下文
Virtual Lab> /context                    # 查看已加载的上下文
Virtual Lab> /team 继续我们的项目         # 智能体会引用之前的讨论
```

```python
from miniprot_virtual_lab import load_meeting_context, run_meeting, PRINCIPAL_INVESTIGATOR, DEFAULT_TEAM
from pathlib import Path

ctx = load_meeting_context("meetings/01_team_planning.json")
run_meeting(
    meeting_type="team",
    agenda="继续之前的项目...",
    team_lead=PRINCIPAL_INVESTIGATOR,
    team_members=DEFAULT_TEAM,
    save_dir=Path("./meetings"),
    summaries=ctx["summaries"],
    contexts=ctx["contexts"],
)
```

---

## 8. 日志

每次运行在 `logs/run_<timestamp>/` 下生成结构化日志：

| 文件 | 内容 |
|------|------|
| `run.log` | 文本日志 |
| `run.jsonl` | 结构化事件流（每行一个 JSON） |
| `api_calls.json` | LLM API 调用 — 模型、Token、延迟、费用 |
| `tools.json` | 工具执行 — 成功/失败、耗时 |
| `discussion.jsonl` | 每个智能体的完整发言 — 内容、轮次 |

```python
from miniprot_virtual_lab import RunLogger
rl = RunLogger(Path("./logs/experiment"))
rl.log_api_call(agent="Search", model="deepseek-v4-pro", input_tokens=1500, output_tokens=800, latency_ms=3200)
rl.finalize()
```

---

## 9. 项目结构

```
miniprot_virtual_lab/
├── run.py                             # 入口（CLI + 演示 + API）
├── Dockerfile / docker-compose.yml    # Docker 环境
├── requirements.txt
├── README.md / README_CN.md
├── config/
│   ├── settings.example.yaml          # 用户配置模板
│   └── tool_paths.example.yaml        # 本地工具路径模板
├── scripts/
│   ├── draw_architecture.py           # 图表生成
│   ├── setup_tools.sh                 # Linux/macOS 工具下载
│   └── setup_tools.ps1                # Windows 工具下载
├── tools_src/                         # 可选工具下载（gitignored）
│   ├── omegafold/                     # Git submodule
│   ├── p2rank/                        # 手动下载
│   └── README.md
└── src/miniprot_virtual_lab/
    ├── agent.py                       # 智能体数据类
    ├── config.py                      # 提供商 + YAML 配置解析
    ├── constants.py                   # 工具分类、工作流
    ├── logging_config.py              # 结构化日志
    ├── prompts.py                     # 智能体角色、会议模板
    ├── run_meeting.py                 # 会议编排 + 上下文加载
    ├── utils.py                       # Token、费用、I/O
    ├── vendor/                        # 内置 enzyme_update 工具（33 个实现）
    │   ├── tool_runner.py             # ToolManager 注册中心
    │   ├── tools/                     # 全部工具 .py 文件
    │   └── utils/                     # path_utils, pdb_clean, fasta_parser
    └── tools/                         # 工具包（8 类别）
        ├── TOOL_GUIDE.md              # 33 工具参考指南
        ├── bridge.py / schemas.py     # ToolBridge + 归一化
        ├── tool_paths.py              # 本地路径解析
        └── {search,structure,chemistry,docking,
             sequence,visualization,utility,specialized}/
```

生成架构图：`python scripts/draw_architecture.py`

---

## 许可证

MIT.

## 引用

Swanson, K., Wu, W., Bulaong, N.L. et al. *The Virtual Lab of AI agents designs new SARS-CoV-2 nanobodies.* Nature (2025). https://doi.org/10.1038/s41586-025-09442-9
