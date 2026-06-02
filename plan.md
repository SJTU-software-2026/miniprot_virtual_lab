# Tool Implementation Plan / 工具实现计划

> 最后更新：2025-06-02（已测试更新）
> 状态说明：✅ 已完成/可用 | 🔧 需要手动安装 | ✋ 需要手写代码 | ❌ 不可用

---

## 〇、2025-06-02 测试结果

### 已通过测试的 API/Python 工具（6 个）
| 工具 | 耗时 | 结果 |
|------|------|------|
| uniprot_search | 1452ms | ✅ 返回 insulin 蛋白 |
| ncbi_search | 1731ms | ✅ NCBI 搜索正常 |
| alphafold | 2875ms | ✅ 下载 P01308 结构 |
| pdb | 825ms | ✅ 获取 1ADA |
| smiles | 921ms | ✅ 获取 aspirin |
| protein_properties | 29ms | ✅ 计算序列属性 |

### 已克隆/下载的源码
| 工具 | 状态 | 位置 |
|------|------|------|
| OmegaFold | 已克隆 | `tools_src/omegafold/` |
| P2Rank | 已下载 | `tools_src/p2rank/p2rank_2.5.1/` |

### 关键发现
- **OmegaFold** 严格限制 Python 3.8-3.10，依赖 PyTorch 1.12.0 CUDA 11.3。我们的 Docker (Python 3.12) 无法直接安装。
- **P2Rank** 解压即用，需要 Java 17+。
- **6 个 API 工具全部可用**，无需任何本地安装。

---

## 一、已在 Docker 中通过 conda/pip 完成的工具（无需额外操作）

| 工具 | 安装方式 | 验证命令 |
|------|---------|---------|
| MAFFT | `conda install -c bioconda mafft` | `mafft --version` |
| MMseqs2 | `conda install -c bioconda mmseqs2` | `mmseqs version` |
| CD-HIT | `conda install -c bioconda cd-hit` | `cd-hit -h` |
| Foldseek | `conda install -c bioconda foldseek` | `foldseek -h` |
| TM-align | `conda install -c bioconda tmalign` | `TMalign` |
| AutoDock Vina | `conda install -c conda-forge vina` | `vina --version` |
| Open Babel | `conda install -c conda-forge openbabel` | `obabel -V` |
| Meeko | `conda install -c conda-forge meeko` | `mk_prepare.py -h` |
| FastTree | `conda install -c bioconda fasttree` | `fasttree -h` |
| PyMOL | `conda install pymol-open-source` | `pymol -c -q` |
| PDBFixer | `pip install pdbfixer` | |
| ETE Toolkit | `pip install ete3` | |
| ESMFold | `pip install transformers torch` | |
| P2Rank | Dockerfile wget + unzip | `prank -h` |

---

## 二、需要手动下载/安装的工具

### 2.1 P2Rank

| 项目 | 详情 |
|------|------|
| **地址** | https://github.com/rdk/p2rank/releases |
| **安装** | 下载 `p2rank_2.5.1.tar.gz` → 解压 → 设置 `P2RANK_HOME` |
| **依赖** | Java 17+ |
| **Docker 状态** | ✅ 已在 Dockerfile 中处理 |
| **手动安装** | `wget https://github.com/rdk/p2rank/releases/download/2.5.1/p2rank_2.5.1.tar.gz` |

### 2.2 SCWRL4

| 项目 | 详情 |
|------|------|
| **地址** | https://dunbrack.fccc.edu/lab/scwrl |
| **安装** | 填写学术许可表单 → 下载安装包 → 运行安装脚本 |
| **许可** | 学术免费，商业需联系 |
| **平台** | Linux 64-bit: `install_scwrl4.0.2_64bit_2020_linux` |
| **Docker 状态** | ❌ 未包含（需手动接受许可） |

---

## 三、需要手写代码/特殊处理的工具

### 3.1 🔧 OmegaFold — Python 版本冲突

| 项目 | 详情 |
|------|------|
| **地址** | https://github.com/HeliXonProtein/OmegaFold |
| **问题** | OmegaFold **要求 Python 3.10**，我们的 Docker 使用 **Python 3.12** |
| **安装** | `pip install git+https://github.com/HeliXonProtein/OmegaFold.git` |
| **模型** | 首次运行自动下载权重至 `~/.cache/omegafold_ckpt/model.pt`（~200MB） |

**手写内容：**
- [ ] 方案 A：Docker 中创建 Python 3.10 conda 子环境，在子环境中安装 OmegaFold，通过 subprocess 调用
- [ ] 方案 B：Docker multi-stage build，OmegaFold 作为独立阶段
- [ ] 方案 C：等待 OmegaFold 升级支持 Python 3.12
- [ ] 需要修改 `omegafold_tool.py` 的 subprocess 调用，适配 conda 子环境路径

**负责人：** ________   **截止：** ________

### 3.2 🔧 enzyme_specificity_predict — 需要模型文件

| 项目 | 详情 |
|------|------|
| **代码位置** | `enzyme_update/src/tools/enzyme_specificity_predict_tool.py` |
| **问题** | 缺少训练好的 `.ckpt` 模型文件 + YAML 配置文件 |
| **需要文件** | `checkpoint_path` (`.ckpt`) + `config_path` (`.yml`) + LMDB 数据路径 |
| **模型来源** | SJTU 内部训练的 enzyme_prediction structure_sequence Lightning 模型 |

**手写内容：**
- [ ] 确认模型文件位置（可能在 `enzyme_prediction/` 仓库中）
- [ ] 编写模型下载脚本或提供网盘链接
- [ ] 创建默认 config 模板，允许用户仅覆盖 test CSV 路径
- [ ] 文档：说明如何获取/放置模型文件

**负责人：** ________   **截止：** ________

### 3.3 🔧 enzymecage_retrieve — 依赖独立环境

| 项目 | 详情 |
|------|------|
| **代码位置** | `enzyme_update/src/tools/enzymecage_retrieve_tool.py` |
| **问题** | 需要独立的 `enzymecage` conda 环境，公开搜索无结果（可能为 SJTU 内部项目） |
| **依赖** | `ENZYMECAGE_PYTHON` 环境变量指向独立的 Python 解释器 |
| **子依赖** | P2Rank (已安装) + 未知 ML checkpoint |

**手写内容：**
- [ ] 确认 EnzymeCAGE 是否为公开可用的工具
- [ ] 如果是内部工具：编写 setup 脚本，自动创建 conda env
- [ ] 如果是公开工具：补充 GitHub 链接和安装说明
- [ ] 在 Dockerfile 中添加可选构建阶段

**负责人：** ________   **截止：** ________

### 3.4 🔧 miniprot_rag — 需要预构建索引

| 项目 | 详情 |
|------|------|
| **代码位置** | `enzyme_update/src/tools/miniprot_rag_tool.py` + `src/miniprot_rag/` |
| **问题** | ChromaDB 向量库需要预构建索引才能查询 |
| **构建方式** | `python scripts/build_miniprot_rag_index.py` |
| **数据源** | `docs/rag_corpus/` 下的 Markdown 文件 |
| **依赖** | `chromadb`, `sentence-transformers`（已在 Docker 中安装） |

**手写内容：**
- [ ] 编写 `scripts/build_rag_index.sh` 一键构建脚本
- [ ] 在 Docker 构建时或首次运行时自动构建索引
- [ ] 提供预构建索引的下载链接（可选）

**负责人：** ________   **截止：** ________

### 3.5 🔧 enzyme_redesign — 需要 SCWRL4 + 学术许可

| 项目 | 详情 |
|------|------|
| **代码位置** | `enzyme_update/src/workflows/enzyme_redesign/` |
| **问题** | 流水线依赖 SCWRL4 二进制文件，但 SCWRL4 需要手动接受许可 |
| **SCWRL4 地址** | https://dunbrack.fccc.edu/lab/scwrl |
| **许可** | 学术免费，需填写表单 |

**手写内容：**
- [ ] 编写 `scripts/install_scwrl4.sh`：引导用户下载 + 安装
- [ ] Docker 中：添加注释说明如何挂载 SCWRL4 二进制
- [ ] 文档：清晰说明许可流程

**负责人：** ________   **截止：** ________

---

## 四、不需要手写、但需要用户自行安装的工具

这些工具的 Python 封装代码**已经完成**（在 enzyme_update 中），只是需要用户自行安装底层二进制/获取许可：

| 工具 | 安装方式 | Docker 状态 |
|------|---------|------------|
| MAFFT | `conda install -c bioconda mafft` | ✅ |
| MMseqs2 | `conda install -c bioconda mmseqs2` | ✅ |
| CD-HIT | `conda install -c bioconda cd-hit` | ✅ |
| Foldseek | `conda install -c bioconda foldseek` | ✅ |
| TM-align | `conda install -c bioconda tmalign` | ✅ |
| AutoDock Vina | `conda install -c conda-forge vina` | ✅ |
| Open Babel | `conda install -c conda-forge openbabel` | ✅ |
| PyMOL | `conda install pymol-open-source` | ✅ |
| ETE/FastTree | `pip install ete3` + `conda install fasttree` | ✅ |
| P2Rank | wget from GitHub releases | ✅ |
| SCWRL4 | 学术许可 → 下载 | ❌ (许可限制) |

---

## 五、还需要手写的辅助脚本总结

| 脚本 | 用途 | 优先级 |
|------|------|--------|
| `scripts/setup_omegafold_env.sh` | 创建 Python 3.10 conda 子环境，安装 OmegaFold | **高** |
| `scripts/install_scwrl4.sh` | 引导用户下载 + 安装 SCWRL4 | 中 |
| `scripts/build_rag_index.sh` | 一键构建 ChromaDB RAG 索引 | 中 |
| `scripts/download_enzyme_model.sh` | 下载 enzyme_specificity_predict 的 .ckpt 模型 | **高**（如果模型可用） |
| `scripts/setup_enzymecage.sh` | 创建 EnzymeCAGE 独立 conda 环境 | 低（待确认工具可用性） |

---

## 六、已知的 Python 版本兼容性问题

| 工具 | 需要版本 | 当前版本 | 解决方案 |
|------|---------|---------|---------|
| OmegaFold | Python 3.10 | Python 3.12 | 创建 conda 子环境 `conda create -n omegafold python=3.10` |
| PyMOL | 3.10-3.12 | Python 3.12 | conda 版本兼容 |
| ESMFold | 3.10-3.12 | Python 3.12 | 正常 |

---

## 七、给团队成员的说明

1. **大部分工具已通过 Docker 解决** — 25+ 工具通过 conda/pip 一键安装
2. **OmegaFold 是最紧迫的问题** — 需要 Python 3.10 子环境或等待上游更新
3. **enzyme_specificity_predict 需要模型文件** — 请联系训练该模型的同学获取 `.ckpt`
4. **enzymecage_retrieve 来源不明** — 请确认是否为 SJTU 内部项目，如是请提供仓库地址
5. **SCWRL4 许可** — 任何成员都可以免费申请学术许可，流程 2 分钟
