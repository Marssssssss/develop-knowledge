# -*- coding: utf-8 -*-
"""flight_protocol.py 自检：逐条对照 ReactFlightClient.js 的行状态机与引用前缀表。"""
import json
import math
from datetime import datetime, timezone

from flight_protocol import (FlightDecoder, Lazy, Chunk, ELEMENT, UNDEFINED,
                             Symbol, ServerRef, encode_row, symbol_for)

ok = 0
fails = []


def check(label, cond, detail=""):
    global ok
    if cond:
        ok += 1
    else:
        fails.append("%s %s" % (label, detail))


def new(payload_bytes):
    d = FlightDecoder()
    d.write(payload_bytes)
    return d


# --- 1. rowID 是十六进制累加 ---
d = new(encode_row(10, "T", '"hi"'))
check("A1 十六进制 rowID", 10 in d.rows, "rows=%r" % (list(d.rows),))
check("A2 payload 正确", d.rows[10][1] == '"hi"')
check("A3 编码器写出 a:", encode_row(10, "T", '"hi"').startswith("a:T"))

# --- 2. 带长度 tag：按「字节」长度读取，可跨任意 chunk 边界 ---
row = encode_row(0, "T", json.dumps({"k": "中文值"}))
b = row.encode("utf-8")
d2 = FlightDecoder()
for piece in (b[:5], b[5:12], b[12:]):
    d2.write(piece)
check("B1 三片段续传后完整重组", d2.model(0) == {"k": "中文值"}, "rows=%r" % (d2.rows,))
check("B2 长度按 UTF-8 字节算", row.split(",")[0].endswith(hex(len(json.dumps({'k': '中文值'}).encode('utf-8')))[2:]),
      row[:20])

# --- 3. 无长度 tag 按换行切分 ---
d3 = new(encode_row(1, "E", "boom", with_length=False))
check("C1 换行行解析", d3.rows.get(1, (0, ""))[1] == "boom", "rows=%r" % (d3.rows,))

# --- 4. 未知 tag 不升级为行头（源码：this was probably part of the data） ---
d4 = new("1:hello:world\n")
check("D1 未知 tag 整段当 payload", d4.rows.get(1, (0, ""))[1] == "hello:world", "rows=%r" % (d4.rows,))
d5 = new("1:rfoo\n")
check("D2 'r' 是合法换行 tag", d5.rows.get(1, (0, ""))[1] == "foo", "rows=%r" % (d5.rows,))

# --- 5. 一个 chunk 内包含多行 ---
# 带长度的行之间没有分隔符，长度就是边界（多余的 \n 会被当成下一行 rowID 数据）
d6 = new(encode_row(0, "T", '"a"') + encode_row(1, "T", '"b"'))
check("E1 一次 write 解出两行", len(d6.rows) == 2 and d6.rows[1][1] == '"b"', "rows=%r" % (d6.rows,))

# --- 6. 引用前缀：特殊值往返 ---
d7 = FlightDecoder()
models = d7._resolve({
    "d": "$D2026-01-01T00:00:00.000Z",
    "big": "$n9007199254740993",
    "nan": "$N",
    "neg0": "$-0",
    "neginf": "$-Infinity",
    "inf": "$I",
    "u": "$u",
    "sym": "$Sshared",
    "esc": "$$notaref",
    "el": "$",
})
check("F1 Date", models["d"] == datetime(2026, 1, 1, tzinfo=timezone.utc), repr(models["d"]))
check("F2 BigInt 超 2^53 不丢精度", models["big"] == 9007199254740993, repr(models["big"]))
check("F3 NaN", models["nan"] != models["nan"])
check("F4 -0 保留符号", models["neg0"] == 0 and math.copysign(1, models["neg0"]) == -1.0)
check("F5 -Infinity", models["neginf"] == -math.inf)
check("F6 Infinity", models["inf"] == math.inf)
check("F7 undefined 哨兵", models["u"] is UNDEFINED)
check("F8 Symbol.for", isinstance(models["sym"], Symbol) and models["sym"] is symbol_for("shared"))
check("F9 $$ 转义", models["esc"] == "$notaref")
check("F10 单个 $ 是 ElementType", models["el"] is ELEMENT)

# --- 7. $L 引用：先到 shell，内容后到（RSC 流式的核心） ---
d8 = FlightDecoder()
d8.write(encode_row(0, "T", json.dumps({"shell": 1, "comments": "$L1"})))
m0 = d8.model(0)
check("G1 shell 已可用", m0["shell"] == 1)
lazy = m0["comments"]
check("G2 引用是惰性占位", isinstance(lazy, Lazy))
check("G3 内容未到时未解析", not lazy.resolved)
d8.write(encode_row(1, "T", json.dumps(["c1", "c2"])))
check("G4 后续行回填同一 chunk", lazy.resolved and lazy.value() == ["c1", "c2"], "v=%r" % (lazy.value() if lazy.resolved else None,))

# --- 8. @ 是 Promise chunk，不是 lazy ---
d9 = FlightDecoder()
d9.write(encode_row(0, "T", json.dumps({"p": "$@2"})))
p = d9.model(0)["p"]
check("H1 返回 chunk 本身", isinstance(p, Chunk) and p.status == "pending")
got = []
p.then(lambda v: got.append(v))
d9.write(encode_row(2, "T", '"resolved"'))
check("H2 chunk 兑现后回调触发", got == ["resolved"], "got=%r" % (got,))

# --- 9. Server Reference / Map / Set / Iterator ---
d10 = FlightDecoder()
d10.write(encode_row(3, "T", json.dumps([["a", 1], ["b", 2]])))
d10.write(encode_row(4, "T", json.dumps(["x", "x", "y"])))
d10.write(encode_row(5, "T", json.dumps([1, 2, 3])))
mm = d10._resolve({"fn": "$hserverFn", "map": "$Q3", "set": "$W4", "it": "$i5"})
check("I1 Server Reference", isinstance(mm["fn"], ServerRef) and mm["fn"].id == "serverFn")
check("I2 Map", mm["map"] == {"a": 1, "b": 2}, repr(mm["map"]))
check("I3 Set 去重", mm["set"] == {"x", "y"}, repr(mm["set"]))
check("I4 Iterator", list(mm["it"]) == [1, 2, 3])

# --- 10. 未到达的引用保持 pending，不伪造空值 ---
d11 = FlightDecoder()
d11.write(encode_row(0, "T", json.dumps({"later": "$L9"})))
lz = d11.model(0)["later"]
check("J1 未到达时 resolved=False", not lz.resolved)
check("J2 未到达时 chunk 已建档", isinstance(lz.chunk, Chunk) and lz.chunk.id == 9)

print("flight_protocol: %d/%d assertions passed" % (ok, ok + len(fails)))
for f in fails:
    print("  FAIL", f)
raise SystemExit(1 if fails else 0)
