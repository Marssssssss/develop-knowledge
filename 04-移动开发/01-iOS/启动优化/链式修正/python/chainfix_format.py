"""各 pointer_format 的解析与回写:转写自 mach_o/ChainedFixups.cpp。

回写校验的关键:源码先把值赋进位域(超宽被静默截断),再**读回位域**比对,
所以 next / addend / ordinal 超宽是在这一步被发现的,而不是在赋值时。
"""

from chainfix_const import (bits, ins, Fixup, Error,
                            PTR_ARM64E, PTR_64, PTR_32, PTR_64_OFFSET,
                            PTR_ARM64E_KERNEL, PTR_ARM64E_USERLAND)


class PointerFormat:
    """ChainedFixups::PointerFormat 的最小等价物。"""

    value = 0
    name = "?"
    stride = 4
    is64 = True
    auth = False
    bind_bit_count = 0
    unauth_is_vmaddr = True
    # 位域宽度,子类覆写
    next_bits = 12
    next_lo = 51

    # ---- 能力查询(源码里是纯虚函数) ----
    def min_next(self):
        return self.stride

    def max_next(self):
        return self.stride * ((1 << self.next_bits) - 1)

    def max_rebase_target_offset(self, authenticated):
        raise NotImplementedError

    def max_bind_ordinal(self, authenticated):
        return (1 << self.bind_bit_count) - 1

    def bind_min_addend(self, authenticated):
        raise NotImplementedError

    def bind_max_addend(self, authenticated):
        raise NotImplementedError

    def supports_binds(self):
        return True

    # ---- 链推进 ----
    def next_location(self, loc, raw):
        n = bits(raw, self.next_lo, self.next_lo + self.next_bits - 1)
        if n == 0:
            return None
        return loc + n * self.stride

    def next_field(self, delta):
        return delta // self.stride

    def check_distance(self, raw, delta):
        """读回 next 位域再比,超宽时位域已被截断,这一步才拦得住。"""
        got = bits(raw, self.next_lo, self.next_lo + self.next_bits - 1)
        if got * self.stride != delta:
            raise Error("badChainDistance")

    # ---- 解析 / 回写 ----
    def parse(self, raw, pref):
        raise NotImplementedError

    def write(self, fixup, delta, pref):
        raise NotImplementedError


class Fmt64(PointerFormat):
    """DYLD_CHAINED_PTR_64 : target 是 vmaddr(需减 preferredLoadAddress)。

    位域:rebase = target:36 | high8:8 | reserved:7 | next:12 | bind:1
         bind   = ordinal:24 | addend:8 | reserved:19 | next:12 | bind:1"""

    value = PTR_64
    name = "DYLD_CHAINED_PTR_64"
    stride = 4
    is64 = True
    auth = False
    bind_bit_count = 24
    next_bits = 12
    next_lo = 51
    unauth_is_vmaddr = True

    def max_rebase_target_offset(self, authenticated):
        return 0xFFFFFFFFF  # 36 位

    def bind_min_addend(self, authenticated):
        return 0

    def bind_max_addend(self, authenticated):
        return 255  # 8 位,无符号

    def parse(self, raw, pref):
        if bits(raw, 63, 63):
            return Fixup(is_bind=True, ordinal=bits(raw, 0, 23), addend=bits(raw, 24, 31))
        target = bits(raw, 0, 35)
        hi = bits(raw, 36, 43)
        if self.unauth_is_vmaddr:
            return Fixup(target=(hi << 56) | (target - pref))
        return Fixup(target=(hi << 56) | target)

    def write(self, fixup, delta, pref):
        nxt = self.next_field(delta)
        raw = 0
        if fixup.is_bind:
            raw = ins(raw, 0, 23, fixup.ordinal)
            raw = ins(raw, 24, 31, fixup.addend)
            if bits(raw, 24, 31) != fixup.addend:
                raise Error("badAddend")
            if bits(raw, 0, 23) != fixup.ordinal:
                raise Error("badBindOrdinal")
        else:
            hi = (fixup.target >> 56) & 0xFF
            low = fixup.target & 0x00FFFFFFFFFFFFFF
            raw = ins(raw, 36, 43, hi)
            want = low + pref if self.unauth_is_vmaddr else low
            raw = ins(raw, 0, 35, want)
            if bits(raw, 0, 35) != want:
                raise Error("badVmAddr" if self.unauth_is_vmaddr else "badVmOffset")
        raw = ins(raw, 51, 62, nxt)
        raw = ins(raw, 63, 63, 1 if fixup.is_bind else 0)
        self.check_distance(raw, delta)
        return raw


class Fmt64Offset(Fmt64):
    """DYLD_CHAINED_PTR_64_OFFSET : target 直接是 vm offset(不减基址)。"""

    value = PTR_64_OFFSET
    name = "DYLD_CHAINED_PTR_64_OFFSET"
    unauth_is_vmaddr = False


class Fmt32(PointerFormat):
    """DYLD_CHAINED_PTR_32 : 4 字节条目,next 只有 5 位。

    位域:rebase = target:26 | next:5 | bind:1
         bind   = ordinal:20 | addend:6 | next:5 | bind:1"""

    value = PTR_32
    name = "DYLD_CHAINED_PTR_32"
    stride = 4
    is64 = False
    auth = False
    bind_bit_count = 20
    next_bits = 5
    next_lo = 26
    unauth_is_vmaddr = True

    def max_rebase_target_offset(self, authenticated):
        return 0x03FFFFFF  # 26 位

    def bind_min_addend(self, authenticated):
        return 0

    def bind_max_addend(self, authenticated):
        return 63  # 6 位

    def parse(self, raw, pref):
        raw &= 0xFFFFFFFF
        if bits(raw, 31, 31):
            return Fixup(is_bind=True, ordinal=bits(raw, 0, 19), addend=bits(raw, 20, 25))
        return Fixup(target=bits(raw, 0, 25) - pref)

    def write(self, fixup, delta, pref):
        nxt = self.next_field(delta)
        raw = 0
        if fixup.is_bind:
            raw = ins(raw, 0, 19, fixup.ordinal)
            raw = ins(raw, 20, 25, fixup.addend)
            if bits(raw, 20, 25) != fixup.addend:
                raise Error("badAddend")
            if bits(raw, 0, 19) != fixup.ordinal:
                raise Error("badBindOrdinal")
        else:
            want = fixup.target + pref
            raw = ins(raw, 0, 25, want)
            if bits(raw, 0, 25) != want:
                raise Error("badVmOffset")
        raw = ins(raw, 26, 30, nxt)
        raw = ins(raw, 31, 31, 1 if fixup.is_bind else 0)
        self.check_distance(raw, delta)
        return raw & 0xFFFFFFFF


class FmtArm64e(PointerFormat):
    """arm64e 通用:next 11 位,支持 auth,stride 由子类给(8 或 4)。

    位域:rebase     = target:43 | high8:8 | next:11 | bind:1 | auth:1
         authRebase = target:32 | diversity:16 | addrDiv:1 | key:2 | next:11 | bind:1 | auth:1
         bind       = ordinal:16 | zero:16 | addend:19 | next:11 | bind:1 | auth:1
         authBind   = 同上 bind,但 addend 的 19 位换成 diversity/addrDiv/key"""

    is64 = True
    auth = True
    bind_bit_count = 16
    next_bits = 11
    next_lo = 51
    unauth_is_vmaddr = True

    def max_rebase_target_offset(self, authenticated):
        return 0xFFFFFFFF if authenticated else 0x7FFFFFFFFFF  # 32 位 / 43 位

    def bind_min_addend(self, authenticated):
        return 0 if authenticated else -0x3FFFF

    def bind_max_addend(self, authenticated):
        return 0 if authenticated else 0x3FFFF  # 19 位

    def parse(self, raw, pref):
        if bits(raw, 62, 62):  # bind
            if bits(raw, 63, 63):  # auth bind
                return Fixup(is_bind=True, authenticated=True,
                             ordinal=bits(raw, 0, 15), key=bits(raw, 49, 50),
                             addr_div=bits(raw, 48, 48), diversity=bits(raw, 32, 47))
            return Fixup(is_bind=True, ordinal=bits(raw, 0, 15), addend=bits(raw, 32, 50))
        if bits(raw, 63, 63):  # auth rebase
            return Fixup(authenticated=True, target=bits(raw, 0, 31),
                         key=bits(raw, 49, 50), addr_div=bits(raw, 48, 48),
                         diversity=bits(raw, 32, 47))
        target = bits(raw, 0, 42)
        hi = bits(raw, 43, 50)
        if self.unauth_is_vmaddr:
            return Fixup(target=(hi << 56) | (target - pref))
        return Fixup(target=(hi << 56) | target)

    def write(self, fixup, delta, pref):
        nxt = self.next_field(delta)
        raw = 0
        if fixup.is_bind:
            if fixup.authenticated:
                raw = ins(raw, 0, 15, fixup.ordinal)
                raw = ins(raw, 32, 47, fixup.diversity)
                raw = ins(raw, 48, 48, fixup.addr_div)
                raw = ins(raw, 49, 50, fixup.key)
                raw = ins(raw, 63, 63, 1)
            else:
                raw = ins(raw, 0, 15, fixup.ordinal)
                raw = ins(raw, 32, 50, fixup.addend)
                if bits(raw, 32, 50) != fixup.addend:
                    raise Error("badAddend")
            if bits(raw, 0, 15) != fixup.ordinal:
                raise Error("badBindOrdinal")
            raw = ins(raw, 62, 62, 1)
        else:
            if fixup.authenticated:
                raw = ins(raw, 0, 31, fixup.target)
                raw = ins(raw, 32, 47, fixup.diversity)
                raw = ins(raw, 48, 48, fixup.addr_div)
                raw = ins(raw, 49, 50, fixup.key)
                raw = ins(raw, 63, 63, 1)
            else:
                hi = (fixup.target >> 56) & 0xFF
                low = fixup.target & 0x00FFFFFFFFFFFFFF
                raw = ins(raw, 0, 42, low + pref if self.unauth_is_vmaddr else low)
                raw = ins(raw, 43, 50, hi)
        raw = ins(raw, 51, 61, nxt)
        self.check_distance(raw, delta)
        return raw


class FmtArm64eRebase(FmtArm64e):
    """DYLD_CHAINED_PTR_ARM64E : 8 字节 stride,unauth target 是 vmaddr。"""

    value = PTR_ARM64E
    name = "DYLD_CHAINED_PTR_ARM64E"
    stride = 8
    unauth_is_vmaddr = True


class FmtArm64eUserland(FmtArm64e):
    """DYLD_CHAINED_PTR_ARM64E_USERLAND : 8 字节 stride,target 是 vm offset。"""

    value = PTR_ARM64E_USERLAND
    name = "DYLD_CHAINED_PTR_ARM64E_USERLAND"
    stride = 8
    unauth_is_vmaddr = False


class FmtArm64eKernel(FmtArm64e):
    """DYLD_CHAINED_PTR_ARM64E_KERNEL : 4 字节 stride,target 是 vm offset。"""

    value = PTR_ARM64E_KERNEL
    name = "DYLD_CHAINED_PTR_ARM64E_KERNEL"
    stride = 4
    unauth_is_vmaddr = False


REGISTRY = {c.value: c for c in (Fmt64, Fmt64Offset, Fmt32,
                                 FmtArm64eRebase, FmtArm64eUserland, FmtArm64eKernel)}


def make_format(v):
    if v not in REGISTRY:
        raise Error("unknown pointer_format %d" % v)
    return REGISTRY[v]()
