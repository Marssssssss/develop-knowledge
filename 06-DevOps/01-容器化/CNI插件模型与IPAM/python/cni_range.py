"""CNI host-local IPAM：Range / RangeSet 的规范化与包含判定。

逐行转写自 containernetworking/plugins：
  plugins/ipam/host-local/backend/allocator/range.go
  plugins/ipam/host-local/backend/allocator/range_set.go
保持 Go 源码的语义与报错文案（只把 net.IP 换成 4 字节元组 / int）。
"""

V4_LEN = 4
V6_LEN = 16


def ip_to_int(ip):
    n = 0
    for b in ip:
        n = (n << 8) | b
    return n


def int_to_ip(n, length=V4_LEN):
    return tuple((n >> (8 * (length - 1 - i))) & 0xFF for i in range(length))


def ip_to_str(ip):
    if len(ip) == V4_LEN:
        return ".".join(str(b) for b in ip)
    return ":".join("%02x" % b for b in ip)


def parse_v4(s):
    return tuple(int(x) for x in s.split("."))


def mask_of(prefix, length=V4_LEN):
    return int_to_ip(((1 << prefix) - 1) << (8 * length - prefix), length)


def canonicalize_ip(ip):
    """Go 的 canonicalizeIP：v4 折叠成 4 字节，v6 折叠成 16 字节。"""
    if len(ip) == V4_LEN:
        return ip
    if len(ip) == V6_LEN:
        return ip
    raise ValueError("IP not v4 nor v6")


def apply_mask(ip, mask):
    return tuple(ip[i] & mask[i] for i in range(len(ip)))


def next_ip(ip):
    return int_to_ip(ip_to_int(ip) + 1, len(ip))


def last_ip(subnet_ip, subnet_mask):
    """Go 的 lastIP：先取 ip|^mask（即广播地址），v4 再末字节减 1 排除广播。"""
    end = tuple(subnet_ip[i] | (subnet_mask[i] ^ 0xFF) for i in range(len(subnet_ip)))
    if len(subnet_ip) == V4_LEN:
        end = end[:3] + (end[3] - 1,)
    return end


def cmp_ip(a, b):
    ia, ib = ip_to_int(a), ip_to_int(b)
    return (ia > ib) - (ia < ib)


class Range:
    """对应 Go 的 allocator.Range（Subnet / RangeStart / RangeEnd / Gateway）。"""

    def __init__(self, subnet_ip, subnet_mask, range_start=None, range_end=None,
                 gateway=None):
        self.subnet_ip = tuple(subnet_ip)
        self.subnet_mask = tuple(subnet_mask)
        self.range_start = range_start
        self.range_end = range_end
        self.gateway = gateway
        self.length = len(self.subnet_ip)

    def prefix_len(self):
        return bin(ip_to_int(self.subnet_mask)).count("1")

    def canonicalize(self):
        """对应 Range.Canonicalize()，返回 None 或错误串。"""
        self.subnet_ip = canonicalize_ip(self.subnet_ip)
        ones, masklen = self.prefix_len(), self.length * 8
        if ones > masklen - 2:
            return "Network %s too small to allocate from" % self.subnet_str()
        if len(self.subnet_ip) != len(self.subnet_mask):
            return "IPNet IP and Mask version mismatch"
        network_ip = apply_mask(self.subnet_ip, self.subnet_mask)
        if self.subnet_ip != network_ip:
            return ("Network has host bits set. For a subnet mask of length %d "
                    "the network address is %s" % (ones, ip_to_str(network_ip)))
        if self.gateway is None:
            self.gateway = next_ip(self.subnet_ip)
        else:
            self.gateway = canonicalize_ip(self.gateway)
        if self.range_start is not None:
            self.range_start = canonicalize_ip(self.range_start)
            if not self.contains(self.range_start):
                return "RangeStart %s not in network %s" % (
                    ip_to_str(self.range_start), self.subnet_str())
        else:
            self.range_start = next_ip(self.subnet_ip)
        if self.range_end is not None:
            self.range_end = canonicalize_ip(self.range_end)
            if not self.contains(self.range_end):
                return "RangeEnd %s not in network %s" % (
                    ip_to_str(self.range_end), self.subnet_str())
        else:
            self.range_end = last_ip(self.subnet_ip, self.subnet_mask)
        return None

    def subnet_str(self):
        return "%s/%d" % (ip_to_str(self.subnet_ip), self.prefix_len())

    def in_subnet(self, addr):
        return apply_mask(addr, self.subnet_mask) == self.subnet_ip

    def contains(self, addr):
        """对应 Range.Contains()：nil 的 RangeStart/RangeEnd 直接忽略。"""
        try:
            addr = canonicalize_ip(addr)
        except ValueError:
            return False
        if len(addr) != len(self.subnet_ip):
            return False
        if not self.in_subnet(addr):
            return False
        if self.range_start is not None and cmp_ip(addr, self.range_start) < 0:
            return False
        if self.range_end is not None and cmp_ip(addr, self.range_end) > 0:
            return False
        return True

    def overlaps(self, other):
        if len(self.range_start) != len(other.range_start):
            return False
        return (self.contains(other.range_start) or self.contains(other.range_end)
                or other.contains(self.range_start) or other.contains(self.range_end))

    def __str__(self):
        return "%s-%s" % (ip_to_str(self.range_start), ip_to_str(self.range_end))


class RangeSet:
    """对应 Go 的 allocator.RangeSet（本质是一个 Range 切片）。"""

    def __init__(self, ranges):
        self.ranges = list(ranges)

    def __len__(self):
        return len(self.ranges)

    def __getitem__(self, i):
        return self.ranges[i]

    def contains(self, addr):
        r, _ = self.range_for(addr)
        return r is not None

    def range_for(self, addr):
        try:
            addr = canonicalize_ip(addr)
        except ValueError:
            return None, "IP not v4 nor v6"
        for r in self.ranges:
            if r.contains(addr):
                return r, None
        return None, "%s not in range set %s" % (ip_to_str(addr), self)

    def overlaps(self, other):
        for r in self.ranges:
            for r1 in other.ranges:
                if r.overlaps(r1):
                    return True
        return False

    def canonicalize(self):
        if not self.ranges:
            return "empty range set"
        fam = 0
        for i, r in enumerate(self.ranges):
            err = r.canonicalize()
            if err:
                return err
            if i == 0:
                fam = len(r.range_start)
            elif fam != len(r.range_start):
                return "mixed address families"
        n = len(self.ranges)
        for i, r1 in enumerate(self.ranges[:n - 1]):
            for r2 in self.ranges[i + 1:]:
                if r1.overlaps(r2):
                    return "subnets %s and %s overlap" % (r1, r2)
        return None

    def __str__(self):
        return ",".join(str(r) for r in self.ranges)
