# -*- coding: utf-8 -*-
"""Objection 与 Frida 的分层关系(依据 objection 官方 README/Wiki 与 OWASP MASTG-TOOL-0029)。

模拟:
  分层: frida-core(C+QuickJS 注入) -> 语言绑定 -> REPL/工具层
  gadget 模式 vs usb(frida-server) 模式: patchapk 重打包注入 gadget / 直连 server
  REPL 命令分发表 -> agent 命令消息(send/recv) -> job 模型(identifier + addImplementation)
  patchapk 流程: 解码 -> 注入 gadget + loader -> 回编 -> 重签(v1/v2 失效须重签)
  deoptimize: 强制解释执行提高 hook 可靠性(Features 官方口径)
跑法: python objection_frida.py -> 12 项断言
"""
import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ---------------- 分层模型 ----------------
class FridaCore:                          # C 编写的核心: 注入 QuickJS 到目标进程
    def __init__(self): self.sessions = {}
    def inject(self, pid, agent_js):      # attach/spawn 出来的会话
        self.sessions[pid] = {"agent": agent_js, "messages": [], "jobs": []}
        return self.sessions[pid]

class Device:
    def __init__(self, mode, frida_server=False, patched_apk=None):
        self.mode, self.frida_server = mode, frida_server
        self.patched_apk = patched_apk   # gadget 模式须先 patchapk
    def attach(self, core, name):
        if self.mode == "usb":
            if not self.frida_server:
                raise RuntimeError("usb 模式需要 root 设备 + frida-server")
            return core.inject(pid=1000 + hash(name) % 9000, agent_js="agent.bundle.js")
        # gadget 模式: App 启动时自带 gadget,宿主连 "Gadget" 进程
        if not self.patched_apk:
            raise RuntimeError("gadget 模式需要先 patchapk 注入 frida-gadget")
        return core.inject(pid=0, agent_js="gadget://agent.bundle.js")

def patchapk(apk_path, out_path, actions):
    """objection patchapk 流程(语义级): 解码 -> 注入 gadget + loader smali -> 回编 -> 重签。
    返回操作记录; 重打包使原 v1/v2 签名失效,必须用调试证书重签(见 APK签名校验 demo)。"""
    steps = ["decode:apktool d", "inject:libfrida-gadget.so + loader.smali",
             "rebuild:apktool b", "re-sign:debug-key"]
    return steps + actions

# ---------------- agent 侧: 命令处理 + job 模型(来自 pinning.ts 同款结构) ----------------
class Job:
    def __init__(self, ident, name):
        self.identifier, self.name, self.implementations = ident, name, []
    def addImplementation(self, impl):
        if impl is not None:              # 官方: 类不存在时为 undefined,只有真挂上才 add
            self.implementations.append(impl)

class AgentCommands:                      # agent 侧命令注册表(TypeScript 编译进 bundle)
    def __init__(self):
        self.handlers, self.jobs, self._ident = {}, [], 0
        self.register("android-sslpinning-disable", self._sslpinning_disable)
        self.register("android-root-disable", self._root_disable)
        self.register("env", self._env)
        self.register("memory-list-modules", self._memory_list_modules)
    def register(self, cmd, fn): self.handlers[cmd] = fn
    def _identifier(self):
        self._ident += 1; return self._ident
    def _sslpinning_disable(self, args, session):
        job = Job(self._identifier(), "android-sslpinning-disable")
        for hook in ("SSLContext.init", "CertificatePinner.check", "verifyChain",
                     "checkTrustedRecursive", "SSLCertificateChecker.execute"):
            job.addImplementation(None if args.get("skip", {}).get(hook) else hook)
        self.jobs.append(job)
        return {"jobs_added": 1, "hooked": len(job.implementations)}
    def _root_disable(self, args, session):
        job = Job(self._identifier(), "android-root-disable")
        job.addImplementation("RootBeer.checkRoot")   # 官方 Features: bypass RootBeer
        self.jobs.append(job)
        return {"hooked": ["RootBeer.checkRoot"]}
    def _env(self, args, session):
        return {"storage": {"files": "/data/user/0/...", "cache": "/cache/..."}}
    def _memory_list_modules(self, args, session):
        return {"modules": ["libfrida-gadget.so", "libnative-lib.so", "libc.so"]}

# ---------------- REPL 层: 命令分发 ----------------
REPL_ALIASES = {                          # objection REPL 命令 -> agent 命令名
    "android sslpinning disable": "android-sslpinning-disable",
    "android root disable": "android-root-disable",
    "env": "env",
    "memory list modules": "memory-list-modules",
    "android hooking watch class method": None,      # 需参数,此处略
    "android keystore list": None,
}

class ObjectionREPL:
    def __init__(self, session): self.session, self.agent = session, AgentCommands()
    def run(self, cmdline):
        cmd = REPL_ALIASES.get(cmdline)
        if cmd is None:
            return {"error": "unknown or parameterized command: %s" % cmdline}
        return self.agent.handlers[cmd]({}, self.session)   # send()/recv() 消息等价物

# ---------------- 断言 ----------------
def check(label, cond):
    print(("PASS" if cond else "FAIL"), "-", label)
    assert cond, label

def main():
    core = FridaCore()
    # 1. usb 模式: root + frida-server 直连(不需重打包)
    dev = Device("usb", frida_server=True)
    session = dev.attach(core, "Telegram")
    check("1 usb 模式直连 frida-server", session["agent"] == "agent.bundle.js")
    # 2. 非 root 无 server: gadget 模式未 patch -> 报错
    dev2 = Device("gadget", frida_server=False)
    try: dev2.attach(core, "Gadget"); ok = False
    except RuntimeError: ok = True
    check("2 gadget 模式未 patchapk 报错", ok)
    # 3. patchapk 流程完整(注入 gadget + 重签)
    steps = patchapk("app.apk", "app.patched.apk", [])
    check("3 patchapk 四步含注入与重签",
          any("inject" in s for s in steps) and steps[-1].startswith("re-sign"))
    session2 = Device("gadget", patched_apk="app.patched.apk").attach(core, "Gadget")
    check("4 patch 后 gadget 模式可连", session2["agent"].startswith("gadget://"))

    # 4. REPL 命令分发
    repl = ObjectionREPL(session)
    r = repl.run("android sslpinning disable")
    check("5 sslpinning disable 挂 5 条 hook", r["hooked"] == 5)
    r2 = repl.run("env")
    check("6 env 返回存储位置", "storage" in r2)
    r3 = repl.run("memory list modules")
    check("7 memory list 含 gadget 模块", "libfrida-gadget.so" in r3["modules"])
    r4 = repl.run("android root disable")
    check("8 root disable 挂 RootBeer.checkRoot", r4["hooked"] == ["RootBeer.checkRoot"])
    r5 = repl.run("android hooking watch class method")
    check("9 带参命令直接跑报参数错误", "error" in r5)

    # 5. job 模型: only-if-hooked(类不存在 -> None 不入列)
    agent = AgentCommands()
    r6 = agent.handlers["android-sslpinning-disable"](
        {"skip": {"CertificatePinner.check": True, "verifyChain": True}}, None)
    job = agent.jobs[-1]
    check("10 类缺失的 hook 不入 job", r6["hooked"] == 3 and
          "CertificatePinner.check" not in job.implementations)
    check("11 job 含 identifier 与名称", job.identifier == 1 and
          job.name == "android-sslpinning-disable")
    # 6. 分层归属: agent 命令经 frida-core 会话通道
    check("12 REPL 命令走 agent 注册表(经 core 会话)",
          set(ObjectionREPL(session).agent.handlers) >=
          {"android-sslpinning-disable", "android-root-disable", "env"})
    print("ALL 12 CHECKS PASSED")

if __name__ == "__main__":
    main()
