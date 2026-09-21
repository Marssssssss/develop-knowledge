"""BinPRE §3.4 —— 原子语义检测器库（5 种语义类型 + 6 种语义功能）。

每个字段只看两样东西：

- `I(f)`：访问该字段的**指令算子序列**（比较？算术/位运算？是不是 mov 系列？
  有没有循环？是不是跳转条件？）
- `V(f)`：该字段的**取值**（是不是像文件名？是不是和某个循环输出相等？）

Table 2 把判据写成「当且仅当」的规则，本文件就是那张表的可执行版本。

术语：`functional operation` = 算子不属于 mov 系列的指令。

运行：python main.py
"""

from __future__ import annotations

# mov 系列：不算 functional operation
MOV_OPS = frozenset({"mov", "movzx", "movsx", "lea", "push", "pop"})
# 比较类：是 functional operation，但属于「判据本身」——Static 的「无额外 functional
# 操作」指的是除比较之外没有别的，所以要把 cmp/test 单独摘出来。
CMP_OPS = frozenset({"cmp", "test"})

TYPES = ("Static", "Integer", "Group", "Bytes", "String")
FUNCTIONS = ("Command", "Length", "Delim", "Checksum", "Filename", "Aligned")


class Trace:
    """一个字段的执行轨迹摘要（对应论文的 I(f) 与 V(f)）。"""

    def __init__(self, name, ops=(), cmp_consts=(), cmp_true=False,
                 cmp_multi_consecutive=False, cmp_loop_output=False,
                 cmp_switch_consts=(), functional=False, arith_or_bit=False,
                 in_loop_same_ops=False, loop_terminator=False,
                 delimits_neighbors=False, jump_on_true=False,
                 lib_api_length=False, ptr_inc_counter_dec=False,
                 filename_like=False, bytes_all_same_struct=False,
                 consecutive_cmp_same_const=False):
        self.name = name
        self.ops = tuple(ops)
        self.cmp_consts = tuple(cmp_consts)
        self.cmp_true = cmp_true
        self.cmp_multi_consecutive = cmp_multi_consecutive
        self.cmp_loop_output = cmp_loop_output
        self.cmp_switch_consts = tuple(cmp_switch_consts)
        self.functional = functional or any(o not in MOV_OPS for o in self.ops)
        # Static 判据里的「no additional functional operations」= 除比较之外没有别的
        self.non_cmp_functional = functional or any(
            o not in MOV_OPS and o not in CMP_OPS for o in self.ops)
        self.arith_or_bit = arith_or_bit
        self.in_loop_same_ops = in_loop_same_ops
        self.loop_terminator = loop_terminator
        self.delimits_neighbors = delimits_neighbors
        self.jump_on_true = jump_on_true
        self.lib_api_length = lib_api_length
        self.ptr_inc_counter_dec = ptr_inc_counter_dec
        self.filename_like = filename_like
        self.bytes_all_same_struct = bytes_all_same_struct
        self.consecutive_cmp_same_const = consecutive_cmp_same_const


# ---------------------------------------------------------------- 语义类型

def detect_type(t):
    """返回命中的语义类型集合（论文 Table 2 上半部分）。"""
    hits = set()
    # Static：与固定值比较且结果为真，且没有别的 functional 操作
    if t.cmp_true and t.cmp_consts and not t.non_cmp_functional:
        hits.add("Static")
    # Integer：涉及算术/位运算，或与多个连续值比较
    if t.arith_or_bit or t.cmp_multi_consecutive:
        hits.add("Integer")
    # Group：通过条件分支与多个不同常量比较
    if len(set(t.cmp_switch_consts)) >= 2:
        hits.add("Group")
    # Bytes：字段所有字节在同一循环内被同样的操作访问
    if t.in_loop_same_ops and t.bytes_all_same_struct:
        hits.add("Bytes")
    # String：连续字节与同一个常量（分隔符）比较 + 共享循环内操作
    if t.in_loop_same_ops and t.consecutive_cmp_same_const:
        hits.add("String")
    return hits


# ---------------------------------------------------------------- 语义功能

def detect_function(t):
    """返回命中的语义功能集合（论文 Table 2 下半部分）。"""
    hits = set()
    # Command：与固定值比较为真，且为真时立即触发跳转
    if t.cmp_true and t.jump_on_true:
        hits.add("Command")
    # Length：循环终止条件 / 被库 API 取用 / 指针递增与计数器递减
    if t.loop_terminator or t.lib_api_length or t.ptr_inc_counter_dec:
        hits.add("Length")
    # Delim：循环终止条件 + 分隔相邻字段
    if t.loop_terminator and t.delimits_neighbors:
        hits.add("Delim")
    # Checksum：与「对多个连续字节迭代」的输出比较
    if t.cmp_loop_output:
        hits.add("Checksum")
    # Filename：内容符合常见文件命名约定
    if t.filename_like:
        hits.add("Filename")
    # Aligned：完全没有 functional 操作
    if not t.functional:
        hits.add("Aligned")
    return hits


def infer(t):
    """返回 (类型集合, 功能集合)。论文：每个字段必有类型，功能可有可无。"""
    return detect_type(t), detect_function(t)


# ---------------------------------------------------------------- 演示

def _example3():
    """论文 Example-3：Figure 5 / Listing 2 的 f21,22。

    第 5-6 行 `shl / or` 是位运算 -> Integer；
    第 7-16 行与「对连续字节迭代的循环输出」比较 -> Checksum。
    """
    return Trace("f21,22", ops=("movzx", "shl", "or", "cmp"),
                 arith_or_bit=True, cmp_loop_output=True)


def demo():
    cases = [
        ("Protocol Version ID", Trace("ver", ops=("cmp",), cmp_consts=(0x03,),
                                      cmp_true=True)),
        ("填充对齐字段", Trace("pad", ops=("mov",))),
        ("命令码 f7", Trace("cmd", ops=("cmp",), cmp_consts=(0x03,),
                            cmp_true=True, jump_on_true=True)),
        ("长度字段", Trace("len", ops=("movzx", "cmp"), loop_terminator=True,
                           functional=True)),
        ("校验和 f21,22", _example3()),
        ("路径字段", Trace("path", ops=("mov",), filename_like=True)),
    ]
    for label, t in cases:
        ty, fn = infer(t)
        print("%-18s type=%-24s function=%s"
              % (label, sorted(ty) or "-", sorted(fn) or "-"))


if __name__ == "__main__":
    demo()
