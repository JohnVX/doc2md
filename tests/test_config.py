"""config 容错测试: 缺失/语法错 -> 回退默认; 正常 -> 解析分类."""
import os

from tests._runner import assert_eq, assert_true, cleanup, make_tmp_input

from doc2md.config import Config, load_config

CASES = []


def test_no_config_is_default():
    c = load_config(None)
    assert_eq(c.default_category, "unclassified")
    assert_eq(c.categories, ["unclassified"])


CASES.append(("no_config_is_default", test_no_config_is_default))


def test_missing_config_fallback():
    c = load_config("/tmp/definitely_not_exist_xyz.yaml")
    assert_eq(c.default_category, "unclassified")
    assert_true("unclassified" in c.categories_set())


CASES.append(("missing_config_fallback", test_missing_config_fallback))


def test_bad_yaml_fallback():
    tmp = make_tmp_input({"bad.yaml": "taxonomy:\n  - a\n  bad: [unclosed\n"})
    c = load_config(os.path.join(tmp, "bad.yaml"))
    assert_eq(c.default_category, "unclassified")
    cleanup(tmp)


CASES.append(("bad_yaml_fallback", test_bad_yaml_fallback))


def test_real_config_classify():
    cfg = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "config.example.yaml")
    c = load_config(cfg)
    assert_true("architecture" in c.categories_set())
    assert_true("audit-playbook" in c.categories_set())
    # mapping 精确
    assert_eq(c.classify("npu_exploit.pptx", ""), "attack-surface")
    # keywords 命中
    assert_eq(c.classify("x.docx", "漏洞 利用 攻击"), "attack-surface")
    # 兜底
    assert_eq(c.classify("random.xyz", "nothing here"), "unclassified")


CASES.append(("real_config_classify", test_real_config_classify))
