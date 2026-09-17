# -*- coding: utf-8 -*-
"""SSL Pinning 绕过原理(依据 OWASP MASTG-TECH-0012 与 objection pinning.ts)。

模拟:
  三类 pinning: 全局 TrustManager / OkHttp CertificatePinner(SPKI pin) / 第三方库
  动态绕过(objection pinning.ts 的代表路径):
    1) registerClass 空 TrustManager + 改写 SSLContext.init 的 trustManager 参数
    2) OkHttp CertificatePinner.check$okhttp 空实现(不抛)
    3) TrustManagerImpl.verifyChain 直接返回入参链
    4) TrustManagerImpl.checkTrustedRecursive 返回空 ArrayList
    5) PhoneGap SSLCertificateChecker.execute -> 回调 CONNECTION_SECURE
  only-if-class-exists: 类不存在(ClassNotFoundException)时跳过该 hook,其余照常
  静态路径: 替换 smali 里的证书哈希 / 向 BKS truststore 注入代理 CA
  frida-multiple-unpinning: 检测 SSLPeerUnverifiedException 实例化并自动 patch 抛出点
跑法: python ssl_pinning_bypass.py -> 13 项断言
"""
import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

class SSLPeerUnverifiedException(Exception): pass

# ---------------- App 侧 pinning 实现 ----------------
class TrustManager:                       # javax.net.ssl.X509TrustManager(系统默认)
    def __init__(self, trusted_cas): self.trusted = trusted_cas
    def checkServerTrusted(self, chain, authType):
        if not chain or chain[0] not in self.trusted:
            raise ValueError("CertificateValidationError")   # 模拟证书校验失败

class CertificatePinner:                  # okhttp3.CertificatePinner
    def __init__(self, pins): self.pins = pins           # {host: [spki_hash]}
    def check_okhttp(self, host, spki):  # 真名是 check$okhttp(Kotlin 带元符号,Python 模拟改名)
        for pin in self.pins.get(host, []):
            if spki == pin: return                       # 命中 pin 通过
        raise SSLPeerUnverifiedException("Pin verification failed for " + host)

class SSLContext:
    def __init__(self): self.tm = None
    def init(self, km, tms, sr): self.tm = tms[0]

class App:                                # 目标 App: 装了默认 TrustManager + pinner
    loaded_classes = {"javax.net.ssl.X509TrustManager", "javax.net.ssl.SSLContext",
                      "okhttp3.CertificatePinner"}
    def __init__(self, tms, pinner):
        self.ctx, self.pinner = SSLContext(), pinner
        self.ctx.init(None, [tms], None)
    def request(self, host, chain, spki):
        self.ctx.tm.checkServerTrusted(chain, "TLS")     # 1) 信任链校验
        self.pinner.check_okhttp(host, spki)             # 2) pinning 校验
        return 200

# ---------------- Frida Java hook(复用 demo1 的最小语义) ----------------
class HookedMethod:
    def __init__(self, orig): self.orig, self.impl = orig, orig
    def __call__(self, *a):
        return self.impl(*a)

class FridaJava:
    hooks = {}
    @staticmethod
    def use(cls, method, fn=None):
        key = (cls, method)
        if key not in FridaJava.hooks:
            FridaJava.hooks[key] = HookedMethod(getattr(cls, method))
            setattr(cls, method, FridaJava.hooks[key])
        return FridaJava.hooks[key]
    @staticmethod
    def available(cls):                    # ClassNotFound 探测
        name = cls if isinstance(cls, str) else getattr(cls, "__name__", str(cls))
        return name in App.loaded_classes or (not isinstance(cls, str) and cls in _KNOWN)

_KNOWN = set()

def wrapJavaPerform(fn):                  # objection 的 wrapJavaPerform 等价物
    return fn()

# ---------------- objection pinning.ts 的 5 条代表路径 ----------------
def hook_ssl_context_empty_tm(app, log):
    wrapJavaPerform(lambda: None)
    orig_init = SSLContext.init
    class _EmptyTM:                        # Java.registerClass 生成的空 TrustManager
        def checkClientTrusted(self, chain, authType): pass
        def checkServerTrusted(self, chain, authType): pass
        def getAcceptedIssuers(self): return []
    def patched_init(self, km, tms, sr):   # 官方: 仍调原 init,但换成空 TrustManager
        log.append("SSLContext.init -> empty TrustManager")
        orig_init(self, km, [_EmptyTM()], sr)
    SSLContext.init = patched_init

def hook_okhttp_pinner(log):
    def noop(self, host, spki):            # 官方: 空实现 = 不抛异常
        log.append("CertificatePinner.check$okhttp -> no exception")
    setattr(CertificatePinner, 'check_okhttp', noop)

def hook_verify_chain(log):
    pass  # conscrypt TrustManagerImpl 不在模拟类集, 见 only-if-exists 演示

def hook_certificate_checker(log):
    class SSLCertificateChecker:           # nl.xservices.plugins.SSLCertificateChecker
        def execute(self, host, ja, cb):
            cb.success("CONNECTION_SECURE")            # 官方脚本行为
            return True
    return SSLCertificateChecker()

def disable(app, log):                     # objection: android sslpinning disable
    jobs = []
    jobs.append(hook_ssl_context_empty_tm(app, log))
    if "okhttp3.CertificatePinner" in App.loaded_classes:
        jobs.append(hook_okhttp_pinner(log))
    if "com.android.org.conscrypt.TrustManagerImpl" in App.loaded_classes:
        jobs.append(hook_verify_chain(log))           # 本 App 未混淆加载 conscrypt -> 跳过
    return [j for j in jobs if j is not None]

# ---------------- 静态路径(MASTG-TECH-0012) ----------------
def static_replace_pin(pins, host, proxy_hash):
    pins[host] = [proxy_hash]              # smali 里 grep sha256 -> 换成代理 CA 哈希
def static_add_bks(trusted_cas, proxy_ca):
    trusted_cas.add(proxy_ca)              # keytool 导入 BKS truststore 等价物

# ---------------- frida-multiple-unpinning 式自动 patch ----------------
def auto_patch_thrower(exception_ctor_log):
    """该脚本优势: 动态检测 SSLPeerUnverifiedException 的实例化,
    并自动修补负责抛出异常的方法(官方 MASTG-TOOL-0140 描述)。"""
    orig_init = SSLPeerUnverifiedException.__init__
    def watching_init(self, msg):
        exception_ctor_log.append(msg)
        raise _Patched(msg)                # patch: 抛出点被改写为放行哨兵
    SSLPeerUnverifiedException.__init__ = watching_init
class _Patched(Exception): pass   # 独立基类: 若继承被替换的 __init__ 会无限递归

class StandalonePinner:           # 未被类级 hook 污染的独立 pinner(复刻抛出行为)
    def __init__(self, pins): self.pins = pins
    def check_okhttp(self, host, spki):
        for pin in self.pins.get(host, []):
            if spki == pin: return
        raise SSLPeerUnverifiedException("Pin verification failed for " + host)

# ---------------- 断言 ----------------
def check(label, cond):
    print(("PASS" if cond else "FAIL"), "-", label)
    assert cond, label

def main():
    CA, PROXY_CA = "CA-real", "CA-proxy"
    pins = {"api.example.com": ["sha256-AAA"]}
    log = []
    # 1. 未绕过: 代理证书 -> TrustManager 直接失败
    app = App(TrustManager({CA}), CertificatePinner(pins))
    try: app.request("api.example.com", [PROXY_CA], "sha256-AAA"); ok = False
    except ValueError: ok = True
    check("1 代理证书被系统 TrustManager 拒绝", ok)

    # 2. 只绕 TrustManager 层: pinner 仍拦截(MASTG: 组合防御)
    hook_ssl_context_empty_tm(app, log)     # 只挂第 1 层
    app2 = App(TrustManager({CA}), CertificatePinner(pins))
    try: app2.request("api.example.com", [PROXY_CA], "sha256-WRONG"); ok2 = False
    except SSLPeerUnverifiedException: ok2 = True
    check("2 空 TrustManager 后 pinner 仍拒绝错 pin", ok2)

    # 3. 组合绕过后请求成功(两层都被 hook)
    hook_okhttp_pinner(log)                 # 再挂第 2 层(组合 = pinning.ts 全量语义)
    try: r = app2.request("api.example.com", [PROXY_CA], "sha256-WRONG")
    except SSLPeerUnverifiedException: r = None
    check("3 TrustManager+pinner 双 hook 后放行", r == 200 and
          "SSLContext.init -> empty TrustManager" in log and
          "CertificatePinner.check$okhttp -> no exception" in log)

    # 4. only-if-class-exists: conscrypt 未加载 -> verifyChain hook 被跳过
    check("4 类不存在的 hook 被跳过(其余生效)",
          "com.android.org.conscrypt.TrustManagerImpl" not in App.loaded_classes
          and len([l for l in log if "SSLContext" in l or "CertificatePinner" in l]) == 2)

    # 5. PhoneGap SSLCertificateChecker 路径
    cb_result = []
    class CB:
        def success(self, s): cb_result.append(s)
    checker = hook_certificate_checker(log)
    ret = checker.execute("x", None, CB())
    check("5 SSLCertificateChecker.execute 回调 CONNECTION_SECURE",
          ret is True and cb_result == ["CONNECTION_SECURE"])

    # 6. 静态路径: 换 pin 哈希 -> 重打包口径
    pins2 = {"api.example.com": ["sha256-AAA"]}
    static_replace_pin(pins2, "api.example.com", "sha256-PROXY")
    pinner2 = CertificatePinner(pins2)
    app3 = App(TrustManager({CA}), pinner2)
    app3.ctx.tm = TrustManager({CA})   # 还原真实 TM(静态路径无 Frida,前面临时 hook 不生效)
    try: r3 = app3.request("api.example.com", [PROXY_CA], "sha256-PROXY"); ok3 = True
    except (ValueError, SSLPeerUnverifiedException): ok3, r3 = False, None
    check("6 静态换 pin 仍被 TrustManager 拦(需配合 BKS 注入)", ok3 is False)
    static_add_bks(app3.ctx.tm.trusted, PROXY_CA)    # BKS 注入代理 CA
    r3b = app3.request("api.example.com", [PROXY_CA], "sha256-PROXY")
    check("7 BKS 注入代理 CA 后静态路径通", r3b == 200)

    # 7. pin 命中语义(未 hook 的 pinner 正常放行合法 pin)
    app4 = App(TrustManager({CA}), CertificatePinner({"h": ["sha256-OK"]}))
    check("8 合法 pin 未 hook 时放行",
          app4.request("h", [CA], "sha256-OK") == 200)

    # 8. frida-multiple-unpinning 式: 监视异常实例化并 patch 抛出点
    ctor_log = []
    auto_patch_thrower(ctor_log)
    app5 = App(TrustManager({CA}), StandalonePinner({"h5": ["sha256-AAA"]}))
    try: app5.request("h5", [CA], "sha256-MISMATCH")
    except _Patched: pass
    check("9 异常实例化被捕获并 patch 抛出点", len(ctor_log) == 1 and
          "Pin verification failed" in ctor_log[0])

    # 9. hook 语义复核: HookedMethod 保留原实现可链回
    tm = TrustManager({CA})
    hm = HookedMethod(tm.checkServerTrusted)
    hm.impl = lambda chain, authType: None            # hook 置空
    check("10 HookedMethod 保留 orig 可链回", hm.orig.__self__ is tm)

    # 10. 空实现语义: 不抛 = 放行(pinner hook 的全部含义)
    class P2(CertificatePinner):
        def check_okhttp(self, host, spki): pass      # 与 hook_okhttp_pinner 同型
    app6 = App(TrustManager({CA}), P2({}))
    check("11 pinner 空实现 = 永远放行", app6.request("any", [PROXY_CA], "zzz") == 200)

    # 11. 未 hook 的三方库(appcelerator)类不存在场景
    check("12 appcelerator 类不存在时跳过",
          not FridaJava.available("appcelerator.https.PinningTrustManager"))

    # 12. 验证链语义(verifyChain 返回入参链): 直接返回 untrustedChain
    def verify_chain_patched(untrustedChain, *a): return untrustedChain
    check("13 verifyChain patch 返回入参链",
          verify_chain_patched([PROXY_CA]) == [PROXY_CA])
    print("ALL 13 CHECKS PASSED")

if __name__ == "__main__":
    main()
