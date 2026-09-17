#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把官方 Go 1.24 sizeclasses.go 的表数据注入 go/main.go 的 `//@NAME@` 占位符。

存在的理由：68 项 × 5 张表的手工抄录必然出错，且本机无 Go 工具链、编译错误无法暴露。
本脚本同时复核 python/go_sizeclasses.py 里已嵌入的同一批表，确保两侧数据同源一致。

用法：python inject_tables.py [sizeclasses.go 路径]
"""
import re
import sys

# 第 1 个参数可覆盖官方源码路径；默认指向本轮抓取的副本。
# 官方源码：https://raw.githubusercontent.com/golang/go/go1.24.0/src/runtime/sizeclasses.go
SRC = sys.argv[1] if len(sys.argv) > 1 else r".workbuddy\tmp\_r\sizeclasses.go"
GO = r"D:\开发研究\09-语言学习\Golang\内存分配器\go\tables.go"
PY = r"D:\开发研究\09-语言学习\Golang\内存分配器\python\go_sizeclasses.py"

src = open(SRC, encoding="utf-8", errors="replace").read()

rows = re.findall(
    r"^//\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+([\d.]+)%\s+(\d+)\s*$", src, re.M)
assert len(rows) == 67, len(rows)


def parse(name):
    m = re.search(name + r" = \[[^\]]+\](?:uint8|uint16)\{([^}]*)\}", src)
    return [x.strip() for x in m.group(1).split(",") if x.strip()]


tables = {
    "CLASS_TO_SIZE": ("classToSize", "[68]int", parse("class_to_size"), "int"),
    "CLASS_TO_ALLOCNPAGES": ("classToAllocnpages", "[68]int", parse("class_to_allocnpages"),
                             "int"),
    "OFFICIAL_OBJECTS": ("officialObjects", "[67]int", [r[3] for r in rows], "int"),
    "OFFICIAL_TAIL_WASTE": ("officialTailWaste", "[67]int", [r[4] for r in rows], "int"),
    # 百分比放大 100 倍存整数，避免浮点等值断言
    "OFFICIAL_MAX_WASTE_BP": ("officialMaxWasteBP", "[67]int",
                              [str(int(round(float(r[5]) * 100))) for r in rows], "int"),
    "OFFICIAL_SIZE_TO_CLASS128": ("officialSizeToClass128", "[]int",
                                  parse("size_to_class128"), "int"),
}

print("=== 官方表长度 ===")
for k, (_, typ, vals, _) in tables.items():
    print("%-28s %-10s %d" % (k, typ, len(vals)))

# ---------------------------------------------------------------- 注入 Go
# 幂等：首次运行替换 `//@NAME@` 占位符；再次运行则替换已注入的 `var NAME = ...` 单行声明。
go = open(GO, encoding="utf-8").read()
for key, (goname, gotyp, vals, _) in tables.items():
    body = "var %s = %s{%s}" % (goname, gotyp, ", ".join(vals))
    marker = "//@%s@" % key
    if marker in go:
        go = go.replace(marker, body)
        continue
    pat = re.compile(r"^var " + goname + r" = .*$", re.M)
    assert pat.search(go), "neither marker nor injected decl for " + goname
    go = pat.sub(lambda _m: body, go, count=1)
open(GO, "w", encoding="utf-8", newline="\n").write(go)
print("\ngo/tables.go 注入完成，剩余占位符 %d 个"
      % len(re.findall(r"//@[A-Z_0-9]+@", go)))

# ---------------------------------------------------------------- 复核 Python 侧
py = open(PY, encoding="utf-8").read()
bad = []
for key in ("CLASS_TO_SIZE", "CLASS_TO_ALLOCNPAGES", "OFFICIAL_OBJECTS",
            "OFFICIAL_TAIL_WASTE", "OFFICIAL_MAX_WASTE_BP"):
    m = re.search(key + r" = \[([^\]]*)\]", py)
    got = [x.strip() for x in m.group(1).split(",") if x.strip()]
    if got != tables[key][2]:
        bad.append((key, len(got), len(tables[key][2])))
print("python/go_sizeclasses.py 五张表与官方源码一致性:", "OK" if not bad else bad)

# size_to_class128 在 Python 里是折行书写的（每行 21 项）
m = re.search(r"OFFICIAL_SIZE_TO_CLASS128 = \[([^\]]*)\]", py)
got128 = [x.strip() for x in m.group(1).split(",") if x.strip()]
print("python/go_sizeclasses.py size_to_class128 一致性:",
      "OK" if got128 == tables["OFFICIAL_SIZE_TO_CLASS128"][2] else "MISMATCH")

sys.exit(0 if not bad and got128 == tables["OFFICIAL_SIZE_TO_CLASS128"][2] else 1)
