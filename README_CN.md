# MiniProt Virtual Lab（迷你蛋白虚拟实验室）

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**面向蛋白质与酶研究的 AI-人类协作框架。**

> [English Version（英文版）](README.md)

将 [Virtual Lab](https://github.com/zou-group/virtual-lab)（Zou Group, *Nature* 2025）的**多智能体会议架构**与 [MiniProt](https://github.com/SJTU-software-2026/enzyme_update) 的**生物信息学工具能力**相结合。人类研究员通过结构化的团队会议和独立工作会议，与一组专业 LLM 智能体（各自拥有特定蛋白质/酶工具的访问权限）进行协作。

![架构图](figures/architecture.png)

---

## 目录

- [核心特性](#核心特性)
- [快速开始](#快速开始)
- [系统架构](#系统架构)
- [安装](#安装)
- [配置文件](#配置文件)
- [使用方法](#使用方法)
- [智能体角色](#智能体角色)
- [会议类型](#会议类型)
- [日志系统](#日志系统)
- [内置演示](#内置演示)
- [项目结构](#项目结构)
- [与原始项目的对比](#与原始项目的对比)

---

## 核心特性

| 特性 | 描述 |
|------|------|
| **多智能体会议** | 团队会议（PI + 专家）+ 单独会议（专家 + 工具 + 审稿人） |
| **40+ 生信工具** | UniProt、AlphaFold、AutoDock Vina、HMMER、MAFFT、Foldseek、PyMOL... |
| **YAML 配置文件** | 只需编辑一个 `config/settings.yaml` 文件，无需复杂的环境变量 |
| **多 AI 提供商** | DeepSeek V4（默认）、OpenAI GPT-5.2、SJTU 服务器、自定义端点 |
| **单智能体独立 API** | 每个智能体可使用不同 API Key / 模型，实现提示缓存隔离 |
| **结构化日志** | JSONL 事件流、API 调用追踪、工具执行追踪、Token/费用统计 |
| **科学审稿人** | 自动审查专家输出，检查正确性和完整性 |

---

## 快速开始

```bash
# 1. 设置 API Key（环境变量或配置文件均可）
export DEEPSEEK_API_KEY="your_key_here"

# 2. (推荐) 编辑 config/settings.yaml 自定义设置
#    文件中每个选项都有详细的中英文注释

# 3. 运行演示
python run.py --demo

# 4. 交互模式
python run.py

# 5. 切换 AI 提供商（可选）
python run.py --provider openai --demo
```

---

## 系统架构

![会议流程](figures/meeting_flow.png)

虚拟实验室将研究组织为一系列**会议**：

1. **团队会议**：PI 召集专家 → 多轮讨论 → 任务分配
2. **单独会议**：专家通过 MiniProt 执行工具 → 审稿人审查 → 专家修改
3. **迭代循环**：团队评审 → 新任务 → 执行 → 重复

```
人类研究员（提出问题）
      │
      ▼
┌─────────────────────────────────────────┐
│           团队会议（Team Meeting）        │
│  PI 召集 → 专家讨论 → PI 综合            │
│  产出：研究计划 + 任务分配                │
└─────────────────────────────────────────┘
      │
      ▼ （并行单独会议）
┌────────────┐ ┌────────────┐ ┌────────────┐
│  单独会议   │ │  单独会议   │ │  单独会议   │
│            │ │            │ │            │
│ 智能体调用  │ │ 智能体调用  │ │ 智能体调用  │
│ MiniProt   │ │ MiniProt   │ │ MiniProt   │
│ 工具执行   │ │ 工具执行   │ │ 工具执行   │
│     │      │ │     │      │ │     │      │
│ 审稿人审查 │ │ 审稿人审查 │ │ 审稿人审查 │
└────────────┘ └────────────┘ └────────────┘
      │              │              │
      ▼              ▼              ▼
┌─────────────────────────────────────────┐
│         团队会议（评审与综合）            │
│  PI 审阅结果 → 团队讨论 → 最终报告       │
└─────────────────────────────────────────┘
```

---

## 安装

### 方式 A：Docker（推荐 — 所有工具预装）

Docker 镜像包含 Python 3.12 + **25+ 生物信息学工具**（MAFFT、MMseqs2、CD-HIT、AutoDock Vina、Open Babel、P2Rank、Foldseek、TM-align、PyMOL、FastTree、ETE、OmegaFold、ESMFold 等），无需手动安装。

```bash
git clone https://github.com/SJTU-software-2026/miniprot_virtual_lab.git
cd miniprot_virtual_lab

# 1. 配置
cp config/settings.example.yaml config/settings.yaml
# 编辑 settings.yaml — 填入 API Key

# 2. 构建镜像（首次约 10 分钟，约 4 GB）
docker-compose build

# 3. 运行演示
docker-compose run --rm miniprot-vlab python run.py --demo

# 4. 交互模式
docker-compose run --rm miniprot-vlab
```

### 方式 B：手动安装

```bash
git clone https://github.com/SJTU-software-2026/enzyme_update.git
git clone https://github.com/SJTU-software-2026/miniprot_virtual_lab.git

cd enzyme_update && pip install -r requirements.txt
cd ../miniprot_virtual_lab && pip install -r requirements.txt
```

### 2. 配置

```bash
# 第一步：复制示例配置文件
cp config/settings.example.yaml config/settings.yaml

# 第二步：编辑 settings.yaml — 填入你的 API Key
# 每个选项都有中英文详细注释

# 第三步（可选）：用环境变量覆盖
export DEEPSEEK_API_KEY="your_key_here"
```

> **重要：** 程序首先查找 `config/settings.yaml`。如果找不到，会回退到 `settings.example.yaml` 并打印警告提醒你改名。`settings.yaml` 已在 `.gitignore` 中，你的 API Key 不会被提交到 git。

### 3. 验证安装

```bash
python run.py --providers     # 列出可用 AI 提供商
python run.py --demo          # 运行演示流水线
```

---

## 配置文件

所有设置通过 **`config/settings.yaml`** 管理 — 一个包含中英文详细注释的单一文件。无需设置十几个环境变量。

### YAML 配置示例

```yaml
# config/settings.yaml

global:
  provider: deepseek              # deepseek | openai | sjtu | custom
  api_key: ""                     # 留空则使用 DEEPSEEK_API_KEY 环境变量
  model: deepseek-v4-pro          # 所有智能体的默认模型

meeting:
  default_team_rounds: 3          # 团队会议默认讨论轮数
  creative_temperature: 0.7       # 创意讨论的 temperature
  precise_temperature: 0.2        # 精确工具执行的 temperature

agents:
  principal_investigator:
    temperature: 0.7              # PI 使用更高的创造性

  docking_specialist:
    # api_key: "sk-docking-only"  # 可选：为此智能体使用独立 API Key
    # model: gpt-5.2             # 可选：使用不同模型
    temperature: 0.2

logging:
  log_dir: logs                   # 日志目录
  save_api_trace: true            # 保存 API 调用追踪
  save_tool_trace: true           # 保存工具执行追踪

output:
  meetings_dir: meetings          # 会议记录目录
  data_dir: data/outputs          # 工具输出目录
```

### 配置优先级（从高到低）

| 优先级 | 来源 | 示例 |
|--------|------|------|
| 1 | Python 代码中的智能体属性 | `Agent(..., api_key="sk-...")` |
| 2 | 环境变量 | `MINIPROT_DOCKING_SPECIALIST_API_KEY` |
| 3 | **YAML `agents.<slug>`** | `agents.docking_specialist.api_key` |
| 4 | **YAML `global`** | `global.api_key` |
| 5 | 提供商预设环境变量 | `MINIPROT_PROVIDER` |
| 6 | 全局环境变量 | `DEEPSEEK_API_KEY` |
| 7 | 硬编码默认值 | DeepSeek V4 |

### 单智能体 API 隔离（提升缓存命中率）

每个智能体有**不同的系统提示词**。使用独立的 API Key 可以隔离缓存命名空间，提高缓存命中率：

```yaml
# config/settings.yaml — 缓存隔离示例
agents:
  principal_investigator:
    api_key: "sk-pi-xxxx"
    model: deepseek-v4-pro
  scientific_critic:
    api_key: "sk-critic-xxxx"
    model: deepseek-chat           # 审稿任务用便宜模型即可
  docking_specialist:
    api_key: "sk-docking-xxxx"
    model: gpt-5.2                 # 甚至可以用不同厂商的模型
```

或通过环境变量：
```bash
export MINIPROT_PROTEIN_SEARCH_SPECIALIST_API_KEY="sk-search"
export MINIPROT_DOCKING_SPECIALIST_MODEL="gpt-5.2"
```

### 切换 AI 提供商

```bash
# 方式 1：在 config/settings.yaml 中设置 global.provider

# 方式 2：环境变量（覆盖 YAML）
export MINIPROT_PROVIDER=openai

# 方式 3：命令行
python run.py --provider openai --demo
```

---

## 使用方法

### 交互模式

```bash
python run.py
```

```
Virtual Lab> /team 设计一个在细菌中寻找新型脂肪酶的流程
Virtual Lab> /task search 在 UniProt 中搜索脂肪酶，只搜 reviewed
Virtual Lab> /task structure 获取 P12345 的 AlphaFold 结构
Virtual Lab> /agents               # 列出所有智能体及其 API 配置
Virtual Lab> /providers            # 列出 AI 提供商预设
Virtual Lab> /workflow             # 显示酶挖掘参考工作流
Virtual Lab> /demo                 # 运行演示
```

### 命令行模式

```bash
# 单次团队会议
python run.py --agenda "寻找胰岛素蛋白及其三维结构"

# 指定提供商
python run.py --provider openai --demo

# 自定义日志目录
python run.py --log-dir ./my_logs --demo
```

### Python API

```python
from pathlib import Path
from miniprot_virtual_lab import (
    run_meeting, RunLogger,
    PRINCIPAL_INVESTIGATOR, PROTEIN_SEARCH_SPECIALIST,
    DOCKING_SPECIALIST, DEFAULT_TEAM,
)

# 启用结构化日志
rl = RunLogger(Path("./logs/experiment_1"))

# 团队会议
summary = run_meeting(
    meeting_type="team",
    agenda="识别和表征来自嗜热细菌的新型纤维素酶...",
    team_lead=PRINCIPAL_INVESTIGATOR,
    team_members=DEFAULT_TEAM,
    save_dir=Path("./meetings"),
    num_rounds=3,
    temperature=0.7,
    return_summary=True,
    run_logger=rl,
)

# 单独会议（带工具执行）
run_meeting(
    meeting_type="individual",
    agenda="搜索 UniProt 中的纤维素酶，下载 FASTA...",
    team_member=PROTEIN_SEARCH_SPECIALIST,
    save_dir=Path("./meetings"),
    enable_tools=True,
    run_logger=rl,
)

# 完成日志
summary = rl.finalize()
print(f"日志保存在: {summary['log_dir']}")
```

---

## 智能体角色

![智能体角色](figures/agent_roles.png)

| 智能体 | 工具 | 职责 |
|--------|------|------|
| **首席研究员 (PI)** | (无) | 领导团队、制定议程、综合讨论、分配任务 |
| **蛋白质搜索专家** | `uniprot_search`, `ncbi_search` | 在 UniProt/NCBI 中搜索蛋白质 |
| **结构专家** | `alphafold`, `pdb`, `foldseek`, `tmalign`, `esmfold`, `omegafold`, `structure_from_fasta`, `structure_alignment_batch`, `similarity_matrix` | 获取/预测三维结构 |
| **化学专家** | `smiles` | 查找化合物、准备配体 |
| **对接专家** | `autodock_vina`, `pocket_picker`, `pocket_box`, `pdb_repair` | 分子对接 |
| **序列分析专家** | `sequence_alignment`, `hmmer`, `mmseqs2`, `cdhit`, `protein_properties`, `pymol`, `ete`, `merger`, `pdb_merge`, `fasta_convert`, `sequence_length_filter`, `sequence_similarity` | 多序列比对、同源搜索、系统发育 |
| **科学审稿人** | (无) | 审查输出，检查正确性和完整性 |

---

## 会议类型

### 团队会议 (`meeting_type="team"`)

多智能体讨论格式：

```
第 1 轮：PI 开场 → 专家 1 → 专家 2 → ... → PI 综合
第 2 轮：专家 1 → 专家 2 → ... → PI 综合
...
最终轮：PI 产出总结 + 任务分配
```

### 单独会议 (`meeting_type="individual"`)

单智能体执行任务（带工具调用）+ 审稿人审查：

```
专家接收任务 → 调用工具（JSON action block）→ 最终回答
    → 审稿人审查 → 专家修改（如有需要）
```

**工具调用格式**（智能体输出此 JSON 来调用 MiniProt 工具）：
```json
{"action": "run_tool", "tool": "uniprot_search", "args": {"query": "insulin", "limit": 5}}
```

---

## 日志系统

每次运行在 `logs/run_<timestamp>/` 下生成完整结构化日志：

| 文件 | 内容 |
|------|------|
| `run.log` | 人类可读文本日志（所有级别） |
| `run.jsonl` | 结构化事件流（每行一个 JSON） |
| `api_calls.json` | 所有 LLM API 调用（含 Token、延迟、费用） |
| `tools.json` | 所有工具执行（含成功/失败状态） |
| `discussion.jsonl` | **每个智能体的完整发言** — 含智能体名、轮次、用途 |

**事件类型：** `run_start`、`meeting_start`、`api_call`、`tool_call`、`agent_config`、`agent_response`、`phase`、`critic_satisfied`、`error`、`warning`、`run_end`

---

## 从之前的会议继续

智能体可以读取之前的会议记录作为上下文，支持跨多个会话的长期研究项目。

### CLI（交互模式）

```bash
Virtual Lab> /history                    # 列出所有已保存的会议
Virtual Lab> /load 01_team_planning      # 加载会议作为上下文
Virtual Lab> /context                    # 查看当前已加载的上下文
Virtual Lab> /team 继续我们的项目         # 智能体会引用之前的讨论
Virtual Lab> /clearctx                   # 清除上下文
```

### CLI（命令行）

```bash
python run.py --agenda "继续胰岛素项目..." \
     --context meetings/01_team_planning.json meetings/02a_protein_search.json
```

### Python API

```python
from miniprot_virtual_lab import load_meeting_context, list_saved_meetings

# 浏览已保存的会议
for m in list_saved_meetings("meetings"):
    print(f"{m['name']}: {m['agents']} ({m['turns']} 轮)")

# 加载为上下文
ctx = load_meeting_context("meetings/01_team_planning.json")

# 传入下一个会议
run_meeting(
    meeting_type="team",
    agenda="继续胰岛素项目的下一步...",
    summaries=ctx["summaries"],
    contexts=ctx["contexts"],
    ...
)
```

---

## 工具二进制文件 — 三种获取方式

许多工具需要外部二进制文件（MAFFT、Vina、P2Rank 等），你有**三种选择**：

### 方式 1：Docker（最简单，全部预装）

```bash
docker-compose build && docker-compose run --rm miniprot-vlab python run.py --demo
```

25+ 工具预装，零手动配置。详见上方 [Docker 安装](#安装) 章节。

### 方式 2：使用本地已安装的工具

如果你电脑上已有 MAFFT、Vina、P2Rank 等工具，告诉 MiniProt 它们的位置即可：

```bash
cp config/tool_paths.example.yaml config/tool_paths.yaml
# 编辑 tool_paths.yaml —— 填入本地工具路径
```

程序会自动检测 `config/tool_paths.yaml`。只配置你有的工具，其余回退到 PATH/Docker。

### 方式 3：下载到 `tools_src/`（项目内，git 忽略）

将工具直接下载到项目的 `tools_src/` 目录：

```
tools_src/                         ← 已被 gitignore（不会提交）
├── p2rank/
│   └── p2rank_2.5.1/              ← 下载自 https://github.com/rdk/p2rank/releases
├── omegafold/                     ← git clone https://github.com/HeliXonProtein/OmegaFold
└── java/
    └── jdk-17/                    ← 下载自 https://adoptium.net/download/
```

然后编辑 `config/tool_paths.yaml` 指向这些本地路径。`tools_src/` 已在 `.gitignore` 中，大文件（~700MB+）永远不会被提交到 git。

| 工具 | 大小 | 下载方式 |
|------|------|---------|
| P2Rank | ~260 MB | `wget https://github.com/rdk/p2rank/releases/download/2.5.1/p2rank_2.5.1.tar.gz` |
| OmegaFold | ~5 MB (git) | `git clone https://github.com/HeliXonProtein/OmegaFold.git` |
| OpenJDK 17 | ~180 MB | https://adoptium.net/download/ |

> **提示：** 你也可以把工具装到系统任意位置（如 `C:\tools\`、`/opt/bioinf/`），通过 `tool_paths.yaml` 引用即可 —— 无需放在项目目录内。

---

## 内置演示

`python run.py --demo` 运行完整的 3 阶段酶挖掘流程：

| 阶段 | 会议类型 | 参与者 | 目的 |
|------|---------|--------|------|
| **1. 规划** | 团队会议（2 轮） | PI + 5 位专家 | 设计研究方法 |
| **2a. 搜索** | 单独会议 | 蛋白质搜索专家 | 在 UniProt 中查找 TPH 酶 |
| **2b. 结构** | 单独会议 | 结构专家 | 下载 TPH2 AlphaFold 结构 |
| **2c. 对接** | 单独会议 | 对接专家 | 将色氨酸对接到 TPH2 |
| **3. 评审** | 团队会议（2 轮） | PI + 5 位专家 | 综合发现，规划下一步 |

---

## 项目结构

```
miniprot_virtual_lab/
├── run.py                             # 入口（CLI + 演示 + API）
├── requirements.txt                   # Python 依赖
├── README.md                          # 英文文档
├── README_CN.md                       # 中文文档（本文件）
├── config/
│   └── settings.yaml                  # ⭐ 用户配置文件
├── data/outputs/                      # 工具输出（FASTA、PDB 等）
├── meetings/                          # 会议记录（JSON + Markdown）
├── logs/                              # 结构化运行日志
│   └── run_20260101_120000/
│       ├── run.log                    # 文本日志
│       ├── run.jsonl                  # JSONL 事件流
│       ├── api_calls.json             # API 调用追踪
│       └── tools.json                 # 工具执行追踪
├── figures/                           # 架构图
├── scripts/
│   └── draw_architecture.py           # 图表生成脚本
└── src/miniprot_virtual_lab/
    ├── __init__.py                    # 包导出
    ├── agent.py          (~70 行)     # 智能体数据类
    ├── config.py         (~380 行)    # 提供商 + YAML + 单智能体配置解析
    ├── constants.py      (~170 行)    # 工具分类、工作流模板
    ├── logging_config.py (~200 行)    # 结构化日志（RunLogger）
    ├── prompts.py        (~350 行)    # 智能体角色、会议模板
    ├── run_meeting.py    (~390 行)    # 核心会议编排
    ├── tools.py          (~250 行)    # ToolBridge → MiniProt
    └── utils.py          (~170 行)    # Token 计数、I/O、费用
```

---

## 生成架构图

```bash
pip install matplotlib
python scripts/draw_architecture.py --output-dir ./figures
```

生成：`architecture.png`、`meeting_flow.png`、`agent_roles.png`

---

## 与原始项目的对比

| 维度 | MiniProt (enzyme_update) | Virtual Lab (zou-group) | **MiniProt Virtual Lab** |
|------|--------------------------|------------------------|--------------------------|
| **交互方式** | 单用户 ↔ Agent 对话 | 人类 PI + 多人会议 | 人类 PI + 多人会议 |
| **智能体模型** | Planner→Executor→Summarizer | 角色提示词 | 角色提示词 + 工具访问 |
| **工具使用** | 40+ 工具，LLM 规划调用 | 仅 PubMed 搜索 | 40+ 通过 MiniProt ToolManager |
| **配置方式** | 分散的环境变量 | 分散的环境变量 | **单一 YAML 配置文件** |
| **API 配置** | 单一全局 Key | 单一全局 Key | **单智能体独立 Key/模型** |
| **日志系统** | stdlib logging | Print 语句 | **结构化 JSONL + 追踪文件** |
| **AI 提供商** | 仅 DeepSeek（LangChain） | 仅 OpenAI | **多提供商预设** |
| **质量保证** | 工具重试、重新规划 | 科学审稿人 + 合并 | 科学审稿人 + 工具重试 |
| **记忆/状态** | LangGraph MemorySaver | 会议摘要 | 会议摘要 + 工具产物 |
| **代码规模** | ~4500 行 graph.py | ~270 行 run_meeting.py | ~390 行 run_meeting.py |

---

## 许可证

MIT License.

## 引用

如使用 MiniProt Virtual Lab，请引用：

- Swanson, K., Wu, W., Bulaong, N.L. et al. *The Virtual Lab of AI agents designs new SARS-CoV-2 nanobodies.* Nature (2025). https://doi.org/10.1038/s41586-025-09442-9
- MiniProt — AI Agent for Protein & Enzyme Mining. SJTU Software 2026.
