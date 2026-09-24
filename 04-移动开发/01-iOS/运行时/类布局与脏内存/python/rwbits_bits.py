"""class_data_bits_t：把 class_ro_t / class_rw_t 指针与 3 个 FAST 标志
打包进同一个字。

对照 objc4 `runtime/objc-runtime-new.h` 2364 行起的 `class_data_bits_t`。
"""

from rwbits_const import (RW_REALIZING_OR_FUTURE, AuthFailure, PtrAuth, addr_of,
                          deref)


class Violation(Exception):
    """源码里的 ASSERT 被触发。"""


class ClassDataBits:
    def __init__(self, arch, disc, disable_enforce=False):
        self.arch = arch
        self.ptra = PtrAuth(arch, disc)
        self.disable_enforce = disable_enforce
        self.bits = 0
        self.cas_failures = 0   # 测试钩子：让接下来 N 次 CAS 返回失败
        self.cas_attempts = 0
        self.probe = None       # 测试探针：记录走过哪条 auth 路径

    # ---- 基本存取 ----
    def load(self):
        return self.bits

    def _mark(self, what):
        if self.probe is not None:
            self.probe.append(what)

    def flags(self, bits=None):
        """`static uint32_t flags(uintptr_t bits)`：strip 后用 RO 密钥，
        再 & FAST_DATA_MASK，按偏移 0 读出 uint32。

        源码注释明说 "This intentionally DOES NOT check the signatures"，
        所以被篡改了 PAC 的 bits 照样能读出正确的 flags。
        """
        b = self.bits if bits is None else bits
        stripped = self.ptra.strip(b)
        return deref(stripped & self.arch.data_mask).flags & 0xFFFFFFFF

    def has_rw_pointer(self, bits=None):
        b = self.bits if bits is None else bits
        if self.arch.rw_bit:
            return bool(b & self.arch.rw_bit)
        # 32 位没有 FAST_IS_RW_POINTER，退化为「非空且 flags 里有 RW_REALIZED」
        return b != 0 and (self.flags(b) & 0x80000000) != 0

    def _auth_or_strip(self, b):
        if self.disable_enforce:
            self._mark("strip")
            return self.ptra.strip(b)
        self._mark("auth")
        return self.ptra.auth(b)

    def data(self):
        """返回 class_rw_t*：先验签，再 & FAST_DATA_MASK。"""
        if not self.has_rw_pointer():
            raise Violation("data() requires has_rw_pointer()")
        return deref(self._auth_or_strip(self.bits) & self.arch.data_mask)

    def safe_ro(self, authenticate=True):
        """`safe_ro()`：bits 只 load 一次，然后一次性判断走哪条路。

        源码注释解释了为什么不能 load 两次：否则可能先看到 !has_rw_pointer，
        随后并发地被人存进 class_rw_t*，再用 RO 的签名方案去解一个 RW 指针。
        """
        bits_value = self.load()
        if self.has_rw_pointer(bits_value):
            self._mark("safe_ro:rw")
            return self.data().ro()
        self._mark("safe_ro:ro")
        if authenticate and not self.disable_enforce:
            self._mark("auth:ro")
            authed = self.ptra.auth(bits_value)
        else:
            self._mark("strip:ro")
            authed = self.ptra.strip(bits_value)
        return deref(authed & self.arch.data_mask)

    # ---- 写入 ----
    def _cas(self, old, new):
        self.cas_attempts += 1
        if self.cas_failures > 0:
            self.cas_failures -= 1
            self.bits = old ^ self.arch.flags_mask  # 制造一次「被并发改写」
            return False
        self.bits = new
        return True

    def setData(self, rw):
        """`setData(class_rw_t *newData)`。

        newBits = (authedBits & FAST_FLAGS_MASK) | newData | FAST_IS_RW_POINTER
        —— 旧字的 FAST 标志（3 或 2 位）被留下，指针被整个换掉。
        """
        if self.has_rw_pointer() and not (rw.flags & RW_REALIZING_OR_FUTURE):
            raise Violation(
                "setData over an existing rw pointer needs RW_REALIZING|RW_FUTURE")
        local = self.load()
        authed = 0 if local == 0 else self._auth_or_strip(local)
        addr = addr_of(rw, self.arch)
        new_bits = (authed & self.arch.flags_mask) | addr | self.arch.rw_bit
        self.bits = self.ptra.sign(new_bits)   # store release

    def setAndClearBits(self, set_bits, clear_bits):
        """先 auth 出裸值，改位，再重新 sign，最后 CAS 弱循环。"""
        if not self.has_rw_pointer():
            raise Violation("setAndClearBits requires has_rw_pointer()")
        if set_bits & clear_bits:
            raise Violation("set and clear must not overlap")
        while True:
            old = self.load()
            auth_bits = self._auth_or_strip(old)
            new_bits = (auth_bits | set_bits) & ~clear_bits & self.arch.full
            new_bits = self.ptra.sign(new_bits)
            if self._cas(old, new_bits):
                return

    def setBits(self, s):
        self.setAndClearBits(s, 0)

    def clearBits(self, c):
        self.setAndClearBits(0, c)

    def copyRWFrom(self, other):
        """换判别子重新签名（auth_and_resign），store release。"""
        raw = other.ptra.auth(other.load())
        self.bits = self.ptra.sign(raw) & self.arch.full

    def copyROFrom(self, other, authenticate):
        if self.flags() & 0x80000000:
            raise Violation("copyROFrom requires RW_REALIZED unset")
        raw = other.load()
        if authenticate:
            raw = other.ptra.auth(raw)
            self.bits = self.ptra.sign(raw)
        else:
            self.bits = raw


def bad_signature_probe(bits_obj, tampered):
    """判断给定 bits 值验签是否失败（不抛异常）。"""
    try:
        bits_obj.ptra.auth(tampered)
        return False
    except AuthFailure:
        return True
