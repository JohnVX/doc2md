"""主流程: 扫描输入 -> 识别 -> 解析 -> 去噪 -> 分类 -> 落 md + 拷原文件 -> 目录 -> 增量.

parser 返回的 deferred (结构化 stage2 待处理项) 经 aggregate_deferred 合并后
写入 front-matter / manifest / catalog, 供 stage2 agent 编程查询.
"""
import logging
import re
import shutil
from pathlib import Path

import yaml

from . import catalog, denoise, detector, manifest, parsers, util
from .config import load_config
from .parsers._common import aggregate_deferred, defer

log = logging.getLogger("pipeline")

LARGE_FILE_MB = 50  # 超过此值给出慢速警告


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


def _finalize(out, move, man, conf, p, info, rel, sha, doc_id, parsed):
    """公共收尾(支持格式与 defer 共用): 去噪/分类/重分类清理/拷原文件/side files/写 md/入 manifest.

    返回 "ok" 或 "write_fail"(写 md 失败, 调用方计 errors 且不入 manifest).
    """
    body = denoise.clean_text(parsed["body"])
    body = denoise.strip_boilerplate(body)
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

    # md parser 登记的 side files (内嵌本地图片引用) 一并拷到 assets
    for src, dest in parsed.get("meta", {}).get("side_files", []):
        adir = out / "original-doc" / f"{doc_id}_assets"
        adir.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(src, str(adir / dest))
        except Exception as ex:
            log.warning("side file 拷贝失败 %s: %s", src, ex)

    summary = denoise.extract_summary(body)
    sections = denoise.extract_sections(body)
    toc = [s["title"] for s in sections[:10]]
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
        "tags": doc["tags"], "toc": toc, "deferred": deferred,
        "sections": [{"level": s["level"], "title": s["title"],
                      "anchor": util.heading_slug(s["title"])} for s in sections],
    })
    log.info("处理: %s -> docs/%s/%s.md", rel, category, doc_id)
    return "ok"


def run(input_dir, output_dir, config_path=None, move=False, verbose=False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")
    # 压掉 pdfminer/pdfplumber 的内部噪声 (FontBBox 等), 只留 ERROR
    for noisy in ("pdfminer", "pdfminer.six", "pdfplumber", "PIL"):
        logging.getLogger(noisy).setLevel(logging.ERROR)

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
    try:
        for p in files:
            info = detector.detect(str(p))
            handler = info["handler"]
            rel = util.relpath(p, in_dir)
            sha = util.sha256_file(p)
            doc_id = util.stable_doc_id(rel)

            if handler is None or parsers.get(handler) is None:
                if info["format"] == "empty":
                    # 空文件: 无内容, 跳过(并清理可能的旧条目)
                    log.warning("跳过空文件: %s", p.name)
                    unsupported += 1
                    if not move:
                        old0 = man.entries.get(rel)
                        if old0:
                            _prune_entry(man, out, rel, old0)
                            purged += 1
                    continue
                # 非空但不支持格式(旧 office/二进制/损坏 zip 等):
                # 仍复制原件 + 建 defer md 进 catalog, 让 stage2 接手(不静默丢弃)
                if not move and man.is_unchanged(rel, sha):
                    skipped += 1
                    continue
                # 若该 doc 之前是支持格式(留有 assets), 清掉避免孤儿
                old_assets = out / "original-doc" / f"{doc_id}_assets"
                if old_assets.exists():
                    shutil.rmtree(old_assets, ignore_errors=True)
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
            else:
                if man.is_unchanged(rel, sha):
                    # sha 未变但 config 可能变了: 用已存 title+summary 重判类目
                    old_entry = man.entries.get(rel)
                    if old_entry:
                        reclassify_text = (old_entry.get("title", "") + "\n"
                                           + old_entry.get("summary", ""))
                        new_cat = conf.classify(p.name, reclassify_text)
                        old_cat = old_entry.get("category", conf.default_category)
                        if new_cat != old_cat:
                            _reclassify(out, man, conf, rel, old_entry, new_cat)
                    skipped += 1
                    continue
                ctx = dict(info.get("meta", {}))
                ctx.update({"relpath": rel, "doc_id": doc_id, "output_root": out})
                # 大文件提示
                try:
                    size = p.stat().st_size
                except OSError:
                    size = 0
                if size > LARGE_FILE_MB * 1024 * 1024:
                    log.warning("大文件(%d MB), 解析可能较慢: %s", size // (1024 * 1024), rel)
                # 重新处理: 先清掉该 doc 旧 assets 目录, 避免旧版图残留(parser 会重建)
                old_assets = out / "original-doc" / f"{doc_id}_assets"
                if old_assets.exists():
                    shutil.rmtree(old_assets, ignore_errors=True)
                try:
                    parsed = parsers.get(handler)(str(p), ctx)
                except Exception as ex:
                    msg = str(ex).lower()
                    if "encrypt" in msg or "password" in msg:
                        log.error("文档可能加密, 跳过解析 %s: %s", rel, ex)
                    else:
                        log.error("解析失败 %s: %s", rel, ex)
                    errors += 1
                    deferred_list = []
                    parsed = {
                        "title": p.stem,
                        "body": defer(deferred_list, "parse-failed", 1,
                                       f"{ex}; 见 original-doc/{rel}"),
                        "tags": [], "meta": {},
                        "deferred": deferred_list,
                    }

            # 公共收尾(支持格式 / defer / 解析失败占位 都走这里)
            status = _finalize(out, move, man, conf, p, info, rel, sha, doc_id, parsed)
            if status == "write_fail":
                errors += 1
            else:
                processed += 1
    except KeyboardInterrupt:
        log.warning("收到中断信号, 保存已处理部分并退出...")

    # 清理: 输入已删除的文件 (--move 模式输入本就被消费空, 不清理)
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
