# doc2md — 资料转换处理器

把各种格式的原始资料（txt / md / docx / pptx / xlsx / pdf / png / jpeg …）**自动、确定性地**转换成一套「对大模型 agent 友好、可按需索引」的资料库：一个全局 `catalog.yaml` 索引 + 对应的 Markdown 正文库 + 原始文件兜底。

处理器**自身不依赖任何大模型**，纯确定性 Python 流程，调用现成的解析库与 OCR 工具完成。它面向资料归整的**第一阶段**：做确定性的格式统一、文字提取与去噪；图片语义理解、复杂版面精修等留给多模态大模型在第二阶段接手。处理器在产物里用 `<!-- stage2: ... -->` 标记把第二阶段该接手的位置显式留出，原文件始终保留兜底——**stage 1 的工作总是有益的，而对后续处理是无害的**。

---

## 一、背景与定位

资料散落在多种格式：Office/PDF 是结构化或半结构化的，图片是纯视觉的，md/txt 是文本的。直接喂给大模型有三个问题：① 格式杂、噪声多（页眉页脚、母版装饰、空行）；② 无法精准定位（agent 不知道哪份资料讲什么）；③ 图片/扫描件没有文字层。

`doc2md` 解决的是「**把异构资料统一成 agent 可消费的资料系统**」这件事——

- **统一格式**：全部输出为 Markdown，带 YAML front-matter 元数据。
- **统一索引**：全局 `catalog.yaml` 列出每份资料的 id / 标题 / 类目 / 来源 / 摘要，agent 据此定位再读正文；章节结构在 md 正文里（标题锚点 + 长 md 顶部渲染目录）。
- **去噪 + 提取**：每种格式用对应工具解析，去样板噪声，提取正文、表格、图片中的文字（OCR）。
- **确定性与可复现**：同一输入产出同一结果；doc-id 由源文件相对路径哈希生成，跨平台稳定不漂移。
- **并行加速**：多文件解析自动并行（ProcessPool），OCR 多图并行（ThreadPool），并行度按硬件自动嗅探（CPU 核数 + 可用内存），输出顺序按文件名排序不受进程影响，确定性不变。
- **不预设领域**：类目体系完全由配置定义，换一份 `config.yaml` 即可从「安全审计」切到「法规」「产品手册」等任何领域，代码不动。
- **不依赖大模型**：分类、摘要、标题选取全用规则与抽取式，OCR 用本地引擎。

> **两阶段分工**：第一阶段（本处理器）做确定性的格式归整与文字提取；第二阶段（多模态大模型，另做）对图片做语义理解、对复杂结构做精修。stage 1 只做确定性能做好的事，做不好的诚实标记并保留原件，让 stage 2 知情接手。

---

## 二、处理流水线

输入一个目录，递归扫描每个文件，走统一流水线：

```
扫描 → 识别 → [并行解析] → [并行OCR] → 去噪与分类 → 落盘(md+原件) → 入目录 → 增量清理
```

1. **识别**：按文件**内容签名**判定格式，不靠扩展名（扩展名改错也能认对）。OOXML（docx/pptx/xlsx）都是 zip，靠 zip 内部结构区分；PDF/图片看魔数；文本类嗅探编码后按内容特征分 md/code/txt；旧二进制 office（.doc/.xls/.ppt）识别为 ole2。
2. **解析**（ProcessPool 并行）：每种格式走对应解析器（见下表），产出 Markdown 正文。解析器只提取文字/表格/图片，不做 OCR——图片存盘后插入 `<!-- ocr:type:dest -->` 占位，OCR 留到收尾阶段执行。解析器不加载 OCR 引擎，worker 进程轻量（避免多进程内存爆炸）。
3. **OCR**（ThreadPool 并行）：收尾阶段在 parent 进程执行，单实例 OCR 引擎，多线程并行处理图片（ONNX Runtime 线程安全）。WMF/EMF 格式自动跳过（Linux 无解码器），defer 给 stage2。
4. **去噪与分类**：折叠空行、去明显页码行、去行尾空白；分类由配置驱动；摘要取首个正文段（跳过标题/图片/表格/列表）。
5. **落盘**：写 front-matter + 正文 md 到 `docs/<类目>/`；原件复制（或 `--move` 移动）到 `original-doc/`；提取的内嵌图存 `original-doc/<doc-id>_assets/`。
6. **入目录**：更新 `catalog.yaml` + `handoff.yaml`（每次从全量 manifest 重建）。
7. **增量清理**：哈希未变则跳过；源文件删除则清理其 md/原件/条目；格式变为不支持则转 defer（见下）。

**为什么解析用 ProcessPool、OCR 用 ThreadPool？** 解析阶段 fitz（PDF 表格检测）在 C 层有全局状态，线程不安全，必须进程隔离。OCR 阶段 rapidocr 底层 ONNX Runtime 的 `run()` 线程安全，用线程即可——共享同一个模型实例，零额外内存。两阶段的并行度均按硬件自动嗅探，无需人工配置。

### 各格式能力

| 格式 | 能力 |
|---|---|
| md | pass-through；标题取首个 H1；内嵌本地图片引用跟进提取+OCR |
| txt / 代码 | 编码嗅探（utf-8/gbk/gb18030）；代码裹带语言标注围栏 |
| docx | 段落/表格/有序列表(编号+层级)/粗斜体；内嵌图(段落+表格单元格)提取+OCR；公式/图表/SmartArt 检测标记 |
| pptx | 递归展开分组图形；逐 slide 标题/正文/表格/备注；内嵌图提取+OCR；非文本图形标记 |
| xlsx | 每 sheet→md 表格；合并单元格展开；无缓存公式回退显示公式文本 |
| pdf | 大纲注入章节标题；文字块按坐标排序；表格抽取(过滤伪表, 失败时降级为仅文字层)；扫描页渲染+OCR；内嵌图标记 |
| png / jpeg … | OCR 文本块按序拼进 md；无文字/无引擎则 defer |

### OCR

用 `rapidocr-onnxruntime`（PP-OCR 模型转 ONNX，中英文）。OCR 在收尾阶段 parent 进程执行（不在解析 worker 中），单实例引擎 + ThreadPool 多线程并行，避免多进程内存爆炸。WMF/EMF 格式自动跳过（Linux 无 Pillow 解码器），defer 给 stage2。运行时检测：有引擎就 OCR，无引擎或初始化失败则缓存失败状态不再重试，图片 defer 给 stage 2。OCR 是可选增强——不装处理器照常跑，只是图片类内容标 defer。

### 分类、摘要、章节索引（确定性，不调大模型）

- **分类**：`mapping`（文件名 glob 精确）优先 → `keywords`（文件名+正文命中数最多者胜出）→ 默认类目。无配置时全落 `unclassified`。
- **摘要**：抽取式——首个像正文的段，截断 200 字。
- **章节索引**：正文标题 ≥6 个时在 md 顶部渲染 `## 目录`（链接锚点，前 30），agent 打开文档即可定位到章节级。catalog/manifest 不重复存储章节结构——它已在 md 正文里。

---

## 三、输出

```
<输出目录>/
├── index/
│   ├── catalog.yaml          # 导航索引（业务 agent 定位文档；每文档含 deferred 上下文）
│   ├── handoff.yaml          # 交接说明 + stage2_pending 工作单（仅 stage2 agent 读）
│   └── .manifest.yaml        # 增量记录（内部）
├── docs/                     # 正文 Markdown（对 agent 理解/操作友好）
│   └── <类目>/<doc-id>.md    # 每个 md 头部带 YAML front-matter
└── original-doc/             # 原始资料，按需兜底查阅
    ├── <原文件相对路径>       # 源文件原样（默认复制；--move 则移动）
    └── <doc-id>_assets/      # 从 docx/pptx/pdf 提取的内嵌图
```

两个索引文件**各自标明面向对象与需求边界，零重叠**：
- **catalog.yaml**（面向：业务 agent）— 纯导航索引。按类目/标题/摘要定位文档。每文档的 `deferred` 字段为上下文（"这篇有未处理项"），不是工作单。章节结构不重复存储——打开 md 文件即见标题 + 目录渲染。
- **handoff.yaml**（面向：仅 stage2 agent）— 交接说明 + 工作项。stage2 agent 读此一处即获取全部：producer 身份、stage1 做了/没做什么、defer 类型处理契约、输出指南、目录布局、`stage2_pending` 具体工作项。业务 agent 不需要读此文件。

### front-matter（每个 md 头部）

```yaml
---
id: doc-a1b2c3d4
title: NPU漏洞利用
category: attack-surface
source: original-doc/npu_exploit.pptx
source_type: pptx
tags: []
summary: NPU漏洞利用
deferred:                    # stage1 无法确定处理的内容 (仅有待处理项时出现)
  - type: chart              # 类型枚举, 可编程统一处理
    count: 2                 # 数量
    note: 图表/SmartArt等未提取
---
```

### catalog.yaml

```yaml
version: 1
generated_at: 2026-09-30T17:39:47+08:00
categories:                 # 按类目聚合的 doc-id 列表
  attack-surface: [doc-1665f3ca, doc-a3348d73]
documents:                  # 全量文档明细
  - id: doc-a3348d73
    title: NPU漏洞利用
    category: attack-surface
    source: original-doc/npu_exploit.pptx
    source_type: pptx
    doc: docs/attack-surface/doc-a3348d73.md
    summary: NPU漏洞利用
    tags: []
    deferred:                  # 每文档上下文 (仅有待处理项时出现; 聚合工作单在 handoff.yaml)
      - {type: chart, count: 2, note: 图表/SmartArt等未提取}
```

### handoff.yaml

```yaml
producer:                      # stage1 身份
  name: doc2md
  stage: 1
stage1_done: [...]             # stage1 已完成 (7项, stage2 不需重复)
stage1_not_done: [...]         # stage1 未做 (3项, 需 stage2 接手)
deferred_contract:             # 每个 defer 类型的处理契约 (where/source/action)
  image-ocr:
    where: md 中标记紧邻上方有 ![](path) 图片引用
    source: 跟随 ![](path) 读取图片
    action: 多模态理解, 提取语义描述或文字
  ...                          # (9 种 type, 每种含 where + source + action)
procedure:                     # stage2 依次执行的 6 步
  1: 从 stage2_pending 取工作项
  2: 按 type 查 deferred_contract (where/source/action)
  3: 打开 md, 正则找该 type 所有标记 (可能多个, 逐个处理)
  4: 每个标记: 按 where 定位原件, 执行 action, 替换标记
  5: 全部替换后四处同步: md body + front-matter + catalog + handoff
  6: 重复直到 stage2_pending 为空
output_guide:                  # 四处更新目标详解
  md_body / front_matter / catalog / handoff / originals / marker_format
layout: {catalog: ..., docs: ..., originals: ...}
stage2_pending:                # 聚合工作单 (仅 stage2 agent 需要; 无待处理项时为空列表)
  - id: doc-a3348d73
    title: NPU漏洞利用
    doc: docs/attack-surface/doc-a3348d73.md
    source: original-doc/npu_exploit.pptx
    deferred:
      - {type: chart, count: 2, note: 图表/SmartArt等未提取}
```

**agent 用法**：
- **业务 agent**：读 `catalog.yaml` 按类目/标题/摘要定位到某份 `doc`，再读对应 md 正文（正文标题即章节锚点，可深链 `docs/<cat>/<id>.md#<锚点>` 到具体章节）；需要原图/原表时回 `original-doc/`。
- **stage2 agent**：读 `handoff.yaml` 一处即获取全部——背景、契约、`stage2_pending` 工作项、输出指南。按工作项逐个处理，**四处同步更新**（md body 替换标记、front-matter deferred、catalog deferred、handoff stage2_pending），保持全链一致。

`doc-id` = `doc-` + sha256(源文件相对路径)[:8]，稳定可复现。

---

## 四、关键设计原则

- **确定性优先**：识别/解析/分类/摘要/标题全用规则与抽取式，不调大模型，结果可复现。
- **对 stage 2 无害**：解析器只读不改原件；每个被处理文件**先复制/移动到 `original-doc/` 兜底**（即使解析失败也先保原件再写占位）；输出与输入目录分离。
- **诚实标记**：难以精准处理的内容用标准化 `<!-- stage2:type=count note -->` 标记 + 结构化 `deferred` 字段，不静默丢弃，让 stage 2 知情接手。
- **通用不预设领域**：类目/映射/关键词全在配置，代码不写死任何领域或文件名。
- **内容识别优于扩展名**：格式按内容签名判定，扩展名改错也能认对。

### deferred 数据模型（stage1 → stage2 交接协议）

stage1 无法确定处理的内容，用**结构化 `deferred` 字段** + **标准化正文标记** + **handoff 工作单**三重表达，保证可编程统一处理：

- **每文档 `deferred` 字段**（front-matter / catalog）：`[{type, count, note}]`，上下文——"这篇文档有哪些未处理项"。
- **正文标记**（md body 内原地）：`<!-- stage2:type=count note -->`，可用正则 `<!-- stage2:([\w-]+)=(\d+)(?:\s+(.*?))? -->` 统一提取。
- **handoff.yaml `stage2_pending`**（聚合工作单）：列出所有有待处理项的文档，stage2 agent 读 handoff 一处即知全貌。

| type | 含义 |
|---|---|
| `image-ocr` | 图片无文字/无引擎，需多模态理解 |
| `inline-image` | PDF 文字页内嵌图，图内文字未提取 |
| `scan-page` | 扫描页渲染/OCR 失败 |
| `formula` | 数学公式(OMML)未提取 |
| `chart` | 图表/SmartArt 未提取 |
| `formula-cell` | xlsx 公式单元格无缓存值（已回退显示公式文本，需复核） |
| `encrypted` | 加密文档 |
| `unsupported` | stage1 不支持的格式 |
| `parse-failed` | 解析失败 |

同一文档内多个同类型项经 `aggregate_deferred` 合并（count 求和）。无待处理项的文档不输出 `deferred` 字段，不产生噪声。

---

## 五、用法

### 零参数模式（推荐）

```bash
pip install -e .          # 一次性安装, 注册 doc2md 命令
doc2md                    # 就这一行
```

敲下 `doc2md`，自动完成：
- **定位输入目录**：扫描 CWD 子目录，选含文档最多的（排除 `knowledge/` 等输出/构建目录）
- **指定输出目录**：`./knowledge/`，不存在则创建
- **查找配置**：CWD 有 `config.yaml` 就用，没有则全落 `unclassified`
- **增量处理**：默认复制模式，反复 `doc2md` 只处理新增/变更文件

可混用——只指定需要的参数，其余自动：
```bash
doc2md                    # 全自动
doc2md -i mydocs          # 只指定输入, 输出/config 自动
doc2md --move -v           # 只指定选项, 输入/输出/config 自动
```

### 显式参数

```bash
doc2md -i <输入目录> -o <输出目录> [-c config.yaml] [--move] [-v]
```

| 参数 | 说明 |
|---|---|
| `-i / --input` | 输入目录（不指定则自动检测） |
| `-o / --output` | 输出目录（不指定则 `./knowledge/`） |
| `-c / --config` | 配置文件 yaml（不指定则自动查找 `config.yaml`） |
| `--move` | 移动原文件而非复制（一次性摄取，清空输入；不可增量重跑） |
| `-v / --verbose` | 调试日志 |

```bash
# 带分类配置
doc2md -i input-examples -o knowledge -c config.example.yaml

# 不传配置：全部落 unclassified
doc2md -i input-examples -o knowledge

# 增量重跑：未变跳过
doc2md -i input-examples -o knowledge -c config.example.yaml
# → 完成: 处理 0, 跳过 N(全部已处理), 失败 0, 清理 0, 不支持 0, defer 0
```

也可用 `python3 doc2md.py ...` 调用，参数相同。

### 增量与清理

重跑时：哈希不变跳过；内容变则重处理（分类变则删旧类目 md）；源文件删除则清理其 md/原件/条目；格式变为不支持则转 defer（保留条目+标记，不丢）。catalog 每次从全量 manifest 重建。

### 鲁棒性

路径校验、config 容错回退默认、写盘/解析失败不崩整轮、损坏 PDF 降级处理、OCR 引擎失败缓存不重试、中断保存已处理部分、大文件慢速提示、空文件/二进制/旧 office 友好处理。

---

## 六、依赖与跨平台

```bash
pip install -r requirements.txt
```

跨平台（Linux / Windows / macOS，Python 3.9+），全部为预编译 wheel，无需编译器：

| 库 | 用途 |
|---|---|
| python-docx / python-pptx / openpyxl | Office 解析 |
| PyMuPDF (fitz) | PDF 文字层/大纲/渲染/表格抽取 |
| Pillow | 图像 |
| PyYAML | config/manifest/catalog/front-matter |
| rapidocr-onnxruntime | OCR（可选增强；不装则图片 defer） |

无 tesseract / pandoc / libreoffice / torch / paddle——都不需要。

**Windows**：用 `python`（非 `python3`）；CLI 自动重配 stdout/stderr 为 utf-8；输出统一 LF 行尾；doc-id 跨平台稳定。

---

## 七、配置（config.yaml，可选）

类目、映射、关键词全在配置，**代码不写死任何类目**。不提供配置则只有默认类目 `unclassified`。换领域只改这份配置。

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

## 八、项目结构

```
ai-doc2md/
├── doc2md.py              # CLI 入口 (python3 doc2md.py)
├── pyproject.toml         # 包定义 + doc2md 命令注册 (pip install -e .)
├── config.example.yaml    # 分类配置示例
├── requirements.txt       # 依赖
├── doc2md/                # 处理器包
│   ├── detector.py        # 格式识别
│   ├── ocr.py             # OCR 封装
│   ├── denoise.py         # 去噪 + summary + toc + 章节索引 + front-matter
│   ├── config.py          # 配置加载 + 通用分类
│   ├── manifest.py        # 增量 manifest
│   ├── catalog.py         # catalog.yaml + handoff.yaml 生成
│   ├── pipeline.py        # 主流程编排 (三阶段: 串行预处理→ProcessPool并行解析→串行收尾[ThreadPool OCR+去噪+分类])
│   ├── cli.py             # CLI + 跨平台适配
│   ├── util.py            # 工具函数
│   └── parsers/           # 各格式解析器（md/text/image/xlsx/docx/pptx/pdf）
│       └── _common.py     # md 表格 / 占位符检测 / stage2 defer 结构化标记
├── tests/                 # 回归测试套（stdlib，无额外依赖）
└── input-examples/        # 示例资料
```

---

## 九、测试

测试套位于 `tests/`，**不依赖 pytest 或任何额外库**，所有测试输入由代码现场生成，零外部 fixture 依赖、hermetic 自清理。

```bash
python tests/run_all.py          # 退出码 0=全过，1=有失败
python tests/run_all.py -v        # 详细（含失败堆栈）
```

覆盖：格式识别（含扩展名改错诱饵+多编码）、各格式解析输出正确性（docx 表格图/有序列表/公式标记/图表标记、pptx 分组递归、pdf 大纲/扫描页/表格/降级、xlsx 公式/合并展开/截断、md 内嵌图/远程图片跳过/无H1 fallback、image OCR 正向/纯图无文字defer）、OCR 引擎失败缓存/WMF-EMF格式跳过、去噪（toc/summary 围栏感知/clean_text/strip_boilerplate/空summary/表格行不取summary）、config 容错、分类优先级（mapping>keywords>default）、路径校验、空/二进制/旧 office、大文件、损坏 pdf/docx/xlsx/pptx/图片、中断保存、增量全链路（跳过/新增/重处理/删除清理/同时增删改/3次以上/删除恢复doc-id/配置变更重分类/格式变 defer/重分类剪枝/旧 assets 清理）、deferred 结构化（front-matter/catalog 每文档上下文/标记正则/聚合/多type同文档/标记位置/无defer不噪声/stage2_pending 在 handoff 不在 catalog/handoff 完整性/面向对象注释）、零参数模式（自动定位输入/输出/config/增量重跑/config在输入目录/嵌套子目录/move后空/输出自动创建/排除输出目录/多候选选最多/混用参数/无文档报错）、--move、多格式混合端到端、原件保留验证、catalog 精简无 toc/sections + md 目录渲染 + front-matter 合法性、并行处理（两次运行确定性/10文件顺序不受进程影响/混合格式正确）、doc-id 稳定性。

---

## 十、限制 / 留给第二阶段

stage 1 原则：**确定性工作总是有益、对后续无害**。原文件/提取图一律保留在 `original-doc/` 兜底；难精准处理的内容用结构化 `deferred` 字段 + 标准化正文标记（见 [deferred 数据模型](#deferred-数据模型stage1--stage2-交接协议)）显式标记，让多模态大模型（第二阶段）知情接手。

**已显式标记、留 stage2 的：**
- 纯架构图/扫描图无文字（OCR 无结果）→ 看图语义理解交多模态
- 旧二进制 office 及其它非空不支持格式 → 复制原件 + 进 catalog + defer 标记（不静默丢弃）
- docx 公式/图表/SmartArt、pptx 非文本图形 → 检测到即标记未提取
- xlsx 无缓存值公式单元格 → 回退显示公式文本 + 复核标记
- 解析失败/加密 → 占位 md 指向原件

**已知精度限制（不丢字、只影响结构/顺序，原件可回看）：**
- PDF 复杂多栏版面：文字都在，但多栏阅读序可能错乱（stage2 可重排）
- docx 修订/批注/footnotes/页眉页脚：未提取
- 极少数混合编码文本可能需 stage2 复核

这些不影响正文文字可用性，agent 仍可据 catalog 定位、读 md 获取主要内容，必要时回 `original-doc/` 兜底。

---

## 十一、快速验证

```bash
pip install -e .                       # 注册 doc2md 命令
doc2md                                 # 零参数: 自动定位 input-examples, 输出到 ./knowledge/
cat knowledge/index/catalog.yaml       # 看导航索引
cat knowledge/index/handoff.yaml       # 看 stage2 交接说明
python tests/run_all.py                # 跑回归测试
```
