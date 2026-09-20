"""反调试 / Root / 越狱检测自检：判据取自 MASTG 原文（见 anti_debug.py 顶部）。

运行：python selfcheck_anti.py
"""

from anti_debug import (
    LAYER_JAVA, LAYER_NATIVE, LAYER_SYSCALL, LAYERS, CHECK_LAYER,
    SENSITIVE_HOOK_TARGETS, FRIDA_DEFAULT_PORT,
    Device, check, Detector, Bypass,
)

PASS = [0]


def eq(a, b, label):
    assert a == b, "FAILED: %s (期望 %r 实际 %r)" % (label, b, a)
    PASS[0] += 1


def ok(cond, label):
    assert cond, "FAILED: " + label
    PASS[0] += 1


# ---------- 1. 三层 API（MASTG-TEST-0046 有效性判据） ----------

eq(LAYERS, ("java", "native", "syscall"), "三个 API 层")
for name in CHECK_LAYER:
    ok(CHECK_LAYER[name] in LAYERS, "%s 的层已登记" % name)
ok(any(l == LAYER_JAVA for l in CHECK_LAYER.values()), "有 Java 层检查")
ok(any(l == LAYER_NATIVE for l in CHECK_LAYER.values()), "有 native 层检查")
ok(any(l == LAYER_SYSCALL for l in CHECK_LAYER.values()), "有系统调用层检查")

# ---------- 2. Frida 默认端口与敏感 API 清单 ----------

eq(FRIDA_DEFAULT_PORT, 27047, "Frida 默认监听端口")
eq(len(SENSITIVE_HOOK_TARGETS), 12, "MASTG-TEST-0354 列出 12 个敏感 API")
for api in ("SecItemCopyMatching", "SecItemAdd", "SecItemUpdate",
            "SecKeyCreateSignature", "SecKeyCreateDecryptedData", "CCCrypt",
            "LAContext.evaluatePolicy", "LAContext.evaluateAccessControl",
            "URLSession.dataTask", "URLSession.uploadTask",
            "URLSession.downloadTask", "URLSessionTask.resume"):
    ok(api in SENSITIVE_HOOK_TARGETS, "敏感 API 清单含 %s" % api)

# ---------- 3. 干净设备：所有检查都不响 ----------

clean = Device()
for name in CHECK_LAYER:
    eq(check(clean, name), False, "干净设备上 %s 不响" % name)

# ---------- 4. 各检查的触发条件 ----------

eq(check(Device(debugger_attached=True), "debug_is_debugger_connected"), True,
   "debugger 已附加 → isDebuggerConnected 响")
eq(check(Device(debuggable=True), "app_info_flag_debuggable"), True,
   "FLAG_DEBUGGABLE → 清单检查响")
eq(check(Device(su_binary=True), "root_su_binary"), True, "存在 su → root 检查响")
eq(check(Device(root_packages=("com.topjohnwu.magisk",)), "root_package_present"), True,
   "root 包存在 → 包名检查响")
eq(check(Device(third_party_stores=("Sileo", "Zebra")), "jailbreak_third_party_store"), True,
   "第三方商店存在 → 越狱检查响（MASTG 举的就是 Sileo / Zebra）")
eq(check(Device(tracer_pid=1234), "native_tracer_pid"), True, "TracerPid 非 0 → 被跟踪")
eq(check(Device(maps=("/data/local/tmp/frida-agent.so",)), "native_frida_maps"), True,
   "maps 里有 frida-agent → 检出注入")
eq(check(Device(frida_port_open=True), "native_frida_port"), True, "27047 端口开放 → 检出")
eq(check(Device(jailbreak_files=("/bin/bash", "/usr/sbin/sshd")), "native_jailbreak_files"), True,
   "越狱特征文件存在 → 检出")
eq(check(Device(hooked=("SecItemCopyMatching",)), "hook_secitem_copy_matching"), True,
   "SecItemCopyMatching 被 hook → 检出")
eq(check(Device(hooked=("CCCrypt",)), "hook_cccrypt"), True, "CCCrypt 被 hook → 检出")
eq(check(Device(tracer_pid=99), "syscall_ptrace_traceme"), True, "已被附加 → TRACEME 失败")
eq(check(Device(debugger_attached=True), "syscall_ptrace_traceme"), True, "调试器附加 → TRACEME 失败")
eq(check(Device(forked_pid=4321), "syscall_fork_denied"), True, "fork 成功 → 非沙箱/已越狱")
eq(check(Device(forked_pid=-1), "syscall_fork_denied"), False, "fork 失败 → 沙箱内")

hostile = Device(tracer_pid=7, debugger_attached=True, su_binary=True,
                 maps=("frida-agent.so",), frida_port_open=True)

# ---------- 5. 组合策略 ----------

det_any = Detector(["debug_is_debugger_connected", "native_tracer_pid",
                    "syscall_ptrace_traceme"], mode="any")
det_all = Detector(["debug_is_debugger_connected", "native_tracer_pid",
                    "syscall_ptrace_traceme"], mode="all")
# 阈值模式要用**相互独立**的事实，否则同一根因会一次点亮多条
det_thr = Detector(["debug_is_debugger_connected", "native_frida_maps",
                    "syscall_fork_denied"], mode="threshold", threshold=2)

eq(det_any.layers(), ["java", "native", "syscall"], "三条检查覆盖三个层")
eq(det_any.verdict(clean), False, "干净设备上 any 模式不报")
eq(det_any.verdict(Device(tracer_pid=7)), True, "any 模式：一条响就报")
eq(det_all.verdict(Device(tracer_pid=7)), False, "all 模式：只响一条不报")
eq(det_all.verdict(Device(tracer_pid=7, debugger_attached=True)), True, "all 模式：全响才报")
eq(det_thr.verdict(Device(debugger_attached=True)), False, "阈值 2：只响一条不报")
eq(det_thr.verdict(Device(debugger_attached=True, maps=("frida-agent.so",))), True,
   "阈值 2：响两条即报")

# 关键：由**同一根因**派生的多条检查不是独立信号。
# tracer_pid 非 0 会同时点亮 native_tracer_pid 与 syscall_ptrace_traceme，
# 所以"看起来两条"其实只需改一个根因就能全灭 —— 这正是要选独立事实做阈值的原因。
eq(det_any.fired(Device(tracer_pid=7)), ["native_tracer_pid", "syscall_ptrace_traceme"],
   "同一根因（tracer_pid 非 0）点亮两条检查")
eq(len(det_any.fired(Device(tracer_pid=7))), 2, "两条检查共享同一个根因")
b_root = Bypass(det_any)
b_root.override("tracer_pid", 0)
eq(sorted(n for n, v in b_root.signals(Device(tracer_pid=7)).items() if v), [],
   "改掉根因后两条一起熄灭")
# 但 hostile 还有第二个根因（debugger_attached），只改 tracer_pid 灭不掉它
eq(sorted(n for n, v in b_root.signals(hostile).items() if v),
   ["debug_is_debugger_connected", "syscall_ptrace_traceme"],
   "还有第二个根因时，只改一个根因不够")

# ---------- 6. 绕过：hook 掉 Java 层远远不够 ----------

hostile = Device(tracer_pid=7, debugger_attached=True, su_binary=True,
                 maps=("frida-agent.so",), frida_port_open=True)

b1 = Bypass(det_any)
b1.patch_layer(LAYER_JAVA)
eq(b1.remaining_layers(), ["native", "syscall"], "hook 掉 Java 层后还剩两层")
eq(b1.verdict(hostile), True, "只 hook Java 层 → 仍然被检出（绕过失败）")

b2 = Bypass(det_any)
b2.patch_layer(LAYER_JAVA)
b2.patch_layer(LAYER_NATIVE)
eq(b2.remaining_layers(), ["syscall"], "再 hook native 层后只剩系统调用层")
eq(b2.verdict(hostile), True, "系统调用层没覆盖 → 仍然被检出")

b3 = Bypass(det_any)
for layer in LAYERS:
    b3.patch_layer(layer)
eq(b3.remaining_layers(), [], "三层全被覆盖")
eq(b3.verdict(hostile), False, "三层全 hook 掉 → 绕过成功")

# ---------------------------------------------- 关键：分散 vs 集中
# MASTG 判据："多种检测手段分散在代码各处，而不是全在一个方法里"
scattered = Detector(["debug_is_debugger_connected", "native_tracer_pid",
                      "syscall_ptrace_traceme"], mode="any")
eq(len(scattered.names), 3, "分散在三个点")
bs = Bypass(scattered)
bs.patch("native_tracer_pid")
eq(bs.verdict(hostile), True, "只 patch 一个点 → 其余两个点照样报")

# ---------- 7. 改环境才是彻底手段 ----------

b4 = Bypass(det_any)
b4.override("tracer_pid", 0)
b4.override("debugger_attached", False)
eq(b4.verdict(hostile), False, "改掉底层条件后，不 hook 任何检查也不再报")
eq(b4.signals(hostile), {"debug_is_debugger_connected": False,
                         "native_tracer_pid": False,
                         "syscall_ptrace_traceme": False}, "三个信号全部为假")
ok(b4.verdict(hostile) is False, "改环境比 hook 更彻底（MASTG：可直接改系统）")

# ---------- 8. 参数校验 ----------

try:
    Detector(["no_such_check"])
    raise AssertionError("本应抛错")
except ValueError:
    PASS[0] += 1
try:
    Detector(["native_tracer_pid"], mode="majority")
    raise AssertionError("本应抛错")
except ValueError:
    PASS[0] += 1

print("PASS %d 项断言全部通过" % PASS[0])
