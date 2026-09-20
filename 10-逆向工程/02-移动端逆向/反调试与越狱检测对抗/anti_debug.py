"""反调试 / Root / 越狱检测与绕过的分层模型。

判据取自 OWASP MASTG（已读原文）：
  - MASTG-TEST-0046（Android Anti-Debugging Detection）的绕过手段与有效性判据：
      * 把反调试逻辑整体 patch 成 NOP；
      * 用 Frida / Xposed 在 Java 层与 native 层 hook，篡改 isDebuggable、
        isDebuggerConnected 这类函数的返回值；
      * 改环境（Android 是开放环境，可直接改系统，推翻开发者做检测时的假设）。
      * 有效性判据：jdb 与基于 ptrace 的调试器附加会失败或让 App 终止；
        多种检测手段**分散**在代码各处（而不是集中在一个函数里）；
        检测机制分布在**多个 API 层**（Java、native 库函数、汇编/系统调用）。
  - MASTG-TEST-0354（Runtime Use of Hook Detection Techniques）列出的敏感 API：
      SecItemCopyMatching / SecItemAdd / SecItemUpdate、SecKeyCreateSignature、
      SecKeyCreateDecryptedData、CCCrypt、LAContext.evaluatePolicy、
      LAContext.evaluateAccessControl、URLSession 的 dataTask / uploadTask /
      downloadTask、URLSessionTask.resume。
  - MASTG-TEST-0240 / 0241（Jailbreak Detection in Code / Runtime Use）描述的
    越狱检测形态：检查第三方应用商店（Sileo、Zebra 等）或"表明设备已越狱的
    特定文件/目录"是否存在。
"""

# ---------- 三层 API（MASTG 原文：Java、native library functions、assembler/system calls） ----------

LAYER_JAVA = "java"
LAYER_NATIVE = "native"
LAYER_SYSCALL = "syscall"

LAYERS = (LAYER_JAVA, LAYER_NATIVE, LAYER_SYSCALL)

# 检查名 -> 所在层
CHECK_LAYER = {
    # Java 层
    "debug_is_debugger_connected": LAYER_JAVA,
    "app_info_flag_debuggable": LAYER_JAVA,
    "root_su_binary": LAYER_JAVA,
    "root_package_present": LAYER_JAVA,
    "jailbreak_third_party_store": LAYER_JAVA,
    "hook_secitem_copy_matching": LAYER_JAVA,
    # native 层
    "native_tracer_pid": LAYER_NATIVE,
    "native_frida_maps": LAYER_NATIVE,
    "native_frida_port": LAYER_NATIVE,
    "native_jailbreak_files": LAYER_NATIVE,
    "hook_cccrypt": LAYER_NATIVE,
    # 系统调用层
    "syscall_ptrace_traceme": LAYER_SYSCALL,
    "syscall_fork_denied": LAYER_SYSCALL,
}

# MASTG-TEST-0354 列出的敏感 API（hook 它们能直接拿到密钥/凭据/明文）
SENSITIVE_HOOK_TARGETS = (
    "SecItemCopyMatching", "SecItemAdd", "SecItemUpdate",
    "SecKeyCreateSignature", "SecKeyCreateDecryptedData", "CCCrypt",
    "LAContext.evaluatePolicy", "LAContext.evaluateAccessControl",
    "URLSession.dataTask", "URLSession.uploadTask",
    "URLSession.downloadTask", "URLSessionTask.resume",
)

FRIDA_DEFAULT_PORT = 27047


class Device(object):
    """被检测的环境（模拟器）。"""

    def __init__(self, tracer_pid=0, debuggable=False, debugger_attached=False,
                 su_binary=False, root_packages=(), frida_port_open=False,
                 maps=(), jailbreak_files=(), third_party_stores=(),
                 hooked=(), forked_pid=-1):
        self.tracer_pid = tracer_pid
        self.debuggable = debuggable
        self.debugger_attached = debugger_attached
        self.su_binary = su_binary
        self.root_packages = tuple(root_packages)
        self.frida_port_open = frida_port_open
        self.maps = tuple(maps)
        self.jailbreak_files = tuple(jailbreak_files)
        self.third_party_stores = tuple(third_party_stores)
        self.hooked = tuple(hooked)
        self.forked_pid = forked_pid        # -1 表示 fork 失败（iOS 沙箱内常见）


def check(device, name):
    """单个检查的实现；返回 True 表示"检出异常"。"""
    if name == "debug_is_debugger_connected":
        return device.debugger_attached
    if name == "app_info_flag_debuggable":
        return device.debuggable
    if name == "root_su_binary":
        return device.su_binary
    if name == "root_package_present":
        return bool(device.root_packages)
    if name == "jailbreak_third_party_store":
        return bool(device.third_party_stores)
    if name == "hook_secitem_copy_matching":
        return "SecItemCopyMatching" in device.hooked
    if name == "native_tracer_pid":
        return device.tracer_pid != 0
    if name == "native_frida_maps":
        return any("frida" in m or "gadget" in m for m in device.maps)
    if name == "native_frida_port":
        return device.frida_port_open
    if name == "native_jailbreak_files":
        return bool(device.jailbreak_files)
    if name == "hook_cccrypt":
        return "CCCrypt" in device.hooked
    if name == "syscall_ptrace_traceme":
        # PTRACE_TRACEME 自我附加：已被附加时再附加会失败
        return device.tracer_pid != 0 or device.debugger_attached
    if name == "syscall_fork_denied":
        # iOS 沙箱里 fork 会失败；越狱/非沙箱环境下能成功（pid > 0）
        return device.forked_pid > 0
    raise ValueError("未知检查 " + name)


class Detector(object):
    """一组检查 + 组合策略。"""

    def __init__(self, names, mode="any", threshold=1):
        unknown = [n for n in names if n not in CHECK_LAYER]
        if unknown:
            raise ValueError("未登记的检查 " + repr(unknown))
        if mode not in ("any", "all", "threshold"):
            raise ValueError("mode 只能是 any / all / threshold")
        self.names = tuple(names)
        self.mode = mode
        self.threshold = threshold

    def signals(self, device):
        return {n: check(device, n) for n in self.names}

    def fired(self, device):
        return [n for n in self.names if check(device, n)]

    def verdict(self, device):
        s = self.signals(device)
        hit = sum(1 for v in s.values() if v)
        if self.mode == "any":
            return hit > 0
        if self.mode == "all":
            return hit == len(self.names)
        return hit >= self.threshold

    def layers(self):
        return sorted({CHECK_LAYER[n] for n in self.names})


class Bypass(object):
    """攻击者侧：patch / hook / 改环境三种手段。"""

    def __init__(self, detector):
        self.detector = detector
        self.patched = set()          # NOP 掉或 hook 掉的检查（一律返回 False）
        self.env_overrides = {}       # 改环境：直接改底层条件

    def patch(self, name):
        if name not in CHECK_LAYER:
            raise ValueError("未登记的检查 " + name)
        self.patched.add(name)

    def patch_layer(self, layer):
        for n, l in CHECK_LAYER.items():
            if l == layer:
                self.patched.add(n)

    def override(self, attr, value):
        self.env_overrides[attr] = value

    def signals(self, device):
        dev = self._apply_env(device)
        out = {}
        for n in self.detector.names:
            out[n] = False if n in self.patched else check(dev, n)
        return out

    def verdict(self, device):
        s = self.signals(device)
        hit = sum(1 for v in s.values() if v)
        if self.detector.mode == "any":
            return hit > 0
        if self.detector.mode == "all":
            return hit == len(self.detector.names)
        return hit >= self.detector.threshold

    def remaining_layers(self):
        """还没被覆盖掉的层 —— 决定绕过是否成功。"""
        live = [n for n in self.detector.names if n not in self.patched]
        return sorted({CHECK_LAYER[n] for n in live})

    def _apply_env(self, device):
        if not self.env_overrides:
            return device
        d = Device(**device.__dict__)
        for k, v in self.env_overrides.items():
            setattr(d, k, v)
        return d
