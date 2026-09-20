# -*- coding: utf-8 -*-
"""tfplugin 自检：全部基于实际读过的官方原文口径。"""
from tfplugin import (parse_version, satisfies, cli_protocol_majors, negotiate,
                      select_version, select_with_protocol, handshake, GOOD_RPCS)

N = 0
FAIL = []


def check(label, cond, detail=""):
    global N
    N += 1
    if not cond:
        FAIL.append("%s  %s" % (label, detail))


# ---- 1. 版本约束 ----
check("A1 ~> 允许最右分量递增(1.0.4→1.0.9 可以)", satisfies("1.0.9", "~> 1.0.4"))
check("A2 ~> 1.0.4 挡住 1.1.0", not satisfies("1.1.0", "~> 1.0.4"))
check("A3 ~> 1.0.4 挡住 1.0.3", not satisfies("1.0.3", "~> 1.0.4"))
check("A4 ~> 1.0 允许 1.9.0", satisfies("1.9.0", "~> 1.0"))
check("A5 ~> 1.0 挡住 2.0.0", not satisfies("2.0.0", "~> 1.0"))
check("A6 >= 下限", satisfies("3.2.0", ">= 1.0") and not satisfies("0.9.9", ">= 1.0"))
check("A7 逗号分隔是「与」", satisfies("1.5.0", ">= 1.0, < 2.0")
      and not satisfies("2.0.0", ">= 1.0, < 2.0"))
check("A8 != 生效", not satisfies("1.2.3", "!= 1.2.3"))
check("A9 = 精确", satisfies("1.2.3", "= 1.2.3") and not satisfies("1.2.4", "= 1.2.3"))
check("A10 元组比较 1.0 < 1.0.1", parse_version("1.0") < parse_version("1.0.1"))

# ---- 2. 协议主版本兼容性 ----
check("B1 CLI 1.0 支持 v5 与 v6", cli_protocol_majors("1.0") == {5, 6})
check("B2 CLI 1.9 支持 v5 与 v6", cli_protocol_majors("1.9.8") == {5, 6})
check("B3 CLI 0.12 只支持 v5", cli_protocol_majors("0.12") == {5})
check("B4 CLI 0.11 一个都不支持", cli_protocol_majors("0.11") == set())

# ---- 3. 协商 ----
check("C1 双方都支持 v6 时取 6", negotiate((6, 3), [(5, 2), (6, 1)])[0] == 6)
check("C2 插件只有 v5 时降级到 5", negotiate((6, 3), [(5, 2)]) == (5, 2),
      negotiate((6, 3), [(5, 2)]))
check("C3 次版本取双方较小(叠加性口径)", negotiate((6, 3), [(6, 5)]) == (6, 3))
try:
    negotiate((6, 0), [(7, 0)])
    check("C4 无共同主版本报错", False, "未抛错")
except ValueError as e:
    check("C4 无共同主版本报错", "incompatible" in str(e), str(e))
check("C5 v6 相对 v5 多了嵌套属性等能力（本模型只判主版本）",
      negotiate((6, 0), [(5, 9), (6, 0)])[0] == 6)

# ---- 4. 版本选择三条规则 ----
# 规则 2：已安装里有可接受的 → 用已安装里最新的，即使 registry 有更新的可接受版本
v, src = select_version(">= 1.0", ["1.2.0", "1.5.0"], [("2.0.0", 6), ("2.1.0", 6)])
check("D1 已安装优先且取最新", (v, src) == ("1.5.0", "installed"), (v, src))
# 规则 3：没有可接受的已安装 → 从 registry 下载最新可接受
v, src = select_version(">= 1.0", [], [("2.0.0", 6), ("2.1.0", 6)])
check("D2 无已安装时取 registry 最新", (v, src) == ("2.1.0", "registry"), (v, src))
# 规则 1：lock file 存在且满足约束 → 一律遵守，即使已安装/registry 都有更新的
v, src = select_version(">= 1.0", ["1.9.0"], [("2.1.0", 6)], lock="1.4.0")
check("D3 lock 优先于一切", (v, src) == ("1.4.0", "locked"), (v, src))
# lock 不满足约束时（例如约束被收紧）退回正常流程
v, src = select_version(">= 2.0", ["1.9.0"], [("2.1.0", 6)], lock="1.4.0")
check("D4 lock 不满足约束则失效", (v, src) == ("2.1.0", "registry"), (v, src))
# 规则 4：都没有 → 失败
v, src = select_version(">= 3.0", ["1.0.0"], [("2.0.0", 6)])
check("D5 都找不到则 failed", (v, src) == (None, "failed"), (v, src))
# 约束不满足的已安装版本不算「可接受」
v, src = select_version(">= 2.0", ["1.5.0"], [("2.1.0", 6), ("2.2.0", 6)])
check("D6 不可接受的已安装被忽略", (v, src) == ("2.2.0", "registry"), (v, src))

# ---- 5. 协议版本参与 registry 筛选 ----
reg = [("1.0.0", 5), ("2.0.0", 6), ("3.0.0", 6)]
v, src = select_with_protocol(">= 1.0", [], reg, "1.9.8")
check("E1 CLI 1.x 能看见 v6 插件", (v, src) == ("3.0.0", "registry"), (v, src))
v, src = select_with_protocol(">= 1.0", [], reg, "0.12")
check("E2 CLI 0.12 只能看见 v5 插件", (v, src) == ("1.0.0", "registry"), (v, src))
v, src = select_with_protocol(">= 2.5", [], reg, "1.9.8")
check("E3 约束与协议共同作用", (v, src) == ("3.0.0", "registry"), (v, src))

# ---- 6. go-plugin 握手 ----
ok, why = handshake("TF_PLUGIN_MAGIC_COOKIE=d602bf8f\n1|6|unix|/tmp/p\n",
                    "TF_PLUGIN_MAGIC_COOKIE", "d602bf8f", 6, 6)
check("F1 cookie 与协议都对则通过", ok and why == "ok", why)
ok, why = handshake("some random binary output\n", "TF_PLUGIN_MAGIC_COOKIE",
                    "d602bf8f", 6, 6)
check("F2 cookie 不匹配拦下非插件二进制", (not ok) and "magic cookie" in why, why)
ok, why = handshake("TF_PLUGIN_MAGIC_COOKIE=d602bf8f\n", "TF_PLUGIN_MAGIC_COOKIE",
                    "d602bf8f", 5, 6)
check("F3 协议版本不一致报错", (not ok) and "incompatible" in why, why)

# ---- 7. 其它 ----
check("G1 Provider 的 8 个核心 RPC 全在表内",
      len(GOOD_RPCS) == 8 and "PlanResourceChange" in GOOD_RPCS
      and "ImportResourceState" in GOOD_RPCS)
check("G2 CLI 只声明 v5 时不会协商出 v6",
      negotiate((5, 0), [(5, 0), (6, 0)])[0] == 5,
      negotiate((5, 0), [(5, 0), (6, 0)]))

print("checks=%d fail=%d" % (N, len(FAIL)))
for f in FAIL:
    print("  FAIL", f)
