"""有序树的编辑距离（Zhang–Shasha）与**归一化 nTED**。

## 为什么要有它（Q3，2026-09-28）

HiPS（arXiv 2509.00909）与 LongDocBench（arXiv 2608.15064）评估"目录层级恢复"用的都是
**树编辑距离**（HiPS 明确写了 nTED = normalized Tree Edit Distance）。
我们之前只有"父子边集合的 Jaccard"当代理 —— 它对"层级错位"敏感，但**不是他们的口径**，
所以分数没法跟文献比。这个模块把真口径补上：**节点重命名 / 增删 / 移动都按 1 计费**，
最后除以 `(|T1| + |T2|)` 归一（与 HiPS 的 nTED 口径一致）。

## 算法

Zhang–Shasha 的有序树编辑距离：先算"森林距离"，再用**关键根**（keyroot）把树拆开。
节点按**后序遍历**编号；`lmd[i]` 是节点 `i` 的最左叶子后代。
成本：`delete = insert = rename = 1`（标签不同才 rename）。

## 输入怎么给

调用方给两棵**已归一化标签**的有序树：
`{"label": str, "children": [ ... ]}`。
标签的口径由调用方定（我们这里用"剥掉编号的标题"，见 `tools/authority_chain.py`）。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Node:
    label: str
    children: list["Node"] = field(default_factory=list)


def _postorder(root: Node) -> list[Node]:
    """后序遍历（父在子之后）—— Zhang–Shasha 要求的下标顺序。"""
    out: list[Node] = []

    def walk(node: Node) -> None:
        for child in node.children:
            walk(child)
        out.append(node)

    walk(root)
    return out


def _leftmost_leaves(order: list[Node]) -> dict[int, int]:
    """每个节点的**最左叶子后代**下标（1-based，和论文一致）。"""
    index = {id(node): i + 1 for i, node in enumerate(order)}
    lmd: dict[int, int] = {}
    for node in order:
        child = node.children[0] if node.children else None
        while child is not None and child.children:
            child = child.children[0]
        lmd[index[id(node)]] = index[id(child)] if child is not None else index[id(node)]
    return lmd


def tree_edit_distance(left: Node, right: Node) -> int:
    """有序树编辑距离（插入/删除/重命名各 1）。"""
    l_order, r_order = _postorder(left), _postorder(right)
    l_idx = {id(n): i + 1 for i, n in enumerate(l_order)}
    r_idx = {id(n): i + 1 for i, n in enumerate(r_order)}
    l_lmd, r_lmd = _leftmost_leaves(l_order), _leftmost_leaves(r_order)
    n, m = len(l_order), len(r_order)

    # `treedist[i][j]`：以 i、j 为根的**子树**距离
    treedist = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            li, lj = l_lmd[i], r_lmd[j]
            rows, cols = i - li + 2, j - lj + 2
            fd = [[0] * cols for _ in range(rows)]
            for x in range(1, rows):
                fd[x][0] = fd[x - 1][0] + 1
            for y in range(1, cols):
                fd[0][y] = fd[0][y - 1] + 1
            for x in range(1, rows):
                for y in range(1, cols):
                    if l_lmd[li + x - 1] == li and r_lmd[lj + y - 1] == lj:
                        cost = 0 if l_order[li + x - 2].label == r_order[lj + y - 2].label else 1
                        fd[x][y] = min(fd[x - 1][y] + 1, fd[x][y - 1] + 1,
                                       fd[x - 1][y - 1] + cost)
                        treedist[li + x - 1][lj + y - 1] = fd[x][y]
                    else:
                        fd[x][y] = min(fd[x - 1][y] + 1, fd[x][y - 1] + 1,
                                       fd[x - 1][y - 1]
                                       + treedist[li + x - 1][lj + y - 1])
            treedist[i][j] = fd[rows - 1][cols - 1]
    return treedist[n][m]


def nted(left: Node, right: Node) -> float:
    """**归一化**树编辑距离：`ted / (|T1| + |T2|)`（0 = 完全一致，1 = 毫无共同结构）。"""
    size = len(_postorder(left)) + len(_postorder(right))
    if size == 0:
        return 0.0
    return tree_edit_distance(left, right) / size


def build_tree(seq: list[tuple[str, int]]) -> Node:
    """`[(label, level)]`（按文档顺序）→ 有序树。层级用栈挂父，根是虚拟节点。"""
    root = Node(label="<root>")
    stack: list[tuple[Node, int]] = [(root, 0)]
    for label, level in seq:
        while stack and stack[-1][1] >= level:
            stack.pop()
        parent = stack[-1][0] if stack else root
        node = Node(label=label)
        parent.children.append(node)
        stack.append((node, level))
    return root


__all__ = ["Node", "build_tree", "nted", "tree_edit_distance"]
