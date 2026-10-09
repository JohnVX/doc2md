"""配置: 通用化分类 (taxonomy/mapping/keywords), 不预设任何领域.

无配置 -> 单默认类目, 全部落 default_category.
分类顺序: mapping(文件名 glob) -> keywords(文件名+正文命中数最多) -> default.
"""
import fnmatch
import logging

import yaml

log = logging.getLogger("config")


class Config:
    def __init__(self, data=None):
        data = data or {}
        self.default_category = data.get("default_category") or "unclassified"
        taxonomy = data.get("taxonomy")
        self.categories = list(taxonomy) if taxonomy else [self.default_category]
        self.mapping = data.get("mapping") or {}        # glob -> category
        self.keywords = data.get("keywords") or {}      # category -> [kw]
        valid = set(self.categories) | {self.default_category}
        for pat, cat in list(self.mapping.items()):
            if cat not in valid:
                log.warning("mapping %r -> %r 不在 taxonomy, 改用 default", pat, cat)
        for cat in self.keywords:
            if cat not in valid and cat != self.default_category:
                log.warning("keywords 类目 %r 不在 taxonomy", cat)

    def classify(self, filename, text=""):
        base = filename
        # 1) mapping (glob, 大小写不敏感)
        for pat, cat in self.mapping.items():
            if fnmatch.fnmatch(base, pat) or fnmatch.fnmatch(base.lower(), pat.lower()):
                return cat
        # 2) keywords (命中数最多者胜出)
        hay = (base + "\n" + (text or "")).lower()
        best, best_hits = None, 0
        for cat, kws in self.keywords.items():
            hits = sum(1 for kw in kws if kw and kw.lower() in hay)
            if hits > best_hits:
                best_hits, best = hits, cat
        if best:
            return best
        return self.default_category

    def categories_set(self):
        return set(self.categories) | {self.default_category}


def load_config(path):
    if not path:
        return Config()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        log.warning("配置文件不存在, 用默认配置: %s", path)
        return Config()
    except yaml.YAMLError as ex:
        log.warning("配置文件语法错误, 用默认配置: %s: %s", path, ex)
        return Config()
    return Config(data)
