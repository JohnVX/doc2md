"""大文件测试: 超过阈值(50MB)应给 WARNING 且正常解析."""
import os

from tests._runner import (assert_eq, assert_true, capture_logs, cleanup,
                            find_mds, make_tmp_input, make_tmp_output)

from doc2md import pipeline

CASES = []


def test_large_file_warns():
    in_dir = make_tmp_input()
    # 单行 ~53MB (阈值 50MB): 行数=1 避免 clean_text 建超长列表占爆内存, 仍触发大文件警告
    with open(os.path.join(in_dir, "big.md"), "w", encoding="utf-8") as f:
        f.write("a" * 53000000)
    out = make_tmp_output()
    with capture_logs("pipeline") as cap:
        p, _, e = pipeline.run(in_dir, out)
    assert_true(cap.has("大文件"), "应有大文件警告")
    assert_eq(e, 0)
    assert_eq(p, 1)
    assert_true(len(find_mds(out)) >= 1)
    cleanup(in_dir, out)


CASES.append(("large_file_warns", test_large_file_warns))
