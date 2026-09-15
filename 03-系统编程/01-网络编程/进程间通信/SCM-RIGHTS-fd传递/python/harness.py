#!/usr/bin/env python3
"""自检脚手架：计数器 + check() + 两个构造小工具（从 main.py 拆出）。

拆出来是因为 main.py 逼近 300 行上限（_docs/OPTIMIZATION.md §1.1），
而且这套脚手架与 C 版本里的 CHECK 宏是同一个角色。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scm_model import FdTable, OpenFileDescription, UnixSocket  # noqa: E402

OK = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  [ok]   {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


def open_file(payload: bytes) -> OpenFileDescription:
    """新开一个文件 OFD。refcount 从 0 起 —— 引用只在 **install 进某张 fd 表**
    或被 sendmsg 挂进队列时产生，这样引用计数才有办法被断言检验。"""
    ofd = OpenFileDescription("file", payload)
    ofd.refcount = 0
    return ofd


def pair() -> tuple[UnixSocket, UnixSocket]:
    a = UnixSocket("stream")
    b = UnixSocket("stream", peer=a)
    return a, b


def summary() -> int:
    """打印结果并返回进程退出码。

    计数器是本模块的全局变量，所以必须由本模块来报告 —— 如果 main.py 写
    `from harness import OK, FAIL`，那只是把当时的**值 0** 绑到 main 的命名
    空间，check() 之后 main.py 里读到的仍是 0（这是个很容易踩的坑）。
    """
    print()
    print(f"===== 断言结果: {OK}/{OK + FAIL} 通过，{FAIL} 失败 =====")
    return 1 if FAIL else 0

