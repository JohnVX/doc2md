"""增量 manifest: relpath -> {sha256, id, title, category, source, source_type, doc_path, summary, tags, deferred}.

未变(sha 相同)即跳过. catalog 每次从全量 manifest 重建.
"""
import logging

import yaml
from pathlib import Path

from . import util

log = logging.getLogger("manifest")


class Manifest:
    def __init__(self, path):
        """加载 manifest 文件 (不存在则空). 加载时剥离旧版 toc/sections 字段."""
        self.path = Path(path)
        self.entries = {}   # relpath -> dict
        if self.path.exists():
            try:
                data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
                for e in data.get("entries", []):
                    rp = e.get("relpath")
                    if rp:
                        e.pop("toc", None)
                        e.pop("sections", None)
                        self.entries[rp] = e
            except Exception as ex:
                log.warning("manifest 读取失败(%s): %s", self.path, ex)

    def is_unchanged(self, relpath, sha):
        """该文件 sha 是否与 manifest 记录一致 (未变则跳过)."""
        e = self.entries.get(relpath)
        return bool(e) and e.get("sha256") == sha

    def upsert(self, entry):
        """插入或更新一条 relpath -> entry."""
        self.entries[entry["relpath"]] = entry

    def values(self):
        """返回全部条目列表 (按插入序)."""
        return list(self.entries.values())

    def save(self):
        """写 manifest 到磁盘 (YAML, allow_unicode)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        out = {"entries": list(self.entries.values())}
        util.write_text(self.path, yaml.safe_dump(out, allow_unicode=True, sort_keys=False))
