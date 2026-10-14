# doc2md — 确定性资料转换处理器

把 txt / md / docx / pptx / xlsx / pdf / png 等异构资料，**自动、确定性地**转换成一套对大模型 Agent 友好的知识库：全局索引 + Markdown 正文 + 原件兜底。

处理器**自身不依赖任何大模型**——纯规则驱动、纯本地执行。它做确定性能做好的事（格式统一、文字提取、OCR、去噪、分类、索引），做不好的诚实标记并保留原件，让多模态大模型在第二阶段知情接手。

> **核心信条**：stage 1 的工作总是有益的，对后续处理是无害的。

---

## 一、设计哲学

| 原则 | 内涵 |
|------|------|
| **确定性优先** | 识别、解析、分类、摘要、标题——全用规则与抽取式，不调大模型，同输入同输出 |
| **内容优于扩展名** | 格式按二进制签名 / zip 内部结构判定，扩展名改错也能认对 |
| **诚实标记** | 难以精准处理的内容不静默丢弃，用标准化标记 + 结构化字段显式留出 |
| **对 stage 2 无害** | 解析器只读不改原件；先复制到 `original-doc/` 兜底，再写正文 |
| **通用不预设领域** | 类目体系全在配置文件，代码不写死任何业务领域 |
| **零配置可用** | 敲 `doc2md` 一行命令，自动定位输入、输出、配置、并行度 |

---

## 二、两阶段分工

```
 ┌──────────────┐     标记 + 原件      ┌──────────────┐
 │   Stage 1    │ ──────────────────▶ │   Stage 2    │
 │  doc2md      │   catalog + handoff  │  多模态大模型  │
 │  确定性提取   │ ◀────────────────── │  语义理解/精修 │
 └──────────────┘     标记/工作单回传     └──────────────┘
```

- **Stage 1（本处理器）**：格式统一 → 文字提取 → OCR → 去噪 → 分类 → 索引。全部确定性，结果可复现。
- **Stage 2（另做）**：图片语义理解、公式渲染、复杂版面精修。按 handoff 契约逐项接手（见第六节）。

Stage 1 做不好的地方，用 `<!-- stage2:type=count note -->` 标记 + `deferred` 结构化字段三重表达，让 stage 2 可编程统一处理。

---

## 三、架构总览

### 处理流水线

```
输入目录
  │
  ▼
┌─────────────────────────────────────────────────┐
│  Phase 1  串行预处理                              │
│  递归扫描 → 内容签名识别 → SHA256 → 跳过/重分类    │
└───────────────────────┬─────────────────────────┘
                        │
          ┌─────────────┴──────────────┐
          ▼                            ▼
   ┌──────────────┐           ┌──────────────┐
   │  Batch 模式   │           │ Streaming 模式 │
   │  ≥4GB 可用内存 │           │  <4GB 可用内存  │
   │              │           │              │
   │ ProcessPool  │           │  逐文件:      │
   │ 并行解析全部   │           │  解析→OCR→    │
   │ →shutdown pool│           │  写出→gc      │
   │ →OCR+写出     │           │              │
   └──────┬───────┘           └──────┬───────┘
          │                            │
          └─────────────┬─────────────┘
                        ▼
┌─────────────────────────────────────────────────┐
│  Phase 4  清理 + 索引                              │
│  删除孤儿 → 保存 manifest → 生成 catalog + handoff  │
└─────────────────────────────────────────────────┘
                        │
                        ▼
                    输出目录
```

### 模块布局

```
doc2md/
├── cli.py          CLI 入口 + 零参数自动检测 + 跨平台适配
├── pipeline.py     主流程编排 (预处理→解析→OCR→写出→清理→索引)
├── detector.py     内容签名格式识别 (14 种)
├── parsers/        各格式解析器 (统一签名, 统一返回结构)
│   ├── _common.py    共享: md 表格 / defer 标记 / 聚合
│   ├── docx.py       Word: 段落/表格/列表/内嵌图/公式图表检测
│   ├── pptx.py       PowerPoint: 分组递归/slide/表格/备注/图片
│   ├── pdf.py        PDF: 大纲/文字块排序/表格/跨页样板剔除/扫描页渲染
│   ├── xlsx.py       Excel: 多 sheet/合并展开/公式回退
│   ├── image.py      图片: 存图+OCR占位 (与其他 parser 一致)
│   ├── md.py         Markdown: pass-through + 内嵌图跟进
│   └── text.py       纯文本/代码: 编码嗅探 + 语言标注围栏
├── ocr.py          OCR 封装: 单例引擎 + 失败缓存 + WMF/EMF 跳过
├── denoise.py      去噪 + 抽取式摘要 + 章节索引 + front-matter
├── config.py       配置加载 + 通用分类 (mapping>keywords>default)
├── catalog.py      catalog.yaml + handoff.yaml 生成
├── manifest.py     增量 manifest (SHA256 跳过/删除清理)
└── util.py         工具: sha256 / doc-id / slugify / save_asset / write_text(LF)
```

---

## 四、设计决策

### 4.1 内容签名识别，不靠扩展名

**决策**：按文件二进制签名判定格式。OOXML（docx/pptx/xlsx）都是 zip，靠 zip 内部结构区分（`word/document.xml` vs `xl/workbook.xml` vs `ppt/presentation.xml`）；PDF 看魔数 `%PDF`；图片看各自魔数；文本类先嗅探编码（utf-8/gbk/gb18030），再按内容特征分 md/code/txt。

**理由**：扩展名经常被改错或丢失。内容签名是确定性的——同一文件永远识别为同一格式，不受命名影响。

### 4.2 ProcessPool 解析，不用 ThreadPool

**决策**：解析阶段用 ProcessPoolExecutor，不用 ThreadPoolExecutor。

**理由**：PyMuPDF 的 `fitz.Page.find_tables()` 在 C 层有全局状态，线程不安全。多线程并发解析 PDF 会产生竞争条件。ProcessPool 隔离内存空间，彻底避免此问题。

**为何不担心 spawn 开销**（与 4.3 对照）：解析是**粗粒度**任务（每文件一次），进程只创建一轮且常驻到全部解析完，spawn 开销被摊薄；而 OCR 是**大量细粒度**任务，且 Linux 下才有独立引擎的好处，故 OCR 才按平台分流（见 4.3）。

### 4.3 OCR 平台自适应：Linux ProcessPool / Windows ThreadPool

**决策**：

| 条件 | 模式 | 理由 |
|------|------|------|
| Linux + batch + 图≥50 + 多核 + 内存够 | ProcessPool | fork 轻量，各进程独立 ONNX session 无锁 |
| 其余（含 Windows 全部） | ThreadPool | 共享单实例引擎，ONNX `run()` 释放 GIL 提供并行；Windows 上 spawn 开销过高，实测慢 3-5 倍 |

**理由**：Windows 上 `spawn` 创建新进程需重新导入所有模块 + 重新初始化 ONNX，开销远大于 fork。Linux 上 `fork` 几乎零成本，独立引擎消除 ONNX session 锁，真并行。

**ThreadPool 模式的关键优化——跨文档全局池**：batch 模式下不再逐文档新建
ThreadPool，而是 finalize 前用**一个全局 ThreadPool** 跑完所有文档的全部
图片（`_precompute_ocr`），结果缓存后各文档查表写回，消除逐文档池的并行度空洞
（实测见 `pipeline.py:_precompute_ocr` 注释）。

### 4.4 两层资源限制：系统硬上限 + 应用优化

**决策**：

| 层级 | 作用 | Windows | Linux |
|------|------|---------|-------|
| 系统级（硬上限） | 安全网，防 CPU 100% / OOM | `SetProcessAffinityMask` (60% 核) + Job Object（内存硬上限 + **CPU 占用率硬上限**） | `sched_setaffinity` (60% 核) + `RLIMIT_AS` (4GB+/进程) |
| 应用级（优化） | 读亲和性后实际核数，决定 spawn 几个进程 | `_get_affinity_count()` | `_get_affinity_count()` |

**理由**：系统级限制子进程自动继承，ONNX/OpenCV 内部线程也无法绕过——这是安全网。应用级读亲和性后的实际核数来决定并行度——避免进程数 > 核数导致的上下文切换。两层互补，缺一不可：只有系统级没有应用级 → 进程数仍可能过多；只有应用级没有系统级 → ONNX 内部线程不受控。

**为何亲和性不足、需再加 CPU 占用率上限**：亲和性只规定“能用哪几个核”（限核），这 N 个核仍会被跑满，故任务管理器里常见 **90%+**。真正的占用率限制靠 Job Object 的 `JobObjectCpuRateControlInformation`（InfoClass 15，Win8+，`ENABLE|HARD_CAP`），按**系统总 CPU 百分比**限整个进程树。两套同时生效：亲和性隔离核心、占用率封顶（Linux 侧无等价非 root API，但亲和性 + 小并发已足够）。

### 4.5 Parser 不加载 OCR 引擎，占位 + 收尾替换

**决策**：所有 parser（含 image.py）只提取文字/表格/图片，不做 OCR。图片 blob 存到 assets 目录，body 中插入 `<!-- ocr:img:dest -->` 占位符。OCR 在 parent 进程的 `_post_ocr()` 收尾阶段统一执行。

**理由**：解析在 ProcessPool worker 中执行。如果每个 worker 都加载 ONNX 引擎（~400MB），N 个 worker = N×400MB，内存爆炸。占位模式让 worker 轻量，OCR 集中在 parent 进程或独立的 OCR ProcessPool（可控制进程数）。

### 4.6 ONNX 推理线程数限制为 1（防超订）

**决策**：主路径在构造时直接传 `RapidOCR(intra_op_num_threads=1, inter_op_num_threads=1)`（rapidocr 会读该 config 写入 `sess_options`，最可靠）；另 monkey-patch `onnxruntime.InferenceSession` 兜底，防旧版 rapidocr 不支持 kwarg。默认值 1，可用环境变量 `DOC2MD_OCR_INTRA_OP` 覆盖。

**理由**：不限制时，每个 ONNX 推理默认起全部核心数个线程，与 ThreadPool 相乘
造成**线程超订**（12 线程 × 全核 16 = 192 线程抢 16 核），并行反退化为准串行。

**关键坑**：rapidocr 的 `OrtInferSession` 总是**自己传入 `sess_options`**，因此只在
「缺失时补默认」的 monkey-patch 会被绕过，`intra_op` 实际仍为 0（全核）。修复必须
**原地覆盖**调用方传入的 `sess_options`，或在构造 `RapidOCR` 时直接传
`intra_op_num_threads`（推荐）。`OMP_NUM_THREADS=1` 在 Windows 上无效，必须改 session 选项。

> 实测数据见 `ocr.py:_intra_op_value` 注释。

### 4.7 Batch vs Streaming，按内存自动选择

**决策**：≥4GB 可用内存 → batch 模式；<4GB → streaming 模式。

**理由**：batch 模式先解析完全部文件再 OCR，解析和 OCR 不重叠（CPU 不叠加），吞吐高。但需要足够内存暂存全部解析结果。streaming 模式逐文件处理，每个文件解析完立即 OCR+写出+`gc.collect()`，内存安全但吞吐低。阈值 4GB 是经验值：足够暂存中等规模文档集的解析结果。

### 4.8 确定性 doc-id

**决策**：`doc-id = "doc-" + sha256(源文件相对路径)[:8]`。

**理由**：相对路径经 `/` 归一（`\` vs `/` 已统一）后再哈希，跨平台同文件产出相同 doc-id，产物路径（md 文件名 / assets 目录名 / catalog `id`）稳定，重跑可原位覆盖、不产生重复文件。截取 8 位足够区分，短且可读。

> **与增量跳过无关**：跳过判断由 manifest 的 **relpath 作键 + sha256 比对**保证（`is_unchanged`，见 4.7 / 八节），不依赖 doc-id。doc-id 只用路径哈希、**不看内容**，故内容变而路径不变时 doc-id 不变，由 sha256 触发重处理。

### 4.9 Deferred 三重表达

**决策**：stage 1 做不好的内容，用三重结构化表达：

| 层次 | 位置 | 格式 | 面向 |
|------|------|------|------|
| 每文档上下文 | front-matter / catalog | `deferred: [{type, count, note}]` | 业务 agent（"这篇有什么未处理项"） |
| 正文原地标记 | md body | `<!-- stage2:type=count note -->` | stage2 agent（正则提取，逐个替换） |
| 聚合工作单 | handoff.yaml | `stage2_pending: [{id, doc, source, deferred}]` | stage2 agent（一处知全貌） |

**理由**：业务 agent 只需读 catalog 定位文档，不需要看工作单。stage2 agent 读 handoff 一处即获取全部——背景、契约、工作项、输出指南。三重表达保证可编程统一处理，不丢信息，不产生噪声（无待处理项的文档不输出 `deferred` 字段）。

### 4.10 零参数 CLI

**决策**：不传任何参数，`doc2md` 自动定位输入目录（CWD 子目录中文档最多的）、输出（`./knowledge/`）、配置（`config.yaml`）。

**理由**：降低使用门槛。用户把资料放进一个子目录，敲 `doc2md` 就完了。可混用——只指定需要的参数，其余自动补全。

---

## 五、输出结构

```
<输出目录>/
├── index/
│   ├── catalog.yaml          # 导航索引 (业务 agent)
│   ├── handoff.yaml          # 交接说明 + 工作单 (stage2 agent)
│   └── .manifest.yaml        # 增量记录 (内部)
├── docs/
│   └── <类目>/<doc-id>.md    # 正文 Markdown (带 front-matter)
└── original-doc/
    ├── <原文件相对路径>       # 源文件原样兜底
    └── <doc-id>_assets/      # 提取的内嵌图 (供 OCR / stage2)
```

### catalog.yaml — 导航索引

面向**业务 agent**。按类目/标题/摘要定位文档，可深链到章节锚点。每文档的 `deferred` 字段是上下文（"这篇有未处理项"），不是工作单。章节结构不重复存储——打开 md 即见标题 + 目录渲染。

### handoff.yaml — 交接说明 + 工作单

面向**仅 stage2 agent**。读此一处即获取全部：`producer` 身份、`stage1_done` / `stage1_not_done` 边界、10 种类型的 `deferred_contract`（where/source/action）、`procedure`（6 步流程）、`termination`（完成 / `blocked` / 幂等终止条件）、`loc_field`（定位字段说明）、`output_guide`（四处同步更新，见第六节）、`layout`（路径布局）、`stage2_pending` 工作单。

### front-matter

每个 md 头部带 YAML 元数据：`id` / `title` / `category` / `source` / `source_type` / `tags` / `summary` / `deferred`（仅有待处理项时出现）。

---

## 六、Deferred 交接协议

10 种 defer 类型，每种含 where（标记与原件定位）/ source（原件在哪）/ action（该做什么）：

| type | 含义 |
|------|------|
| `image-ocr` | 图片无文字/无引擎，需多模态理解 |
| `inline-image` | PDF 文字页内嵌图，图内文字未提取 |
| `scan-page` | 扫描页渲染/OCR 失败 |
| `formula` | 数学公式 (OMML) 未提取 |
| `chart` | 图表/SmartArt 未提取 |
| `formula-cell` | xlsx 公式单元格无缓存值（已回退显示公式文本，需复核） |
| `truncated` | xlsx sheet 行列超过上限（行 2000/列 100）已截断，需查原件补全 |
| `encrypted` | 加密文档 |
| `unsupported` | stage1 不支持的格式 |
| `parse-failed` | 解析失败 |

正文标记可用正则统一提取：`<!-- stage2:([\w-]+)=(\d+)(?:\s+(.*?))? -->`

同一文档内多个同类型项经 `aggregate_deferred` 合并（count 求和）；若各项带 `loc` 定位线索（PDF 页号 / xlsx sheet 名），合并为 `locs` 列表，**位置不丢**。无待处理项的文档不输出 `deferred` 字段，不产生噪声。

**契约与代码的一致性**：handoff 的 `deferred_contract` 与代码内的 `DEFER_TYPES` 由启动时校验 + 回归测试守护，不会出现“代码有类型、契约无说明”。

**处理后四处同步**：同一 defer 事实在制品中有 4 份副本，stage2 完成一项后需同步更新 4 处，漏一处制品即不一致：

1. **md body** — 标记替换为实际内容
2. **md front-matter 的 `deferred`** — 移除该项
3. **catalog.yaml 该文档的 `deferred`** — 移除该项
4. **handoff.yaml 的 `stage2_pending`** — 移除该工作项

---

## 七、各格式能力

| 格式 | 能力 |
|------|------|
| md | pass-through；标题取首个 H1；内嵌本地图片引用跟进提取+OCR |
| txt / 代码 | 编码嗅探（utf-8/gbk/gb18030）；代码裹带语言标注围栏 |
| docx | 段落/表格/有序列表(编号+层级)/粗斜体；内嵌图(段落+表格单元格)提取+OCR；文本框、脚注/尾注、批注提取；公式/图表/SmartArt 检测标记 |
| pptx | 递归展开分组图形；逐 slide 标题/正文/表格/备注；内嵌图提取+OCR；非文本图形标记 |
| xlsx | 每 sheet→md 表格；合并单元格展开；无缓存公式回退显示公式文本 |
| pdf | 大纲注入章节标题；文字块按坐标排序；表格抽取(过滤伪表, 失败时降级为仅文字层)；扫描页渲染+OCR；内嵌图标记 |
| png / jpeg / gif / webp / tiff / bmp | OCR 文本块按序拼进 md；无文字/无引擎则 defer |
| 其余（旧 office / zip / 二进制 / 空文件等） | 不解析，复制原件 + `unsupported` 标记交 stage2 |

**分类**：`mapping`（文件名 glob 精确）优先 → `keywords`（文件名+正文命中数最多者胜出）→ 默认类目。

**摘要**：抽取式——取首个像正文的段落（跳过标题/图片/注释/引用/表格行/纯列表，代码块内行不参与），截断 200 字。

**章节索引**：正文标题 ≥6 个时在 md 顶部渲染 `## 目录`（锚点链接，前 30 个）。

---

## 八、用法

### 零参数模式（推荐）

```bash
pip install -e .          # 一次性安装
doc2md                    # 就这一行
```

敲下 `doc2md`，自动完成：定位输入目录、指定输出 `./knowledge/`、查找 `config.yaml`、增量处理。

可混用——只指定需要的参数，其余自动：

```bash
doc2md                    # 全自动
doc2md -i mydocs          # 只指定输入
doc2md -i mydocs --move -v  # 只指定选项
```

> `--move` 会**清空输入目录**（原文件被移走），建议始终配合显式 `-i` 指定，不要依赖自动检测。

### 显式参数

```bash
doc2md -i <输入目录> -o <输出目录> [-c config.yaml] [--move] [-v]
```

| 参数 | 说明 |
|------|------|
| `-i / --input` | 输入目录（不指定则自动检测） |
| `-o / --output` | 输出目录（不指定则 `./knowledge/`） |
| `-c / --config` | 配置文件（不指定则自动查找 `config.yaml`：先当前目录，再输入目录） |
| `--move` | 移动原文件而非复制（一次性摄取，清空输入；不可增量重跑） |
| `-v / --verbose` | 调试日志 |

### 增量与清理

重跑时：哈希不变跳过；内容变则重处理（分类变则删旧类目 md）；源文件删除则清理其 md/原件/条目；格式变为不支持则转 defer（保留条目+标记，不丢）。catalog 每次从全量 manifest 重建。

### 鲁棒性

路径校验 · config 容错回退默认 · 写盘/解析/收尾失败不崩整轮（per-file except）· 损坏文件降级 defer · OCR 引擎失败缓存不重试 · 中断保存已处理部分 · 大文件慢速提示 · 空文件/二进制/旧 office 友好处理。

---

## 九、配置（config.yaml，可选）

类目、映射、关键词全在配置，**代码不写死任何类目**。换领域只改这份配置。

```yaml
taxonomy:                    # 类目体系（完全自定义）
  - architecture
  - business
  - threat-model
  - attack-surface
  - audit-playbook
default_category: unclassified

mapping:                      # 文件名 glob -> 类目（优先级最高）
  "xxx.pptx": attack-surface
  "xxx.docx": audit-playbook

keywords:                     # 关键词 -> 类目（文件名+正文命中最多者胜出）
  audit-playbook: [审计, 验收, 准出]
  attack-surface: [漏洞, exploit, 利用, 攻击面]
  architecture: [架构, 内核, 设计, 内存管理]
```

---

## 十、依赖与跨平台

```bash
pip install -r requirements.txt
```

**支持平台：Linux / Windows（Python 3.9+）**。依赖均为预编译 wheel，无需编译器：

| 库 | 用途 |
|------|------|
| python-docx / python-pptx / openpyxl | Office 解析 |
| PyMuPDF (fitz) | PDF 文字层/大纲/渲染/表格抽取 |
| PyYAML | config/manifest/catalog/front-matter |
| rapidocr-onnxruntime | OCR（可选增强；不装则图片 defer） |

**Windows**：命令行使用 `python`（多数 Windows 环境无 `python3` 别名）；CLI 自动重配 stdout/stderr 为 utf-8；输出统一 LF 行尾；doc-id 跨平台稳定。

---

## 十一、测试

测试套位于 `tests/`，**不依赖 pytest 或任何额外库**，所有测试输入由代码现场生成，零外部 fixture 依赖、hermetic 自清理。

```bash
python tests/run_all.py          # 退出码 0=全过，1=有失败
python tests/run_all.py -v        # 详细（含失败堆栈）
```

用例覆盖：格式识别、各格式解析正确性、OCR（引擎失败缓存/正向出字/ProcessPool/WMF-EMF 跳过）、去噪、分类优先级、路径校验、损坏文件、中断保存、增量全链路、deferred 结构化、零参数 CLI、batch 模式输出一致性、加密 PDF defer、PPTX 内容（标题/正文/表格/备注）、特殊文件名、多 sheet xlsx、GBK 编码、代码文件围栏、verbose 模式、收尾异常不中断、并行确定性、系统资源限制、产物质量回退（表格转义/占位标题/表格摘要/PDF 跨页样板剔除/PPTX 箭头不计 defer）。

---

## 十二、限制 / 留给第二阶段

stage 1 原则：**确定性工作总是有益、对后续无害**。

**已显式标记、留 stage2 的：**
- 纯架构图/扫描图无文字（OCR 无结果）→ 多模态理解
- 旧二进制 office 及其它不支持格式 → 复制原件 + defer 标记
- docx 公式/图表/SmartArt、pptx 非文本图形 → 检测到即标记
- xlsx 无缓存值公式单元格 → 回退显示公式文本 + 复核标记
- xlsx 超长/超宽 sheet（行 > 2000 或列 > 100）→ 截断 + `truncated` defer（结构化上报，交 stage2 查原件）
- 解析失败/加密 → 占位 md 指向原件

**已知精度限制（不丢字、只影响结构/顺序，原件可回看）：**
- PDF 复杂多栏版面：文字都在，多栏阅读序可能错乱
- 极少数混合编码文本可能需复核

---

## 十三、快速验证

```bash
pip install -e .                       # 注册 doc2md 命令
doc2md                                 # 零参数: 自动定位 input-examples, 输出到 ./knowledge/
cat knowledge/index/catalog.yaml       # 看导航索引
cat knowledge/index/handoff.yaml       # 看 stage2 交接说明
python tests/run_all.py                # 跑回归测试
```
