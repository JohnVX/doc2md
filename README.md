# doc2md — 确定性资料转换处理器

把 txt / md / docx / pptx / xlsx / pdf / png 等异构资料，**自动、确定性地**转换成一套对大模型 Agent 友好的知识库：全局索引 + Markdown 正文 + 原件兜底。

处理器**自身不依赖任何大模型**——纯规则驱动、纯本地执行。它做确定性能做好的事（格式统一、文字提取、去噪、OCR），做不好的诚实标记并保留原件，让多模态大模型在第二阶段知情接手。

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
 └──────────────┘    四处同步更新结果   └──────────────┘
```

- **Stage 1（本处理器）**：格式统一 → 文字提取 → OCR → 去噪 → 分类 → 索引。全部确定性，结果可复现。
- **Stage 2（另做）**：图片语义理解、公式渲染、复杂版面精修。按 handoff 契约逐项接手，四处同步更新。

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
│   ├── pdf.py        PDF: 大纲/文字块排序/表格/扫描页渲染
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

### 4.3 OCR 平台自适应：Linux ProcessPool / Windows ThreadPool

**决策**：

| 条件 | 模式 | 理由 |
|------|------|------|
| Linux + batch + 图≥50 + 多核 + 内存够 | ProcessPool | fork 轻量，各进程独立 ONNX session 无锁 |
| 其余（含 Windows 全部） | ThreadPool | 共享单实例引擎，ONNX `run()` 释放 GIL 提供并行 |
| Windows 始终 ThreadPool | — | spawn 开销过高，实测慢 3-5 倍 |

**理由**：Windows 上 `spawn` 创建新进程需重新导入所有模块 + 重新初始化 ONNX，开销远大于 fork。Linux 上 `fork` 几乎零成本，独立引擎消除 ONNX session 锁，真并行。

**ThreadPool 模式的关键优化——跨文档全局池**：batch 模式下不再逐文档新建
ThreadPool，而是 finalize 前用**一个全局 ThreadPool** 跑完所有文档的全部
图片（`_precompute_ocr`），结果缓存后各文档查表写回。消除了逐文档池在文档
边界处的并行度空洞（文档图数 < 线程数时尾部跑不满）。实测 Linux 2 核 155 图
150.3s → 135.1s（+10%），空洞随核数放大。

### 4.4 两层资源限制：系统硬上限 + 应用优化

**决策**：

| 层级 | 作用 | Windows | Linux |
|------|------|---------|-------|
| 系统级（硬上限） | 安全网，防 CPU 100% / OOM | `SetProcessAffinityMask` (60% 核) + Job Object (内存硬上限) | `sched_setaffinity` (60% 核) + `RLIMIT_AS` (4GB+/进程) |
| 应用级（优化） | 读亲和性后实际核数，决定 spawn 几个进程 | `_get_affinity_count()` | `_get_affinity_count()` |

**理由**：系统级限制子进程自动继承，ONNX/OpenCV 内部线程也无法绕过——这是安全网。应用级读亲和性后的实际核数来决定并行度——避免进程数 > 核数导致的上下文切换。两层互补，缺一不可：只有系统级没有应用级 → 进程数仍可能过多；只有应用级没有系统级 → ONNX 内部线程不受控。

### 4.5 Parser 不加载 OCR 引擎，占位 + 收尾替换

**决策**：所有 parser（含 image.py）只提取文字/表格/图片，不做 OCR。图片 blob 存到 assets 目录，body 中插入 `<!-- ocr:img:dest -->` 占位符。OCR 在 parent 进程的 `_post_ocr()` 收尾阶段统一执行。

**理由**：解析在 ProcessPool worker 中执行。如果每个 worker 都加载 ONNX 引擎（~400MB），N 个 worker = N×400MB，内存爆炸。占位模式让 worker 轻量，OCR 集中在 parent 进程或独立的 OCR ProcessPool（可控制进程数）。

### 4.6 ONNX 推理线程数限制为 1（防超订）

**决策**：构造 OCR 引擎时传 `RapidOCR(intra_op_num_threads=1, inter_op_num_threads=1)`；
并 monkey-patch `onnxruntime.InferenceSession` 兜底，强制覆盖调用方传入的
`sess_options`。默认值 1，可用环境变量 `DOC2MD_OCR_INTRA_OP` 覆盖。

**理由**：不限制时，每个 ONNX 推理会起 `intra_op_num_threads` 个线程，默认 = 全部核心。
ThreadPool 模式下 12 线程 × 全核 16 = 192 线程抢 16 核，**严重超订**，并行反而
退化为准串行。实测（Windows 16 核可用，156 真实图）：

| intra_op | 12 线程 OCR 耗时 |
|---|---|
| 全核（不限制） | ~240s（接近串行）|
| 1 | **99.6s** |

**关键坑**：rapidocr 的 `OrtInferSession` 总是**自己传入 `sess_options`**（其 config
`intra_op_num_threads=-1` → 不设置该值 → ORT 默认全核）。因此 monkey-patch 必须
**原地覆盖**调用方传入的 `sess_options`，而不只是补默认值——否则被绕过，`intra_op`
实际仍为 0（全核）。更可靠的方式是构造 `RapidOCR` 时直接传 `intra_op_num_threads`
（rapidocr 的 `UpdateParameters` 会读该 config 并写入自己的 `sess_options`），
monkey-patch 仅作双保险。`OMP_NUM_THREADS=1` 在 Windows 上无效，必须改 session 选项。

**权衡**：`intra_op=4` 在部分测量中略快（真实图 155 张 78.9s→60.0s），但该差异在
测量噪声范围内，且 12×4=48 线程在 16 核上是 3x 超订，故默认保守取 1，环境变量可调优。

### 4.7 Batch vs Streaming，按内存自动选择

**决策**：≥4GB 可用内存 → batch 模式；<4GB → streaming 模式。

**理由**：batch 模式先解析完全部文件再 OCR，解析和 OCR 不重叠（CPU 不叠加），吞吐高。但需要足够内存暂存全部解析结果。streaming 模式逐文件处理，每个文件解析完立即 OCR+写出+`gc.collect()`，内存安全但吞吐低。阈值 4GB 是经验值：足够暂存中等规模文档集的解析结果。

### 4.8 确定性 doc-id

**决策**：`doc-id = "doc-" + sha256(源文件相对路径)[:8]`。

**理由**：用相对路径而非绝对路径——跨平台稳定（Windows `\` vs Linux `/` 已统一）。用路径而非内容哈希——同路径重跑 doc-id 不变，增量跳过可靠。截取 8 位足够区分，短且可读。

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

面向**仅 stage2 agent**。读此一处即获取全部：producer 身份、stage1 做了/没做什么、9 种 defer 类型的处理契约（where/source/action）、6 步操作流程、`stage2_pending` 具体工作项、四处同步更新指南。

### front-matter

每个 md 头部带 YAML 元数据：`id` / `title` / `category` / `source` / `source_type` / `tags` / `summary` / `deferred`（仅有待处理项时出现）。

---

## 六、Deferred 交接协议

9 种 defer 类型，每种含 where（标记与原件定位）/ source（原件在哪）/ action（该做什么）：

| type | 含义 |
|------|------|
| `image-ocr` | 图片无文字/无引擎，需多模态理解 |
| `inline-image` | PDF 文字页内嵌图，图内文字未提取 |
| `scan-page` | 扫描页渲染/OCR 失败 |
| `formula` | 数学公式 (OMML) 未提取 |
| `chart` | 图表/SmartArt 未提取 |
| `formula-cell` | xlsx 公式单元格无缓存值（已回退显示公式文本，需复核） |
| `encrypted` | 加密文档 |
| `unsupported` | stage1 不支持的格式 |
| `parse-failed` | 解析失败 |

正文标记可用正则统一提取：`<!-- stage2:([\w-]+)=(\d+)(?:\s+(.*?))? -->`

同一文档内多个同类型项经 `aggregate_deferred` 合并（count 求和）。无待处理项的文档不输出 `deferred` 字段，不产生噪声。

---

## 七、各格式能力

| 格式 | 能力 |
|------|------|
| md | pass-through；标题取首个 H1；内嵌本地图片引用跟进提取+OCR |
| txt / 代码 | 编码嗅探（utf-8/gbk/gb18030）；代码裹带语言标注围栏 |
| docx | 段落/表格/有序列表(编号+层级)/粗斜体；内嵌图(段落+表格单元格)提取+OCR；公式/图表/SmartArt 检测标记 |
| pptx | 递归展开分组图形；逐 slide 标题/正文/表格/备注；内嵌图提取+OCR；非文本图形标记 |
| xlsx | 每 sheet→md 表格；合并单元格展开；无缓存公式回退显示公式文本 |
| pdf | 大纲注入章节标题；文字块按坐标排序；表格抽取(过滤伪表, 失败时降级为仅文字层)；扫描页渲染+OCR；内嵌图标记 |
| png / jpeg / gif / webp / tiff / bmp | OCR 文本块按序拼进 md；无文字/无引擎则 defer |

**分类**：`mapping`（文件名 glob 精确）优先 → `keywords`（文件名+正文命中数最多者胜出）→ 默认类目。

**摘要**：抽取式——首个像正文的段，截断 200 字。

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
doc2md --move -v           # 只指定选项
```

### 显式参数

```bash
doc2md -i <输入目录> -o <输出目录> [-c config.yaml] [--move] [-v]
```

| 参数 | 说明 |
|------|------|
| `-i / --input` | 输入目录（不指定则自动检测） |
| `-o / --output` | 输出目录（不指定则 `./knowledge/`） |
| `-c / --config` | 配置文件（不指定则自动查找 `config.yaml`） |
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
  "npu_exploit.pptx": attack-surface
  "鸿蒙内核审计系统.docx": audit-playbook

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

跨平台（Linux / Windows / macOS，Python 3.9+），全部为预编译 wheel，无需编译器：

| 库 | 用途 |
|------|------|
| python-docx / python-pptx / openpyxl | Office 解析 |
| PyMuPDF (fitz) | PDF 文字层/大纲/渲染/表格抽取 |
| Pillow | 图像 |
| PyYAML | config/manifest/catalog/front-matter |
| psutil | 资源嗅探（可选；不装时 Windows 用 ctypes 兜底，Linux 用 `/proc/meminfo`） |
| rapidocr-onnxruntime | OCR（可选增强；不装则图片 defer） |

无 tesseract / pandoc / libreoffice / torch / paddle——都不需要。

**Windows**：用 `python`（非 `python3`）；CLI 自动重配 stdout/stderr 为 utf-8；输出统一 LF 行尾；doc-id 跨平台稳定。

---

## 十一、测试

测试套位于 `tests/`，**不依赖 pytest 或任何额外库**，所有测试输入由代码现场生成，零外部 fixture 依赖、hermetic 自清理。

```bash
python tests/run_all.py          # 退出码 0=全过，1=有失败
python tests/run_all.py -v        # 详细（含失败堆栈）
```

133 个用例，覆盖：格式识别、各格式解析正确性、OCR（引擎失败缓存/正向出字/ProcessPool/WMF-EMF 跳过）、去噪、分类优先级、路径校验、损坏文件、中断保存、增量全链路、deferred 结构化、零参数 CLI、batch 模式输出一致性、加密 PDF defer、PPTX 内容（标题/正文/表格/备注）、特殊文件名、多 sheet xlsx、GBK 编码、代码文件围栏、verbose 模式、收尾异常不中断、并行确定性、系统资源限制。

---

## 十二、限制 / 留给第二阶段

stage 1 原则：**确定性工作总是有益、对后续无害**。

**已显式标记、留 stage2 的：**
- 纯架构图/扫描图无文字（OCR 无结果）→ 多模态理解
- 旧二进制 office 及其它不支持格式 → 复制原件 + defer 标记
- docx 公式/图表/SmartArt、pptx 非文本图形 → 检测到即标记
- xlsx 无缓存值公式单元格 → 回退显示公式文本 + 复核标记
- 解析失败/加密 → 占位 md 指向原件

**已知精度限制（不丢字、只影响结构/顺序，原件可回看）：**
- PDF 复杂多栏版面：文字都在，多栏阅读序可能错乱
- docx 修订/批注/footnotes/页眉页脚：未提取
- 极少数混合编码文本可能需复核

---

## 十三、快速验证

```bash
pip install -e .                       # 注册 doc2md 命令
doc2md                                 # 零参数: 自动定位 input-examples, 输出到 ./knowledge/
cat knowledge/index/catalog.yaml       # 看导航索引
cat knowledge/index/handoff.yaml       # 看 stage2 交接说明
python tests/run_all.py                # 跑回归测试 (133/133)
```
