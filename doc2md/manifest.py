"""增量 manifest: relpath -> {sha256, id, title, category, source, source_type, doc_path, summary, tags, toc, sections}.

未变(sha 相同)即跳过. catalog 每次从全量 manifest 重建.
"""
import logging

import yaml
from pathlib import Path

from . import util

log = logging.getLogger("manifest")


class Manifest:
    def __init__(self, path):
        self.path = Path(path)
        self.entries = {}   # relpath -> dict
        if self.path.exists():
            try:
                data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
                for e in data.get("entries", []):
                    rp = e.get("relpath")
                    if rp:
                        self.entries[rp] = e
            except Exception as ex:
                log.warning("manifest 读取失败(%s): %s", self.path, ex)

    def is_unchanged(self, relpath, sha):
        e = self.entries.get(relpath)
        return bool(e) and e.get("sha256") == sha

    def upsert(self, entry):
        self.entries[entry["relpath"]] = entry

    def values(self):
        return list(self.entries.values())

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        out = {"entries": list(self.entries.values())}
        util.write_text(self.path, yaml.safe_dump(out, allow_unicode=True, sort_keys=False))
