"""pptx 解析测试: _walk 递归分组图形(回归 MSO_SHAPE_TYPE NameError 致不递归的 bug)."""
from tests._runner import assert_eq

from doc2md.parsers.pptx import _walk

CASES = []


class _Leaf:
    def __init__(self, n):
        self.n = n

    @property
    def shapes(self):
        raise AttributeError  # 非分组: 无 .shapes


class _Group:
    def __init__(self, kids):
        self._kids = kids

    @property
    def shapes(self):
        return self._kids


def test_walk_recurses_nested_groups():
    flat = list(_walk([_Group([_Leaf(1), _Leaf(2), _Group([_Leaf(3)])]), _Leaf(4)]))
    assert_eq(len(flat), 4, "应递归展开到 4 个叶子(含嵌套分组)")
    assert_eq([s.n for s in flat], [1, 2, 3, 4])


CASES.append(("walk_recurses_nested_groups", test_walk_recurses_nested_groups))
