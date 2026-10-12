# for-pi-agent.md — 交接给后续 agent

## 项目概述

`doc2md` 是一个确定性资料转换处理器，把 txt/md/docx/pptx/xlsx/pdf/png 等异构资料自动、确定性地转换成对大模型 Agent 友好的知识库（全局 catalog.yaml 索引 + Markdown 正文 + 原件兜底）。处理器自身不依赖任何大模型，纯规则驱动。

- **仓库**: `github.com:JohnVX/doc2md.git`，git config `JohnVX <wswbpf@163.com>`，用户自行 push
- **工作目录**: `/home/claude/ai-doc2md`
- **当前 HEAD**: `3a8b504`（已 push）
- **测试**: 130/130 通过，0 失败，0 跳过

## 环境

- **Linux**: 华为云 EulerOS, Python 3.9, 2核/2.3GB RAM。onnxruntime 1.19.2
- **Windows**: Intel i7-14700, 28核/64GB(50.2GB可用), Python 3.10
- **编译器**: Linux 有 gcc，Windows 可用 MSVC 或 MinGW

## 已完成的工作

### 代码审查两轮 + 修复（全部完成）

1. `45b89f1` — CLI 首行增加 code hash (SHA256 前8位)
2. `9689446` — 补全测试覆盖（129/129）
3. `13c50bf` — 第一轮审查修复 6 项（image.py 占位模式、finalize 循环 except Exception、测试 save/restore 全局变量、detector.py context manager、README 格式表补充）
4. `7c1b7d5` — 第二轮审查修复 2 项（detector.py ZipFile context manager、新增 test_finalize_crash）
5. `3a8b504` — README 重构：高屋建瓴架构说明 + 设计决策独立章节（10 项决策含理由）+ 排版优化

### 当前代码状态

- 130 个测试全部通过
- README 与实现完全一致
- 无已知缺陷
- 代码审查两轮全部完成

---

## 当前任务：用 C 扩展实现 GIL-free OCR

### 背景：为什么要做

双平台实测数据（同一批 13 文件 / 156 张图）：

| | Linux (2核/2.3GB) | Windows (28核/50.2GB) |
|---|---|---|
| OCR 模式 | 流式, ThreadPool 1 线程 | 批量, ThreadPool 12 线程 |
| 156 图 OCR 耗时 | ~277s | 221.8s |
| 每图计算量 | 1.78s | 1.78s（相同） |
| 并行加速比 | 1x | **仅 1.25x** |

**根因**：ONNX `run()` 释放 GIL（占每图 ~20% 时间），但 Pillow 加载/numpy 预处理/文本解码等 Python 代码持 GIL（占 ~80%），12 线程串行排队。

**解决方案（路线二）**：把整条 OCR 流水线（预处理→ONNX 推理→后处理→文本解码）写成 C 共享库，Python 侧用 ctypes 调用——**ctypes 调外部函数时自动释放 GIL**，ThreadPool 即可获得近线性加速。

### 设计决策

1. **ctypes 调 C 共享库**：`ctypes.CDLL().func()` 调用期间自动释放 GIL，不需要 `Py_BEGIN_ALLOW_THREADS`。ThreadPool 多线程调用时真正并行。
2. **ONNX Runtime C API**：动态加载（dlopen/LoadLibrary）或链接。onnxruntime 1.19.2 已安装，C API header 位置待确认（当前只找到 pybind11 .so，未找到独立 C header）。
3. **图像加载留在 Python**：Python 侧用 Pillow/cv2 解码图片为 BGR uint8 numpy 数组，传 raw pointer 给 C 函数。图像加载不是瓶颈（<100ms/图），瓶颈是预处理+推理+后处理（~1.78s/图）。
4. **字符字典从 Python 传入**：rec 模型的 character 字典在 ONNX metadata 中，Python 侧已能读取（`session.get_modelmeta().custom_metadata_map["character"]`），init 时传给 C 库，不需要在 C 中读 ONNX metadata。
5. **C 库只暴露 3 个函数**：`cocr_init` / `cocr_ocr` / `cocr_cleanup`（+ `cocr_free` 释放返回的字符串）。

### C 库 API 设计

```c
// 初始化: 传入模型路径 + 字符字典 + ONNX 线程数
// 返回 0=成功, -1=失败
int cocr_init(const char* det_model_path,
              const char* rec_model_path,
              const char* cls_model_path,
              const char* character_dict,   // \n 分隔的字符
              int num_chars,
              int num_threads);

// OCR 单张图
// 输入: BGR uint8 像素, height, width, channels
// 输出: malloc 分配的字符串 (调用方用 cocr_free 释放), 格式: "text\tscore\ntext\tscore\n..."
//       NULL = 失败
char* cocr_ocr(const unsigned char* bgr, int height, int width, int channels);

// 释放 cocr_ocr 返回的字符串
void cocr_free(char* ptr);

// 清理: 释放所有 ONNX session
void cocr_cleanup(void);
```

### Python 侧集成

```python
# ocr.py 中的集成方式
import ctypes, os

_lib = ctypes.CDLL(os.path.join(os.path.dirname(__file__), 'cocr.so'))
# ... 设置 argtypes/restype ...

def cocr_init(det_path, rec_path, cls_path, chars, num_threads=1):
    _lib.cocr_init(det_path.encode(), rec_path.encode(), cls_path.encode(),
                    '\n'.join(chars).encode(), len(chars), num_threads)

def cocr_ocr(img_bgr):
    h, w, c = img_bgr.shape
    result = _lib.cocr_ocr(img_bgr.ctypes.data_as(ctypes.c_char_p), h, w, c)
    if result is None:
        return None
    text = ctypes.string_at(result).decode('utf-8')
    _lib.cocr_free(result)
    return text
```

---

## 需要在 C 中实现的完整 OCR 流水线

以下是从 rapidocr-onnxruntime v1.4.4 源码逆向分析得到的完整逻辑。所有细节均已确认，可直接翻译为 C。

### 模型文件位置

```
~/.local/lib/python3.9/site-packages/rapidocr_onnxruntime/models/
├── ch_PP-OCRv4_det_infer.onnx    (4.7MB, 文本检测)
├── ch_PP-OCRv4_rec_infer.onnx   (10.8MB, 文本识别)
└── ch_ppocr_mobile_v2.0_cls_infer.onnx  (585KB, 角度分类)
```

### ONNX 模型规格

| 模型 | 输入名 | 输入形状 | 输出名 | 输出形状 | 备注 |
|------|--------|----------|--------|----------|------|
| det | `x` | `[N,3,H,W]` H/W 须为 32 倍数 | `sigmoid_0.tmp_0` | `[N,1,H,W]` | 已 sigmoid 的概率图 |
| rec | `x` | `[N,3,48,W]` H 固定 48, W 动态 | `softmax_11.tmp_0` | `[N,seq_len,6625]` | 已 softmax, seq_len=W/8 |
| cls | `x` | `[N,3,48,W]` 全动态 | `save_infer_model/scale_0.tmp_1` | `[N,2]` | 原始 logits, 非 softmax |

### 字符字典

- **无外部 .txt 文件**，存在 rec 模型 ONNX metadata 的 custom key `"character"` 中
- 原始 6623 个字符，`splitlines()` 分割
- 处理后 6625 项：index 0 = blank (CTC blank), indices 1..6623 = 原始字符, index 6624 = " "(空格)
- Python 侧读取方式：`session.get_modelmeta().custom_metadata_map["character"].splitlines()`

### 流水线步骤

#### Step A: 全局预处理

1. **reduce_max_side**: 如果 `max(H,W) > 2000`，缩放长边到 2000，保持宽高比，H/W 取整到 32 倍数。记录 `ratio_h = orig_h / new_h`, `ratio_w = orig_w / new_w`
2. **increase_min_side**: 如果 `min(H,W) < 30`，缩放短边到 30，取整到 32 倍数
3. **letterbox**: 如果 `H <= 30` 或 `W/H > 8`，上下加黑边。padding_h = `max(int(W/8), 30)*2`，`padding_h = abs(new_h - H)//2`

#### Step B: 检测预处理 (DetPreProcess)

1. **resize (limit_type="min", limit_side_len=736)**: 如果 `min(H,W) < 736`，缩放短边到 736 保持宽高比；否则 ratio=1.0。然后 `resize_h = round(H*ratio/32)*32`, `resize_w = round(W*ratio/32)*32`。用 cv2.resize（双线性插值）
2. **normalize**: `pixel / 255.0 * (1/0.5) - 0.5 / 0.5` → 等价于 `pixel/255 * 2 - 1`，范围 [-1, +1]。mean=[0.5,0.5,0.5], std=[0.5,0.5,0.5]
3. **transpose**: HWC → CHW, shape `(3, H, W)`
4. **expand**: 加 batch 维 → `(1, 3, H, W)` float32

#### Step C: 检测推理

ONNX run, 输出 `[1, 1, H, W]` sigmoid 概率图

#### Step D: 检测后处理 (DBPostProcess)

参数: `thresh=0.3`, `box_thresh=0.5`, `min_size=3`, `unclip_ratio=1.6`, `max_candidates=1000`, `use_dilation=true`, `dilation_kernel=[[1,1],[1,1]]`

1. `segmentation = pred > 0.3` → 二值 mask
2. 如果 `use_dilation`: `cv2.dilate(mask.astype(uint8), kernel=[[1,1],[1,1]])`
3. **找轮廓** (`cv2.findContours`, `RETR_LIST`, `CHAIN_APPROX_SIMPLE`)
4. 对每个轮廓（最多 `max_candidates=1000` 个）:
   a. `cv2.minAreaRect` → 4 个角点（排序为 TL,TR,BR,BL）+ 短边 `sside`
   b. 跳过 `sside < min_size(3)` 的
   c. **score** (`box_score_fast`): 找轮廓的多边形外接矩形，在 score_map 上 `cv2.fillPoly` 填充多边形 mask，`cv2.mean(score_map_region, mask)[0]` = 多边形内平均概率
   d. 跳过 `score < box_thresh(0.5)` 的
   e. **unclip** (扩展多边形):
      - `distance = polygon_area * 1.6 / polygon_perimeter`
      - `pyclipper.PyclipperOffset()` + `AddPath(box, JT_ROUND, ET_CLOSEDPOLYGON)` + `Execute(distance)`
      - 对扩展后的多边形再次 `minAreaRect` 获取 4 点
      - 跳过 `sside < 5` 的
   f. **缩放到原图**: `box_x = round(box_x / map_W * dest_w)`, `box_y = round(box_y / map_H * dest_h)`，clip 到 `[0, dest]`
5. **filter_tag_det_res**: 排序点为顺时针 (`order_points_clockwise`)，clip 到图像边界，丢弃 `width<=3 or height<=3` 的框

#### Step E: 排序框

`sorted_boxes`: 按 `(y_top, x_left)` 排序，然后冒泡遍历：如果相邻框 y 差 < 10 且后框 x 更小，交换（模拟从上到下、从左到右的阅读顺序）

#### Step F: 裁剪文本区域

对每个 4 点多边形:
1. `crop_width = max(||p0-p1||, ||p2-p3||)`
2. `crop_height = max(||p0-p3||, ||p1-p2||)`
3. `cv2.getPerspectiveTransform` (4 源点 → 4 目标点 [0,0],[w,0],[w,h],[0,h])
4. `cv2.warpPerspective` (flags=INTER_CUBIC, borderMode=BORDER_REPLICATE)
5. 如果 `crop_height / crop_width >= 1.5`（竖排文字），`np.rot90` 旋转 90°

#### Step G: 角度分类（可选，但默认启用）

参数: `cls_batch_num=6`, `cls_thresh=0.9`, 目标尺寸 `[3, 48, 192]`

1. 按宽高比排序裁剪图（升序），分批（每批 6）
2. 预处理: resize 到 height=48, width=192（保持宽高比，右侧 pad 0），normalize 同检测（[-1,+1]）
3. ONNX 推理 → `[N, 2]`
4. `argmax` → 标签 `['0', '180']`
5. 如果标签是 `"180"` 且分数 > 0.9: `cv2.rotate(img, ROTATE_180)`
6. 按原始顺序 scatter 回去

#### Step H: 文本识别

参数: `rec_batch_num=6`, `imgH=48`, `imgW=320`, `text_score=0.5`

1. 按宽高比排序裁剪图（升序），分批（每批 6）
2. `max_wh_ratio = max(320/48=6.667, max(crop_w/crop_h))`，`img_width = int(48 * max_wh_ratio)`
3. 每张裁剪图预处理:
   a. `ratio = w/h`，如果 `ceil(48*ratio) > img_width` 则 `resized_w = img_width`，否则 `resized_w = ceil(48*ratio)`
   b. `cv2.resize(img, (resized_w, 48))`
   c. `astype(float32)` → `transpose((2,0,1))` → `/255 - 0.5 / 0.5` → [-1,+1]
   d. 右侧 pad 0 到 `[3, 48, img_width]`
4. batch → ONNX 推理 → `[N, seq_len, 6625]`
5. CTC 解码 (`CTCLabelDecode`):
   a. `preds_idx = preds.argmax(axis=2)` → 每步最佳字符索引 `[N, seq_len]`
   b. `preds_prob = preds.max(axis=2)` → 每步置信度
   c. 对每个 batch 元素:
      - CTC 去重: `selection[1:] = idx[1:] != idx[:-1]`（保留连续重复的第一个）
      - 去 blank: `selection &= idx != 0`（index 0 = blank）
      - `text = "".join(character[i] for i in idx[selection])`
      - `score = mean(prob[selection])`

#### Step I: 合并结果

1. 框坐标映射回原图: 逆序遍历 op_record: 先减 padding offset, 再乘 ratio_w/ratio_h, clip 到 [0, raw_W/H]
2. 过滤: 保留 `score >= text_score(0.5)` 的
3. 输出: `[[box(4x2 ints), text_str, score], ...]`

---

## C 实现需要手写的算法清单

以下是 Python 侧用 OpenCV/pyclipper/Shapely 实现的，C 侧需从头实现：

| 算法 | Python 来源 | C 实现难度 | 预计行数 | 备注 |
|------|-------------|-----------|----------|------|
| 双线性 resize | cv2.resize | 低 | ~40 | 标准算法 |
| 双三次 resize | cv2.resize(INTER_CUBIC) | 中 | ~60 | 用于裁剪 |
| normalize | numpy | 低 | ~15 | 简单算术 |
| transpose HWC→CHW | numpy | 低 | ~15 | 嵌套循环 |
| 二值化 threshold | numpy | 低 | ~10 | |
| 膨胀 dilate (2x2 kernel) | cv2.dilate | 低 | ~30 | max filter |
| 连通区域标记 | cv2.findContours | 中 | ~150 | union-find + 轮廓追踪 |
| 凸包 | (minAreaRect 内部) | 中 | ~60 | Andrew 单调链 |
| 最小外接矩形 | cv2.minAreaRect | 中 | ~120 | 旋转卡尺 |
| 多边形填充 | cv2.fillPoly | 中 | ~80 | 扫描线填充 |
| 多边形面积 | Shapely Polygon.area | 低 | ~15 | 鞋带公式 |
| 多边形周长 | Shapely Polygon.length | 低 | ~15 | 边长求和 |
| 多边形外扩 unclip | pyclipper PyclipperOffset | 中 | ~100 | 对凸四边形可简化 |
| 透视变换矩阵 | cv2.getPerspectiveTransform | 中 | ~100 | 解 8x8 线性方程组 |
| 透视变换 warp | cv2.warpPerspective | 中 | ~80 | 逆映射 + 双三次 |
| 排序框 sorted_boxes | Python | 低 | ~30 | |
| 旋转 180° | cv2.rotate | 低 | ~10 | |
| CTC 解码 | Python | 低 | ~50 | argmax + 去重 |
| ONNX C API 包装 | (新) | 中 | ~200 | init + run + cleanup |
| 主流水线编排 | (新) | 中 | ~200 | |
| **合计** | | | **~1400** | |

### 需要手写但可简化的算法

1. **连通区域标记**: 不需要完整的 Suzuki-Abe 轮廓追踪。用 union-find 标记连通区域，收集每个区域的边界像素，作为 minAreaRect 的输入点集。
2. **minAreaRect**: 对每个区域的像素点集，先求凸包（Andrew 单调链），再用旋转卡尺求最小外接矩形。返回 4 个角点。
3. **unclip（多边形外扩）**: 因为输入总是 minAreaRect 输出的凸四边形，可以简化: 计算每条边的外法线，将每个顶点沿相邻边法线平分线方向移动 distance。
4. **透视变换**: 求解 8x8 线性方程组（高斯消元）得到 3x3 变换矩阵，然后逆映射 + 双三次插值。
5. **多边形填充**: 扫描线算法，用于计算 score_map 在多边形内的平均值。

### 不需要手写的部分

- **图像加载**: Python 侧 Pillow/cv2 解码后传 raw pointer
- **字符字典**: Python 侧从 ONNX metadata 读取后传入
- **模型路径**: Python 侧定位后传入

---

## ONNX Runtime C API 集成

### 已知信息

- onnxruntime 1.19.2 已安装
- Python 包路径: `~/.local/lib/python3.9/site-packages/onnxruntime/`
- 目前只找到 `capi/onnxruntime_pybind11_state.cpython-39-x86_64-linux-gnu.so`（pybind11 绑定）
- **未找到独立的 C API header (`onnxruntime_c_api.h`) 和 `libonnxruntime.so`**
- 需要确认: onnxruntime C API header 和共享库是否需要单独安装，或可从 nuget/pypi 获取

### C API 关键函数（需要确认签名）

```c
// 获取 API
const OrtApiBase* base = OrtGetApiBase();           // 入口
const OrtApi* ort = base->GetApi(ORT_API_VERSION);   // API 表

// 环境
OrtEnv* env;
ort->CreateEnv(ORT_LOGGING_LEVEL_WARNING, "cocr", &env);

// Session 选项
OrtSessionOptions* opts;
ort->CreateSessionOptions(&opts);
ort->SetIntraOpNumThreads(opts, 1);    // monkey-patch: 每推理 1 线程
ort->SetInterOpNumThreads(opts, 1);

// 创建 session
OrtSession* det_session;
ort->CreateSession(env, det_model_path, opts, &det_session);

// 运行推理
OrtValue* input_tensor;
ort->CreateTensorWithDataAsOrtValue(allocator, data, data_len, shape, shape_len,
    ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT, &input_tensor);
const char* input_names[] = {"x"};
const char* output_names[] = {"sigmoid_0.tmp_0"};
ort->Run(session, NULL, input_names, &input_tensor, 1, output_names, 1, &output_tensor);

// 获取输出
void* output_data;
ort->GetTensorMutableData(output_tensor, &output_data);
```

### 需要解决的问题

1. **找到 C API header**: 检查 onnxruntime 是否有 `onnxruntime_c_api.h`，或需要从 GitHub 下载对应版本
2. **找到/链接共享库**: 需要 `libonnxruntime.so` (Linux) / `onnxruntime.dll` (Windows)。可能需要单独安装 onnxruntime C/C++ 包，或从 pybind11 so 中提取
3. **ORT_API_VERSION**: 需要与 1.19.2 匹配
4. **线程安全**: `OrtSession::Run()` 是否线程安全（多线程同时调用同一 session）？据文档应该是，但需验证
5. **Windows 构建**: 需要 MSVC 或 MinGW，需要找到 Windows 侧的 onnxruntime.dll

---

## 文件结构规划

```
doc2md/
├── cocr/                   # C 扩展
│   ├── cocr.c              # 主 C 实现 (~1400 行)
│   ├── cocr.h              # 头文件
│   ├── build.py            # 构建脚本 (找到 onnxruntime, 编译 .so/.dll)
│   └── cocr.so / cocr.dll  # 编译产物 (不提交, 用户 build.py 生成)
├── ocr.py                  # 修改: 增加 C 路径, ThreadPool 时调用 cocr_ocr
└── ...
```

### ocr.py 修改方案

当前 ocr.py 结构（需先读确认）:
- `_get_engine()` → 初始化 rapidocr RapidOCR 实例（单例）
- `_ocr_image(path)` → 调用 RapidOCR 实例处理图片
- `_broken` 标志 → 引擎失败缓存

修改后:
- `cocr_init()` → 初始化 C 库（替代 rapidocr RapidOCR 实例）
- `_ocr_image(path)` → Python 解码图片 → `cocr_ocr(raw_bgr)` → 解析返回的文本
- 保留 `_broken` 逻辑
- ProcessPool 模式不变（各进程独立 C 库实例）
- ThreadPool 模式: 所有线程共享同一 C 库实例，ctypes 调用自动释放 GIL

### 构建脚本 (build.py)

```python
# 1. 找到 onnxruntime 包路径
import onnxruntime
ort_dir = os.path.dirname(onnxruntime.__file__)
# 2. 找到 C API header 和共享库
# 3. 找到 rapidocr 模型文件路径
# 4. gcc -shared -fPIC -o cocr.so cocr.c -I<ort_include> -L<ort_lib> -lonnxruntime
# 5. Windows: cl /LD cocr.c /I<ort_include> /link onnxruntime.lib
```

---

## 测试计划

1. **单元测试**: 每个算法在 C 中实现后，用 Python 生成测试数据验证正确性
2. **集成测试**: 用现有测试中的 OCR 测试用例验证 C 路径输出与 Python 路径一致
3. **性能测试**: Windows 28 核 156 图，对比 ThreadPool+C 扩展 vs ThreadPool+rapidocr
4. **回归测试**: 130/130 全通过
5. **双平台**: Linux + Windows 均编译运行

---

## 风险与注意事项

1. **onnxruntime C API header 缺失**: 当前系统只找到 pybind11 .so，未找到独立 C header。可能需要:
   - 从 onnxruntime GitHub release 下载 C/C++ 包
   - 或用 `pip install onnxruntime` 的 `--c-api` 选项（如果有的话）
   - 或从 nuget 包提取（Windows）

2. **算法精度**: C 实现的 resize/contour/minAreaRect/unclip/warp 算法需要与 OpenCV/pyclipper 输出精确匹配，否则 OCR 结果会不同。特别是:
   - 双线性/双三次插值的边界处理
   - 连通区域标记的拓扑等价性
   - minAreaRect 的旋转角度精度
   - unclip 的多边形扩展方向

3. **跨平台编译**: Linux gcc vs Windows MSVC，需要处理:
   - 路径分隔符
   - 共享库后缀 (.so vs .dll)
   - ONNX Runtime API 的 char/wchar_t 差异
   - 编译器特性差异

4. **内存管理**: C 侧所有 malloc 必须有对应的 free，特别是在错误路径上。建议用 arena 分配器简化。

5. **浮点精度**: numpy float32 vs C float，需要确保中间计算都用 float32（不是 double），否则结果可能微小不同。

---

## 快速上手指南

### 环境准备

```bash
cd /home/claude/ai-doc2md
python3 tests/run_all.py -q     # 确认 130/130 通过
python3 -c "import onnxruntime; print(onnxruntime.__version__)"  # 1.19.2
```

### 关键源码文件

| 文件 | 作用 | 行数 |
|------|------|------|
| `doc2md/ocr.py` | OCR 封装，需修改集成 C 路径 | ~100 |
| `doc2md/pipeline.py` | 主流程编排，OCR 模式决策 | ~837 |
| `doc2md/parsers/image.py` | 图片解析器，存图+占位模式 | ~35 |
| `doc2md/parsers/_common.py` | defer 标记 + 聚合 | ~30 |
| `doc2md/detector.py` | 格式识别 | ~208 |
| `doc2md/cli.py` | CLI + 零参数模式 | ~200 |

### rapidocr 源码位置（参考）

```
~/.local/lib/python3.9/site-packages/rapidocr_onnxruntime/
├── main.py                    # 主流程编排
├── config.yaml                 # 配置参数
├── utils/
│   ├── load_image.py          # 图片加载 (PIL + cv2)
│   ├── infer_engine.py         # ONNX session 封装
│   ├── process_img.py          # 全局预处理
│   └── parse_parameters.py    # 参数解析
├── ch_ppocr_det/
│   ├── text_detect.py         # 检测主逻辑
│   └── utils.py                # DetPreProcess + DBPostProcess
├── ch_ppocr_cls/
│   ├── text_cls.py             # 分类主逻辑
│   └── utils.py                # ClsPreProcess + ClsPostProcess
├── ch_ppocr_rec/
│   ├── text_recognize.py       # 识别主逻辑
│   └── utils.py                # RecPreProcess + CTCLabelDecode
└── models/
    ├── ch_PP-OCRv4_det_infer.onnx
    ├── ch_PP-OCRv4_rec_infer.onnx
    └── ch_ppocr_mobile_v2.0_cls_infer.onnx
```

### rapidocr 关键配置参数 (config.yaml)

```yaml
Global:
  max_side_len: 2000          # 全局预处理: 长边最大
  min_side_len: 30            # 全局预处理: 短边最小
  
Det:
  limit_side_len: 736         # 检测预处理: 短边最小
  limit_type: "min"
  thresh: 0.3                 # 二值化阈值
  box_thresh: 0.5             # 框分数阈值
  min_size: 3                 # 最小框尺寸
  unclip_ratio: 1.6           # 多边形扩展比
  max_candidates: 1000        # 最大轮廓数
  use_dilation: true          # 是否膨胀
  dilation_kernel: [[1,1],[1,1]]

Cls:
  cls_thresh: 0.9             # 分类阈值
  cls_batch_num: 6

Rec:
  imgH: 48                    # 识别高度
  imgW: 320                   # 识别宽度
  rec_batch_num: 6
  text_score: 0.5             # 识别分数阈值
```

---

## 下一步行动

1. **确认 onnxruntime C API 可用性**: 检查是否能找到 header 和共享库，或需要从 GitHub 下载
2. **写 cocr.c**: 按上述算法清单逐个实现，先编译通过再逐步验证
3. **写 build.py**: 自动找到 onnxruntime 路径 + rapidocr 模型路径，编译共享库
4. **修改 ocr.py**: 集成 C 路径，保留 Python 路径作为 fallback
5. **测试**: 130/130 回归 + 性能对比
