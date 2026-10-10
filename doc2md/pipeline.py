"""主流程: 扫描输入 -> 识别 -> [流式解析+OCR+写出] -> 清理+索引.

三阶段: serial 预处理(检测/sha/跳过/重分类) -> 流式解析+收尾 -> 清理+索引.
解析用 ProcessPool 而非 ThreadPool: fitz.Page.find_tables() 在 C 层有全局状态, 线程不安全.
OCR 用 ThreadPool 而非 ProcessPool: ONNX Runtime run() 线程安全, 共享单实例引擎零额外内存.
流式处理: 解析完一个文件立即 OCR+写出+gc.collect(), 释放内存后再处理下一个.
  避免所有解析结果常驻内存导致低内存机器 OOM (1GB 可用时 18 文件 178 图可跑完).
parser 只存图+插占位 <!-- ocr:type:dest -->, _post_ocr 替换占位为 OCR 文本或 defer 标记.
并行度自动嗅探 (CPU 核数 + 可用内存), 无需人工配置.
parser 返回的 deferred (结构化 stage2 待处理项) 经 aggregate_deferred 合并后
写入 front-matter / manifest / catalog, 供 stage2 agent 编程查询.
"""
import gc
import logging
import os
import re
import shutil
import sys
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path

import yaml

from . import catalog, denoise, detector, manifest, ocr, parsers, util
from .config import load_config
from .parsers._common import aggregate_deferred, defer

log = logging.getLogger("pipeline")

LARGE_FILE_MB = 50  # 超过此值给出慢速警告
MAX_WORKERS = None   # None=自动嗅探; 测试可设为 1 强制串行
MAX_OCR_THREADS = None  # None=自动嗅探

_OCR_RE = re.compile(r'<!-- ocr:(img|scan):(\S+) -->')


def _detect_resources():
    """自动嗅探 CPU 核数和可用内存, 返回 (max_workers, max_ocr_threads, cpus, avail_mb).

    无需人工配置: 根据硬件自动调节, 最多吃 ~80% 资源, 留余量给系统/其他程序.
    - MAX_WORKERS (ProcessPool 解析): 每进程 ~500MB, 受 CPU 和内存约束, 上限 8
    - MAX_OCR_THREADS (ThreadPool OCR): OMP_NUM_THREADS=1 时每线程 1 核,
      取 cpu*0.8 (向下取整), 不加人工 cap
    """
    cpus = os.cpu_count() or 2

    avail_mb = 2048  # 保守默认
    try:
        import psutil
        avail_mb = psutil.virtual_memory().available // (1024 * 1024)
    except ImportError:
        if sys.platform == "win32":
            try:
                import ctypes
                class _MemStatus(ctypes.Structure):
                    _fields_ = [
                        ("dwLength", ctypes.c_ulong),
                        ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                    ]
                ms = _MemStatus()
                ms.dwLength = ctypes.sizeof(ms)
                ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))
                avail_mb = ms.ullAvailPhys // (1024 * 1024)
            except Exception:
                pass
        else:
            try:
                with open("/proc/meminfo") as f:
                    for line in f:
                        if line.startswith("MemAvailable:"):
                            avail_mb = int(line.split()[1]) // 1024
                            break
            except Exception:
                pass

    ocr_budget = max(0, int(avail_mb * 0.8) - 1024)
    max_ocr = min(max(1, int(cpus * 0.8)), max(1, ocr_budget // 200))
    max_workers = min(cpus * 2, max(1, avail_mb // 500), 8, max(1, int(cpus * 0.8) - max_ocr))
    return max_workers, max_ocr, cpus, avail_mb


def _post_ocr(body, out, doc_id, deferred_list):
    """流式收尾 OCR: 替换 <!-- ocr:type:dest --> 占位为 OCR 文本或 defer 标记.

    parser 在 worker 进程中只存图+插占位, 不加载 OCR 引擎 (避免多进程内存爆炸).
    本函数在 parent 进程执行, ThreadPool 并行 OCR (ONNX Runtime 线程安全,
    共享单实例引擎, 零额外内存). defer() 的 list.append 受 GIL 保护, 线程安全.
    """
    adir = out / "original-doc" / f"{doc_id}_assets"
    matches = list(_OCR_RE.finditer(body))
    if not matches:
        return body

    def ocr_one(m):
        """单图 OCR: 返回替换文本 (OCR 文本块或 defer 标记)."""
        ocr_type, dest = m.group(1), m.group(2)
        asset_path = adir / dest
        if not asset_path.exists():
            return defer(deferred_list, "scan-page" if ocr_type == "scan" else "image-ocr", 1, "资产文件丢失")
        blob = asset_path.read_bytes()
        if not ocr.is_ocr_supported(blob):
            return defer(deferred_list, "scan-page" if ocr_type == "scan" else "image-ocr", 1, "WMF/EMF格式不支持OCR")
        txt = ocr.ocr_image(blob)
        if txt:
            return f"```\n{txt}\n```"
        return defer(deferred_list, "scan-page" if ocr_type == "scan" else "image-ocr", 1)

    if len(matches) == 1:
        return _OCR_RE.sub(ocr_one, body)

    with ThreadPoolExecutor(max_workers=min(MAX_OCR_THREADS, len(matches))) as pool:
        replacements = list(pool.map(ocr_one, matches))

    parts = []
    last_end = 0
    for m, repl in zip(matches, replacements):
        parts.append(body[last_end:m.start()])
        parts.append(repl)
        last_end = m.end()
    parts.append(body[last_end:])
    return "".join(parts)


def _parse_failure(p, rel, ex):
    """解析失败时构造 defer 占位. 返回 parsed dict."""
    msg = str(ex).lower()
    if "encrypt" in msg or "password" in msg:
        log.error("文档可能加密, 跳过解析 %s: %s", rel, ex)
    else:
        log.error("解析失败 %s: %s", rel, ex)
    deferred_list = []
    return {
        "title": p.stem,
        "body": defer(deferred_list, "parse-failed", 1, f"{ex}; 见 original-doc/{rel}"),
        "tags": [], "meta": {},
        "deferred": deferred_list,
    }


def _parse_worker(handler, path_str, ctx_dict):
    """子进程解析入口: 返回 (parsed, elapsed_seconds). 异常不捕获, 由调用方处理."""
    from doc2md import parsers
    t0 = time.time()
    result = parsers.get(handler)(path_str, ctx_dict)
    return result, time.time() - t0


def _reclassify(out, man, conf, rel, old_entry, new_cat):
    """配置变更后重分类: 用已存 title+summary 重判类目, 移动 md, 更新 manifest."""
    doc_id = old_entry["id"]
    old_cat = old_entry.get("category", conf.default_category)
    old_doc_path = old_entry.get("doc_path", "")
    new_doc_path = f"docs/{new_cat}/{doc_id}.md"
    old_md = out / old_doc_path
    new_dir = out / "docs" / new_cat
    new_dir.mkdir(parents=True, exist_ok=True)
    if old_md.exists():
        md_text = old_md.read_text(encoding="utf-8")
        m = re.match(r"^---\n(.*?)\n---\n", md_text, re.S)
        if m:
            fm = yaml.safe_load(m.group(1))
            fm["category"] = new_cat
            new_fm = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False).strip()
            md_text = f"---\n{new_fm}\n---\n" + md_text[m.end():]
        util.write_text(new_dir / f"{doc_id}.md", md_text)
        old_md.unlink()
    old_entry["category"] = new_cat
    old_entry["doc_path"] = new_doc_path
    log.info("重分类(配置变更): %s %s -> %s", rel, old_cat, new_cat)


def _prune_entry(man, out, rel, entry):
    """清理一个 manifest 条目对应的产物: md / 原文件 / assets 目录 / manifest 条目.

    用于"输入已删除"和"格式变为不支持"两种场景, 避免孤儿/stale.
    """
    did = entry.get("id", "")
    op = out / entry.get("doc_path", "")
    if op.exists() and "docs" in op.parts:
        try:
            op.unlink()
        except Exception as ex:
            log.warning("删孤儿 md 失败 %s: %s", op, ex)
    sp = out / entry.get("source", "")
    if sp.exists() and "original-doc" in sp.parts:
        try:
            sp.unlink()
        except Exception as ex:
            log.warning("删孤儿原文件失败 %s: %s", sp, ex)
    if did:
        adir = out / "original-doc" / f"{did}_assets"
        if adir.exists():
            shutil.rmtree(adir, ignore_errors=True)
    if rel in man.entries:
        del man.entries[rel]


def _finalize(out, move, man, conf, p, info, rel, sha, doc_id, parsed, parse_time=0.0):
    """公共收尾(支持格式与 defer 共用): 去噪/分类/重分类清理/拷原文件/side files/写 md/入 manifest.

    返回 "ok" 或 "write_fail"(写 md 失败, 调用方计 errors 且不入 manifest).
    """
    t0 = time.time()
    # md parser 登记的 side files 先拷到 assets (OCR 需读这些文件)
    for src, dest in parsed.get("meta", {}).get("side_files", []):
        adir = out / "original-doc" / f"{doc_id}_assets"
        adir.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(src, str(adir / dest))
        except Exception as ex:
            log.warning("side file 拷贝失败 %s: %s", src, ex)
    body = denoise.clean_text(parsed["body"])
    body = denoise.strip_boilerplate(body)
    body = _post_ocr(body, out, doc_id, parsed.setdefault("deferred", []))
    title = parsed.get("title") or p.stem
    category = conf.classify(p.name, title + "\n" + body[:4000])
    if category not in conf.categories_set():
        category = conf.default_category
    cat_dir = out / "docs" / category
    cat_dir.mkdir(parents=True, exist_ok=True)

    # 分类变了: 删旧类目下的旧 md, 避免孤儿
    new_doc_path = f"docs/{category}/{doc_id}.md"
    old_entry = man.entries.get(rel)
    if old_entry and old_entry.get("doc_path") and old_entry["doc_path"] != new_doc_path:
        op = out / old_entry["doc_path"]
        if op.exists():
            op.unlink()
            log.info("清理旧类目 md: %s -> %s", old_entry["doc_path"], new_doc_path)

    # 拷贝/移动 原文件 (保留相对目录结构)
    dst_orig = out / "original-doc" / rel
    dst_orig.parent.mkdir(parents=True, exist_ok=True)
    try:
        if move:
            shutil.move(str(p), str(dst_orig))
        else:
            shutil.copy2(str(p), str(dst_orig))
    except Exception as ex:
        log.warning("原文件拷贝失败 %s: %s", rel, ex)

    summary = denoise.extract_summary(body)
    sections = denoise.extract_sections(body)
    deferred = aggregate_deferred(parsed.get("deferred", []))
    # 长 md 顶部渲染目录(导航概览, 链接到章节锚点); 短文档不加
    if len(sections) >= 6:
        toc_lines = "\n".join(f"- [{s['title']}](#{util.heading_slug(s['title'])})"
                              for s in sections[:30])
        body = f"## 目录\n\n{toc_lines}\n\n" + body
    doc = {
        "id": doc_id, "title": title, "category": category,
        "source": f"original-doc/{rel}", "source_type": info["format"],
        "tags": parsed.get("tags", []) or [], "summary": summary,
        "deferred": deferred,
    }
    md_text = denoise.front_matter(doc) + body + ("" if body.endswith("\n") else "\n")
    try:
        util.write_text(cat_dir / f"{doc_id}.md", md_text)
    except Exception as ex:
        log.error("写 md 失败 %s: %s", cat_dir / f"{doc_id}.md", ex)
        return "write_fail"
    man.upsert({
        "relpath": rel, "sha256": sha, "id": doc_id, "title": title,
        "category": category, "source": doc["source"], "source_type": doc["source_type"],
        "doc_path": f"docs/{category}/{doc_id}.md", "summary": summary,
        "tags": doc["tags"], "deferred": deferred,
    })
    log.info("处理: %s -> docs/%s/%s.md (%.1fs)", rel, category, doc_id,
             parse_time + (time.time() - t0))
    return "ok"


def _handle_empty(man, out, rel, move):
    """空文件: 跳过并清理旧条目. 返回 (unsupported_delta, purged_delta)."""
    log.warning("跳过空文件: %s", rel)
    purged = 0
    if not move:
        old = man.entries.get(rel)
        if old:
            _prune_entry(man, out, rel, old)
            purged = 1
    return 1, purged


def _try_reclassify(out, man, conf, p, rel):
    """sha 未变时检查配置变更是否需重分类."""
    old_entry = man.entries.get(rel)
    if not old_entry:
        return
    text = old_entry.get("title", "") + "\n" + old_entry.get("summary", "")
    new_cat = conf.classify(p.name, text)
    old_cat = old_entry.get("category", conf.default_category)
    if new_cat != old_cat:
        _reclassify(out, man, conf, rel, old_entry, new_cat)


def _clear_old_assets(out, doc_id):
    """清理该 doc 的旧 assets 目录, 避免旧版图残留."""
    old_assets = out / "original-doc" / f"{doc_id}_assets"
    if old_assets.exists():
        shutil.rmtree(old_assets, ignore_errors=True)


def run(input_dir, output_dir, config_path=None, move=False, verbose=False):
    """主流程入口: 路径校验 -> 三阶段处理 -> 清理 + 索引.

    返回 (processed, skipped, errors). 中断时保存已处理部分.
    """
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")
    # 压掉 pdfminer/PIL 的内部噪声 (FontBBox 等), 只留 ERROR
    for noisy in ("pdfminer", "pdfminer.six", "PIL"):
        logging.getLogger(noisy).setLevel(logging.ERROR)

    # --- 资源嗅探: 自动调节并行度 (无人工配置) ---
    global MAX_WORKERS, MAX_OCR_THREADS
    if MAX_WORKERS is None or MAX_OCR_THREADS is None:
        dw, dt, cpus, avail_mb = _detect_resources()
        if MAX_WORKERS is None:
            MAX_WORKERS = dw
        if MAX_OCR_THREADS is None:
            MAX_OCR_THREADS = dt
        log.info("硬件: %d 核 / %.1fGB 可用 -> 解析 %d 进程, OCR %d 线程",
                 cpus, avail_mb / 1024, MAX_WORKERS, MAX_OCR_THREADS)

    # --- 路径校验 ---
    in_dir = Path(input_dir).resolve()
    out = Path(output_dir).resolve()
    if not in_dir.exists() or not in_dir.is_dir():
        log.error("输入目录不存在或不是目录: %s", input_dir)
        return 0, 0, 1
    if in_dir == out:
        log.error("输入与输出不能是同一目录: %s", in_dir)
        return 0, 0, 1
    try:
        out.relative_to(in_dir)  # 输出在输入内部 -> 递归污染
        log.error("输出目录不能在输入目录内部: %s 在 %s 内", out, in_dir)
        return 0, 0, 1
    except ValueError:
        pass

    conf = load_config(config_path)
    (out / "index").mkdir(parents=True, exist_ok=True)
    (out / "docs").mkdir(parents=True, exist_ok=True)
    (out / "original-doc").mkdir(parents=True, exist_ok=True)
    man = manifest.Manifest(out / "index" / ".manifest.yaml")

    files = [p for p in in_dir.rglob("*") if p.is_file() and not p.name.startswith(".")]
    files.sort(key=lambda p: str(p).lower())
    current_rels = {util.relpath(p, in_dir) for p in files}
    if not files:
        log.warning("输入目录为空, 无文件可处理: %s", in_dir)

    processed = skipped = errors = unsupported = purged = deferred = 0
    parse_items = []   # (p, info, rel, sha, doc_id, ctx, handler) — 需并行解析
    finalize_items = []  # (p, info, rel, sha, doc_id, parsed, is_error) — 无需解析, 直接收尾

    # === 阶段 1: 串行预处理 (检测/sha/跳过/重分类/不支持格式) ===
    interrupted = False
    try:
        for p in files:
            info = detector.detect(str(p))
            handler = info["handler"]
            rel = util.relpath(p, in_dir)
            sha = util.sha256_file(p)
            doc_id = util.stable_doc_id(rel)

            if handler is None or parsers.get(handler) is None:
                if info["format"] == "empty":
                    u, pu = _handle_empty(man, out, rel, move)
                    unsupported += u
                    purged += pu
                    continue
                if not move and man.is_unchanged(rel, sha):
                    skipped += 1
                    continue
                _clear_old_assets(out, doc_id)
                deferred_list = []
                parsed = {
                    "title": p.stem,
                    "body": defer(deferred_list, "unsupported", 1,
                                  f"格式'{info['format']}' stage1未解析, 见 original-doc/{rel}"),
                    "tags": [], "meta": {},
                    "deferred": deferred_list,
                }
                deferred += 1
                log.info("defer(stage2): %s (format=%s)", rel, info["format"])
                finalize_items.append((p, info, rel, sha, doc_id, parsed, False))
            else:
                if man.is_unchanged(rel, sha):
                    _try_reclassify(out, man, conf, p, rel)
                    skipped += 1
                    continue
                ctx = dict(info.get("meta", {}))
                ctx.update({"relpath": rel, "doc_id": doc_id, "output_root": out})
                try:
                    size = p.stat().st_size
                except OSError:
                    size = 0
                if size > LARGE_FILE_MB * 1024 * 1024:
                    log.warning("大文件(%d MB), 解析可能较慢: %s", size // (1024 * 1024), rel)
                _clear_old_assets(out, doc_id)
                parse_items.append((p, info, rel, sha, doc_id, ctx, handler))
    except KeyboardInterrupt:
        interrupted = True
        log.warning("收到中断信号 (扫描阶段), 保存已处理部分并退出...")

    if not interrupted:
        log.info("扫描完成: %d 待解析, %d 跳过, %d 不支持 (共 %d 文件)",
                 len(parse_items), skipped, len(finalize_items), len(files))

    # === 阶段 2+3: 流式解析+收尾 (解析完立即 OCR+写出+释放; 低内存机器不 OOM) ===
    if not interrupted and parse_items:
        max_workers = min(MAX_WORKERS, len(parse_items))
        log.info("解析 %d 个文件 (%d 进程)...", len(parse_items), max_workers)
        pool = ProcessPoolExecutor(max_workers=max_workers)
        future_map = {}
        for item in parse_items:
            p, info, rel, sha, doc_id, ctx, handler = item
            future_map[rel] = pool.submit(_parse_worker, handler, str(p), ctx)
        ocr_announced = False
        try:
            for item in parse_items:
                p, info, rel, sha, doc_id, ctx, handler = item
                if interrupted:
                    break
                try:
                    parsed, parse_time = future_map[rel].result()
                    log.info("解析完成: %s (%.1fs)", rel, parse_time)
                except KeyboardInterrupt:
                    interrupted = True
                    break
                except Exception as ex:
                    parsed = _parse_failure(p, rel, ex)
                    errors += 1
                    parse_time = 0.0
                    log.info("解析失败: %s", rel)
                    deferred += 1
                n_ocr = len(_OCR_RE.findall(parsed.get("body", "")))
                if n_ocr > 0 and not ocr_announced:
                    log.info("OCR: %d 线程...", MAX_OCR_THREADS)
                    ocr_announced = True
                status = _finalize(out, move, man, conf, p, info, rel, sha, doc_id, parsed, parse_time)
                if status == "write_fail":
                    errors += 1
                else:
                    processed += 1
                del parsed
                gc.collect()
        except KeyboardInterrupt:
            interrupted = True
        finally:
            for f in future_map.values():
                f.cancel()
            pool.shutdown(wait=True, cancel_futures=True)
        if interrupted:
            log.warning("收到中断信号 (解析/收尾阶段), 保存已处理部分并退出...")

    # finalize_items: 无需解析, 直接收尾
    if not interrupted:
        try:
            for item in finalize_items:
                p, info, rel, sha, doc_id, parsed, _is_err = item
                status = _finalize(out, move, man, conf, p, info, rel, sha, doc_id, parsed)
                if status == "write_fail":
                    errors += 1
                else:
                    processed += 1
        except KeyboardInterrupt:
            interrupted = True
            log.warning("收到中断信号 (收尾阶段), 保存已处理部分并退出...")

    # === 阶段 4: 清理 + 保存 + 生成索引 ===
    if not move:
        for rp in list(man.entries):
            if rp in current_rels:
                continue
            _prune_entry(man, out, rp, man.entries[rp])
            purged += 1
            log.info("清理已删除输入: %s", rp)

    try:
        man.save()
    except Exception as ex:
        log.error("manifest 保存失败: %s", ex)
    try:
        catalog.build_catalog(man.values(), out)
        catalog.build_handoff(man.values(), out)
    except Exception as ex:
        log.error("catalog/handoff 生成失败: %s", ex)
    log.info("完成: 处理 %d, 跳过 %d, 失败 %d, 清理 %d, 不支持 %d, defer %d",
             processed, skipped, errors, purged, unsupported, deferred)
    return processed, skipped, errors
