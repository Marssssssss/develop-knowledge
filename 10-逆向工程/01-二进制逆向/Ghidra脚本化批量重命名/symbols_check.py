#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ghidra_symbols.py 的自检:命名规则 / SourceType 优先级 / 重名 / 事务回滚 / 写注释。

运行: python symbols_check.py     退出码 0 表示全部断言通过。
"""

import sys

from ghidra_symbols import (SOURCE_PRIORITY, NamingError, PriorityError, Program,
                            Transaction, check_name, is_default_name, unique_name)


FAIL = []


def check(cond, label, detail=""):
    if not cond:
        FAIL.append(label)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", label,
                           ("  <- " + str(detail)) if detail else ""))
    return cond


def main():
    print("== 1. 命名规则与默认名前缀 ==")
    check(check_name("sub_401000") == "sub_401000", "合法名通过")
    for bad in ("my func", "401000", "a-b", ""):
        try:
            check_name(bad)
            check(False, "非法名 %r 应被拒绝" % bad)
        except NamingError:
            check(True, "非法名 %r 被拒绝(官方:No spaces / 首字符须为字母或下划线)" % bad)
    check(is_default_name("FUN_00401000") and is_default_name("DAT_00402000") and
          is_default_name("LAB_00403000") and is_default_name("SUB_00404000"),
          "FUN_/DAT_/LAB_/SUB_ 都识别为自动生成名")
    check(not is_default_name("parse_config"), "用户自己起的名字不算默认名")

    print("== 2. SourceType 优先级链 ==")
    check(SOURCE_PRIORITY["USER_DEFINED"] > SOURCE_PRIORITY["IMPORTED"] >
          SOURCE_PRIORITY["ANALYSIS"] > SOURCE_PRIORITY["DEFAULT"],
          "USER_DEFINED > IMPORTED > ANALYSIS > DEFAULT(官方 isHigherPriorityThan 说明)")

    print("== 3. 低优先级不得覆盖高优先级 ==")
    p = Program()
    p.create_label(0x1000, "user_named", True, "USER_DEFINED")
    try:
        p.create_label(0x1000, "analysis_named", True, "ANALYSIS")
        check(False, "ANALYSIS 不得覆盖 USER_DEFINED")
    except PriorityError as e:
        check(True, "ANALYSIS 覆盖 USER_DEFINED 被拒绝", str(e)[:46] + "...")
    check(p.primary_symbol(0x1000).name == "user_named", "原符号完好")
    p.create_label(0x2000, "FUN_00402000", True, "DEFAULT")
    p.create_label(0x2000, "imported_name", True, "IMPORTED")
    check(p.primary_symbol(0x2000).name == "imported_name",
          "高优先级(IMPORTED)可以覆盖低优先级(DEFAULT)")

    print("== 4. 重名与 makeUnique ==")
    p.create_label(0x3000, "dup", True, "USER_DEFINED")
    again = p.create_label(0x3000, "dup", True, "USER_DEFINED")
    check(p.create_count == 4, "同一个地址上创建同名标签是幂等的(不新增符号)",
          p.create_count)
    check(again.name == "dup", "重复创建返回既有符号")
    check(unique_name("dup", 0x3000) == "dup_3000",
          "makeUnique 时把地址拼到名字后(官方文档口径)")

    print("== 5. 事务:出错必须整体回滚 ==")
    p2 = Program()
    p2.create_function(0x4000, "FUN_00400000")
    p2.create_function(0x4010, "FUN_00400010")
    before = p2.dump()
    tx = Transaction(p2, "batch rename").begin()
    p2.rename_symbol(0x4000, "FUN_00400000", "parse_input")
    try:
        p2.rename_symbol(0x4010, "FUN_00400010", "bad name")   # 中途出错
        check(False, "非法名应当抛异常")
    except NamingError:
        check(True, "批处理中途遇到非法名 → 抛异常")
    check(p2.function_at(0x4000).name == "parse_input",
          "此时程序里已经留下了半成品(前一条改名已写进去)")
    tx.rollback()
    check(p2.dump() == before, "rollback 后符号表与事务前完全一致 —— 这就是必须用事务的原因")
    tx2 = Transaction(p2, "batch rename").begin()
    p2.rename_symbol(0x4000, "FUN_00400000", "parse_input")
    tx2.commit()
    check(p2.function_at(0x4000).name == "parse_input", "commit 后改名生效")

    print("== 6. setEOLComment ==")
    check(p2.set_eol_comment(0x4000, "entry of parsing") is True, "对函数地址写注释成功")
    check(p2.set_eol_comment(0x9999, "no function") is False, "无函数处写注释失败")

    print("\n结果: %d 项失败" % len(FAIL))
    for x in FAIL:
        print("  FAIL: %s" % x)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
