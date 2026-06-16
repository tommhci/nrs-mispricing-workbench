# NRS-1 v3 — 系统规格文档
## Narrative-Reality Mispricing Workbench · Technical Specification

> **文档类型:** 设计与开发规格  
> **版本:** 3.0-DRAFT  
> **状态:** 待审核  
> **受众:** 后端工程师 · 前端设计师 · Tom（产品负责人）  
> **范围:** v2（RSS + Anthropic）→ v3（分层信息源 + GLM + 声明验证引擎）

---

## 目录

1. [项目定位：v3 是什么](#1-项目定位)
2. [v2 问题诊断](#2-v2-问题诊断)
3. [v3 架构总览](#3-v3-架构总览)
4. [GLM 集成规格](#4-glm-集成规格)
5. [信息源分层架构](#5-信息源分层架构)
6. [数据模型变更](#6-数据模型变更)
7. [Pipeline 各阶段规格](#7-pipeline-各阶段规格)
8. [Dashboard 变更规格](#8-dashboard-变更规格)
9. [实现清单 · 工作量估算](#9-实现清单)
10. [开放决策（需 Tom 确认）](#10-开放决策)
11. [附录：环境变量 · 依赖清单](#11-附录)

---

## 1. 项目定位

### v2 定位（当前）
> "检测 AI/半导体市场叙事与工程现实之间的错误定价。"



### v3 定位（重构后）
> "自动验证 AI 与半导体领域的可查证工程声明，量化声明信心与工程证据之间的差距。"

**关键变化：**
- 市场价格信号 → **降级为可选输入**，不再是框架中心
- 声明来源 → **从 RSS 标题扩展为分层文档**（SEC 文件、财报电话会议、学术论文、专家分析）
- LLM 提供商 → **从 Anthropic 迁移到 GLM**（Zhipu AI，Tom 有免费 token）
- 系统使命 → 与 Epoch AI **互补而非竞争**：Epoch 追踪能力发展，NRS-1 追踪*关于能力的声明是否可信*

### 为什么不做"综合 AI 发展追踪器"
Epoch AI 已建立涵盖 3500+ ML 模型的公开数据库，服务政策制定者和研究者。通用 AI 发展追踪赛道已有强力玩家占据，资源差距是数量级的。

NRS-1 的可辩护差异化在于**声明验证层**，这是 Epoch AI 没有做的事。

---

## 2. v2 问题诊断

### 问题 1：信息质量（严重）

| 现状 | 问题 |
|------|------|
| 从 RSS 抓取标题（CNBC、MarketWatch、Yahoo Finance） | 标题是叙事摘要，不含工程数据 |
| LLM 从 "NVDA announces AI chip" 估算可行性分数 | 这是猜测，不是评估 |
| R_score 基于 LLM 对标题的推断 | 整套 Gap Index 可信度不足 |

**根本原因：** 标题字符串（≤150 字符）无法支撑工程可行性评估。真正需要的是：SEC 8-K 原文、财报电话会议记录、技术白皮书。

### 问题 2：LLM 提供商（运营）

| v2 | v3 |
|----|----|
| Anthropic API / Groq | GLM (Zhipu AI) |
| 有成本/配额限制 | Tom 有免费 token |
| `anthropic` SDK | `openai` SDK（GLM 兼容） |

**迁移成本：** 6 行代码。不是重写，是替换。

### 问题 3：代码耦合（中等）

v2 是单一 ~1000 行文件，RSS 抓取 + LLM 调用 + Gap Index 计算交织在一起。v3 在同一文件内用清晰的模块分区重组，便于独立测试和调试。

### 问题 4：来源可信度（中等）

v2 中 CNBC 标题和 SemiAnalysis 深度分析被同等对待，都作为 RSS 条目进入同一列表。v3 引入 `source_tier` 属性直接调节 `evidence_strength` 上限。

---

## 3. v3 架构总览

```
┌─────────────────────────────────────────────────────────┐
│                   NRS-1 v3 PIPELINE                     │
└─────────────────────────────────────────────────────────┘

MODULE 0: SOURCE INTELLIGENCE
──────────────────────────────────────────────────────────
 Tier 1 (quality=1.0)    Tier 2 (quality=0.75)   Tier 3 (quality=0.4)
 ┌────────────────┐      ┌─────────────────┐      ┌──────────────┐
 │ • SEC EDGAR 8K │      │ • SemiAnalysis  │      │ • Reuters    │
 │ • 财报电话会议  │      │ • FabricatedKn. │      │ • Yahoo Fin. │
 │ • arXiv 预印本 │      │ • Epoch AI Blog │      │ • CNBC       │
 │ • 公司 IR 文件 │      │ • IEEE Spectrum │      │ (v2 现有源)  │
 └───────┬────────┘      └────────┬────────┘      └──────┬───────┘
         └────────────────────────┴───────────────────────┘
                                  │ SourceRouter (Tier1→2→3)
                                  ▼
                         SourceDocument (含 tier + quality)

MODULE 1: LLM AGENTS (GLM glm-4.5)
──────────────────────────────────────────────────────────
                         SourceDocument
                                  │
                    ┌─────────────▼────────────┐
                    │      NarrativeAgent       │
                    │  INPUT: 文档全文(≤4000字符)│
                    │  OUTPUT: NarrativeObject  │
                    │  新增: verbatim_quote      │
                    └─────────────┬────────────┘
                                  │
                    ┌─────────────▼────────────┐
                    │       RealityAgent        │
                    │  INPUT: 声明 + 来源层级   │
                    │  OUTPUT: RealityObject    │
                    │  新增: evidence_ceiling    │
                    └─────────────┬────────────┘

MODULE 2: 确定性评分（与 v2 完全一致）
──────────────────────────────────────────────────────────
                    ┌─────────────▼────────────┐
                    │      MarketAgent          │
                    │  yfinance 价格数据        │
                    └─────────────┬────────────┘
                                  │
                    ┌─────────────▼────────────┐
                    │    Gap Index Engine       │
                    │  GapIndex = α×NR_gap      │
                    │           + β×MR_gap      │
                    │  5 门禁验证               │
                    └─────────────┬────────────┘

MODULE 3: 输出
──────────────────────────────────────────────────────────
              ┌──────────┬──────────┬──────────┐
         report.md  history.jsonl  audit.jsonl  →  Dashboard
```

### v2 → v3 变更对照表

| 组件 | v2 | v3 |
|------|----|----|
| LLM 提供商 | Anthropic / Groq | GLM (Zhipu AI) |
| 信息源 | RSS 标题 | 分层文档获取器 |
| 来源质量追踪 | 无 | `source_tier` + `quality_score` |
| NarrativeAgent 输入 | 标题字符串（≤150 字符） | 文档全文摘录（≤4000 token） |
| 证据强度上限 | LLM 自由估计 | 由 `source_tier` 强制上限 |
| 代码结构 | 单文件耦合 | 单文件分模块 |

**不变的内容：**
- Gap Index 公式：`GapIndex = α × |N_score - R_score| + β × |M_implied - R_score|`
- 5 门禁验证逻辑
- 审计追踪格式（append-only JSONL）
- Dashboard URL 和数据格式（向后兼容）
- GitHub Actions 自动化（仅改 secret 名称）



---

## 4. GLM 集成规格

### 4.1 API 基本信息（已验证）

| 参数 | 值 |
|------|----|
| Base URL | `https://open.bigmodel.cn/api/paas/v4/` |
| 协议 | OpenAI 兼容 `/chat/completions` |
| 认证 | Bearer token via `ZHIPU_API_KEY` 环境变量 |
| 免费额度 | 可用（Tom 已有 token） |

**推荐模型配置：**

| 模型 | 上下文 | 用途 |
|------|--------|------|
| `glm-4.5` | 128K | NarrativeAgent + RealityAgent（默认） |
| `glm-4.5-flash` | 128K | 若 token 预算紧张，替换 NarrativeAgent |

### 4.2 代码迁移（Drop-in 替换）

**v2（Anthropic）：**
```python
import anthropic
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
response = client.messages.create(
    model="claude-sonnet-4-20250514",
    max_tokens=512,
    system=system_prompt,
    messages=[{"role": "user", "content": user_content}],
)
raw = response.content[0].text.strip()
```

**v3（GLM — 直接替换）：**
```python
from openai import OpenAI   # pip install openai>=1.0.0

ZHIPU_API_KEY = os.environ.get("ZHIPU_API_KEY", "")
GLM_BASE_URL  = "https://open.bigmodel.cn/api/paas/v4/"
GLM_MODEL     = "glm-4.5"

client = OpenAI(api_key=ZHIPU_API_KEY, base_url=GLM_BASE_URL)
response = client.chat.completions.create(
    model=GLM_MODEL,
    max_tokens=512,
    temperature=0.0,
    messages=[
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": user_content},
    ],
)
raw = response.choices[0].message.content.strip()
```

**nrs1_v3.py 中实际改动行数：6 行。** 所有 prompt 逻辑、JSON 解析、重试逻辑完全保留。

### 4.3 GitHub Actions Secret 变更

```
# 删除（不再需要）
ANTHROPIC_API_KEY

# 新增
ZHIPU_API_KEY = <从 open.bigmodel.cn 获取>
```

操作路径：仓库 → Settings → Secrets and variables → Actions

### 4.4 故障行为

GLM API 失败处理与 v2 完全一致：`call_llm()` 在任何异常时返回 `None`，pipeline 自动回退到 `stub_narrative()` / `stub_reality()`。无需修改现有门禁逻辑。

---

## 5. 信息源分层架构

**这是 v3 最重要的变更。** 信息质量直接决定 Gap Index 的可信度。

### 5.1 三层定义

#### Tier 1 — 原始文档（`source_tier=1`, `quality_score=1.0`）

**定义：** 在法律或机构约束下的直接披露。数字由组织本身陈述，具有可追溯性。

| 来源 | 获取方式 | 价值 |
|------|----------|------|
| SEC EDGAR 8-K / 10-Q | EDGAR API（免费，无需 key） | CFO 在法律约束下的实际承诺 |
| 财报电话会议记录 | 公司 IR 页面 / Seeking Alpha | 管理层前瞻性声明 + 分析师追问 |
| 公司技术白皮书 | 直接 URL | 工程规格数据 |
| arXiv 预印本 | arXiv API（免费） | AI/硬件能力声明 + 基准测试 |
| NVIDIA GTC / AMD Tech Day 演讲 | YouTube 字幕 / 公司 IR | 产品路线图声明 |

**关键价值差异：** CFO 在财报电话会议说 "CoWoS 产能受限至 2026 年 Q3" 是可归因的主要声明。Reuters 报道同一电话会议的摘要不是。

#### Tier 2 — 专家分析（`source_tier=2`, `quality_score=0.75`）

**定义：** 有一手信息来源、技术深度和准确性记录的领域专家综合分析。

| 来源 | RSS 地址 | 专注领域 |
|------|----------|----------|
| SemiAnalysis (Dylan Patel) | `https://semianalysis.com/feed` | 半导体供应链、良率、CoWoS、HBM |
| Fabricated Knowledge (Doug O'Laughlin) | `https://www.fabricatedknowledge.com/feed` | 半导体周期、内存、WFE |
| Epoch AI Blog | `https://epoch.ai/blog/rss.xml` | AI 算力、基准测试、能力追踪 |
| IEEE Spectrum | `https://spectrum.ieee.org/rss` | 工程准确性、同行评审背景 |

**重要：** v2 仅存储标题；v3 必须抓取完整文章正文（≤4000 字符）供 NarrativeAgent 使用。

#### Tier 3 — 大众媒体（`source_tier=3`, `quality_score=0.4`）

**定义：** 金融和通用科技新闻。高覆盖度，工程细节低。当 Tier 1/2 无相关内容时使用，或用于检测叙事传播广度。

这是 v2 的全部 RSS 来源（CNBC、MarketWatch、Yahoo Finance）。保留但降级。

### 5.2 核心设计决策：证据强度上限强制机制

**这是 Gap Index 从"LLM 猜测"升级为"结构性约束"的关键。**

```python
def enforce_evidence_ceiling(evidence_strength: str, source_tier: int) -> str:
    """
    强制上限：Tier 3 来源永远不能产生 'strong' 证据。
    保护 R_score 不被媒体声明虚高。
    """
    ORDER   = ["strong", "moderate", "weak", "insufficient"]
    CEILING = {1: "strong", 2: "moderate", 3: "weak"}
    max_idx = ORDER.index(CEILING[source_tier])
    cur_idx = ORDER.index(evidence_strength) if evidence_strength in ORDER else 3
    # 取上限与 LLM 输出中较弱的那个
    return ORDER[max(max_idx, cur_idx)]
```

| `source_tier` | 证据强度上限 | 含义 |
|---------------|-------------|------|
| 1 (原始文档) | `strong` | LLM 自行判断，可以是任何值 |
| 2 (专家分析) | `moderate` | 最高只能是 moderate |
| 3 (大众媒体) | `weak` | 无论 LLM 怎么评，上限是 weak |

**为什么这很重要：** 一个 CNBC 标题说 "NVDA 芯片实现突破性效率"，无论 LLM 多么确信，这个来源的结构性约束是 weak。一个 TSMC 技术研讨会幻灯片配 TEM 图像则可以是 strong。这让 Gap Index 从主观评分变成有结构约束的评分。

### 5.3 SourceRouter 逻辑

```
get_best_source(tickers, topics):
  1. 尝试 Tier 1：EDGAR API → 所有 WATCH_TICKERS 的近期 8-K/10-Q
     如果有相关内容（relevance_score > 0）→ 返回最佳文档

  2. 如果 Tier 1 空：尝试 Tier 2 专家 RSS
     对每个匹配标题：抓取完整文章正文
     → 返回最相关文档

  3. 如果 Tier 2 空：尝试 Tier 3 现有 RSS（v2 逻辑，仅标题）

  4. 如果全部为空：返回 None → pipeline 使用 stub_narrative()
```

### 5.4 EDGAR API 使用

EDGAR 全文检索是免费的，无需 API key。

```python
# 获取公司最新文件列表（含直接链接）
GET https://data.sec.gov/submissions/CIK{cik}.json

# 关键字段
filings.recent.form        # 文件类型: "8-K", "10-Q", "6-K"
filings.recent.filingDate  # 提交日期
filings.recent.primaryDocument  # 文档文件名
```

**Watch list CIK 映射：**
```python
EDGAR_CIK = {
    "NVDA": "0001045810",  "AMD":  "0000002488",
    "TSM":  "0001046179",  "INTC": "0000050863",
    "AVGO": "0001730168",  "ASML": "0000937556",
    "MOD":  "0000067215",  "SMCI": "0001375365",
}
```

---

## 6. 数据模型变更

### 6.1 新增：SourceDocument

```python
@dataclass
class SourceDocument:
    title:         str
    content:       str        # 文档全文，最多 4000 字符（非标题）
    url:           str
    source_name:   str        # "SEC EDGAR", "SemiAnalysis", "Reuters"
    source_tier:   int        # 1, 2, 或 3
    quality_score: float      # 1.0 / 0.75 / 0.4
    doc_type:      str        # "8K", "10Q", "earnings_call", "research_note", "news"
    pub_date:      str        # ISO8601
    ticker_refs:   list       # 提及的 ticker: ["NVDA", "TSM"]
```

### 6.2 NarrativeObject（新增字段）

```python
@dataclass
class NarrativeObject:
    # 保留字段（不变）
    claim:              str
    source_url:         str
    sentiment_polarity: float
    propagation:        float
    novelty:            str
    certainty:          str

    # v3 新增
    source_tier:        int    # 来自 SourceDocument
    source_name:        str    # "SEC EDGAR", "SemiAnalysis", 等
    doc_type:           str    # 文档类型
    verbatim_quote:     str    # 来源中包含声明的原始句子（强制 LLM 锚定文本，不推断）
```

**`verbatim_quote` 的意义：** 强制 LLM 在源文本中找到具体句子，而不是对标题进行推断。如果 LLM 无法找到具体引用，则声明质量存疑。

### 6.3 RealityObject（新增字段）

```python
@dataclass
class RealityObject:
    # 保留字段（不变）
    technical_change:    str
    feasibility_score:   float
    constraint_penalty:  float
    evidence_strength:   str    # 现在由 source_tier 强制上限
    open_constraints:    list
    hardware_constraint: str
    supply_chain_risk:   str

    # v3 新增
    evidence_ceiling:    str    # source_tier 允许的最大值（用于 Dashboard 警告）
    primary_constraint:  str    # 最关键的单一约束（用于 Dashboard 显示）
    comparable_events:   list   # 历史类比事件: ["NVDA Blackwell 延迟 2024", ...]
```

### 6.4 history.jsonl 新增字段

`write_history()` 需要追加：
```python
"source_tier":   document.source_tier,
"source_name":   document.source_name,
"doc_type":      document.doc_type,
"verbatim":      narrative.verbatim_quote[:100],
```

向后兼容：旧 v2 记录缺少这些字段，Dashboard 需要用 `.get()` 并提供默认值。

---

## 7. Pipeline 各阶段规格

### Stage 0（新增）：信息源获取

```
输入: WATCH_TICKERS, WATCH_TOPICS
输出: SourceDocument
失败行为: 返回 None → pipeline 使用 stub（与 v2 一致）
```

详见第 5.3 节 SourceRouter 逻辑。

### Stage 1：叙事提取（修改）

**输入变化：** `SourceDocument`（含全文）而非 `{"title": str}`

**GLM Prompt 变化（新增 source 上下文）：**

```
系统提示中新增：
- Source tier: {source_tier} (1=原始文件, 2=专家分析, 3=大众媒体)
- Document type: {doc_type}
- Source name: {source_name}

输出 JSON 新增字段：
"verbatim_quote": "<来源中包含声明的原始句子>"
```

### Stage 2：现实评估（修改）

**输入变化：** 新增 `verbatim_quote` + `source_tier`

**LLM 返回后，强制执行证据上限：**
```python
reality.evidence_strength = enforce_evidence_ceiling(
    reality.evidence_strength, source.source_tier
)
reality.evidence_ceiling = CEILING[source.source_tier]
```

**GLM Prompt 新增字段：**
```json
"primary_constraint": "<最关键的单一约束>",
"comparable_events":  ["<历史类比>", ...]
```

### Stages 3–7：完全不变

市场数据（yfinance）、Gap Index 计算、5 门禁验证、报告写入、历史追加、邮件发送——全部从 v2 保留，无需修改。

---

## 8. Dashboard 变更规格（面向设计师）

### 8.1 版本标识

侧边栏底部：

```python
# 修改
"VERSION": "NRS-1 v3"
```

### 8.2 分析日志表格新增列

在 `render_table_html()` 的列头中新增：

| 新列 | 显示名 | 宽度 | 样式 |
|------|--------|------|------|
| `source_tier` | `Tier` | 40px | T1=绿色, T2=琥珀色, T3=灰色 |
| `source_name` | `Source` | 80px | 截断到 10 字符 |

**Tier 颜色规范：**
```css
T1 (原始文件):  color: var(--green)   /* #16a34a */
T2 (专家分析):  color: var(--amber)   /* #f59e0b */
T3 (大众媒体):  color: var(--text3)   /* #52525b */
```

### 8.3 证据上限警告

在 "Latest Reading" 区域，如果 `evidence_ceiling` 生效（即 LLM 的原始评估被降级），显示警告：

```python
if latest.get("evidence_ceiling") and latest.get("evidence_ceiling") != latest.get("evidence"):
    st.markdown(
        '<p style="font-family:DM Mono,monospace;font-size:0.7rem;color:#ea580c;">'
        f'⚠ 证据上限已应用：Tier {source_tier} 来源将证据上限至 "{evidence_ceiling}"'
        '</p>',
        unsafe_allow_html=True
    )
```

### 8.4 最新声明卡片：来源标识

在 `.claim-card` 中 `.claim-meta` 行新增来源信息：

```html
<!-- 现有 -->
<div class="claim-meta">{ts} · evidence: {evidence} · {mode}</div>

<!-- v3 新增 source tier badge -->
<div class="claim-meta">{ts} · evidence: {evidence} · {mode}</div>
<div class="claim-tier" style="font-family:DM Mono,monospace;font-size:0.65rem;
     color:{tier_color};margin-top:0.3rem;">
  {source_name} · TIER {source_tier} · {doc_type}
</div>
```

---

## 9. 实现清单

### 后端工程师任务

| # | 任务 | 文件 | 预估工时 |
|---|------|------|----------|
| B1 | GLM client 替换（6 行改动） | `nrs1_v3.py` | 0.5h |
| B2 | `SourceDocument` dataclass | `nrs1_v3.py` | 0.5h |
| B3 | EDGAR API 抓取器（Tier 1） | `nrs1_v3.py` | 3h |
| B4 | Tier 2 RSS + 全文抓取器 | `nrs1_v3.py` | 2h |
| B5 | Tier 3 保留（现有 RSS 逻辑移植） | `nrs1_v3.py` | 0.5h |
| B6 | `SourceRouter.get_best_source()` | `nrs1_v3.py` | 1h |
| B7 | `enforce_evidence_ceiling()` | `nrs1_v3.py` | 0.5h |
| B8 | 更新 Prompt（source 上下文 + verbatim_quote） | `nrs1_v3.py` | 1h |
| B9 | 更新 `NarrativeObject` + `RealityObject` | `nrs1_v3.py` | 0.5h |
| B10 | 更新 `write_history()` + `write_report()` | `nrs1_v3.py` | 0.5h |
| B11 | 更新 `requirements.txt` | `requirements.txt` | 0.1h |
| B12 | 更新 GitHub Actions workflow | `daily_pipeline.yml` | 0.2h |

**后端总估算：约 10.3 小时**

### 前端/设计师任务

| # | 任务 | 文件 | 预估工时 |
|---|------|------|----------|
| D1 | 侧边栏版本标识改为 NRS-1 v3 | `app.py` | 0.1h |
| D2 | 分析日志表格新增 Tier + Source 列 | `app.py` | 1h |
| D3 | 证据上限警告组件 | `app.py` | 0.5h |
| D4 | 声明卡片来源标识 badge | `app.py` | 0.5h |

**前端总估算：约 2.1 小时**

### 测试验收标准

| 测试项 | 方法 |
|--------|------|
| GLM API 返回有效 JSON | `python nrs1_v3.py --test-llm` |
| EDGAR API 每周返回 NVDA 至少 1 个 8-K | `python nrs1_v3.py --test-edgar NVDA` |
| Tier 3 证据上限：永远不产生 "strong" | `python nrs1_v3.py --test` |
| Gap Index 公式与 v2 一致 | 所有现有单元测试通过 |
| `verbatim_quote` 字段对所有 LLM 运行有值 | NarrativeObject schema 验证 |
| Dashboard 显示最新读数的来源 tier | 视觉检查 |

---

## 10. 开放决策

以下问题需要 Tom 在开发开始前确认：

| # | 决策 | 选项 A | 选项 B | 截止 |
|---|------|--------|--------|------|
| OD-1 | 主力 GLM 模型 | `glm-4.5`（质量优先） | `glm-4.5-flash`（速度/成本优先） | 开发前 |
| OD-2 | arXiv 集成 | 本期包含（Tier 1） | 延到下一迭代 | 开发前 |
| OD-3 | Tier 2 付费墙处理 | 跳过付费内容（仅标题） | 添加认证 token | Sprint 1 |
| OD-4 | `verbatim_quote` 幻觉检查 | 人工审核 | 自动子串匹配 | Sprint 2 |
| OD-5 | EDGAR 内容过滤阈值 | 最少 1 个关键词命中 | 最少 3 个关键词命中 | Sprint 1 |
| OD-6 | history.jsonl 向后兼容 | v3 字段用 `.get()` 默认值 | 迁移脚本重建历史文件 | 部署前 |

---

## 11. 附录

### 11.1 环境变量完整清单（v3）

```bash
# 必须
ZHIPU_API_KEY=<从 open.bigmodel.cn 获取>

# 邮件（与 v2 相同，不变）
GMAIL_SENDER=
GMAIL_APP_PASSWORD=
GMAIL_RECIPIENT=

# 已删除（v2 遗留）
# ANTHROPIC_API_KEY  ← 不再需要
# GROQ_API_KEY       ← 不再需要
```

### 11.2 依赖变更

```
# requirements.txt v3

# 新增
openai>=1.0.0          ← GLM 客户端（OpenAI 兼容）

# 移除
# anthropic>=0.40.0   ← 不再需要

# 保留（不变）
streamlit>=1.35.0
pandas>=2.0.0
plotly>=5.18.0
requests>=2.31.0
yfinance>=0.2.40
```

### 11.3 三个已知失败点

**F-1（高风险）：** EDGAR 8-K 返回与主题无关的内容。公司任命/治理类 8-K 是 Tier 1 但与工程声明无关。对策：EDGAR 内容在传给 NarrativeAgent 前做关键词过滤，零命中则拒绝。

**F-2（中风险）：** GLM 伪造 `verbatim_quote`。LLM 可能生成听起来合理但不在原文中的引用。对策：审计追踪记录 `verbatim_quote` 和 `source_url`，未来版本可做子串验证。

**F-3（中风险）：** Tier 2 来源 Cloudflare 拦截。SemiAnalysis 等可能阻止自动抓取。对策：全文抓取失败时将该文档降级为 Tier 3（仅标题），不声称 Tier 2 质量。

---

*NRS-1 v3 规格文档 · 非投资建议 · 所有分数实验性且未校准*  
*最后更新：2026 年 6 月*
