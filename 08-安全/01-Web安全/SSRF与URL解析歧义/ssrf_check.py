"""SSRF 防护自检。

断言来源:
- WHATWG URL Standard §3.5/§4.4(IPv4 number parser、IPv4 parser、ends in a number、host parser)
- OWASP SSRF Prevention Cheat Sheet(允许列表/阻止列表、重定向、DNS pinning、
  解析器分歧 `http://example.com\\@evil.com`、云元数据与 IMDSv2、绕过技术表)
"""
import ipaddress
from urllib.parse import urlsplit

from ssrf_guard import (BLOCKED_NETS, Policy, SsrfBlocked, classify, is_public,
                        parser_disagreement, unwrap)
from url_ipv4 import (bypass_table, ends_in_a_number, host_parser, ipv4_number_parser,
                      ipv4_parser, whatwg_authority_host)

PASS = 0


def check(label, cond, detail=""):
    global PASS
    assert cond, f"FAIL {label} {detail}"
    PASS += 1
    print(f"  ok  {label}")


def blocked(fn, *a, **kw):
    try:
        fn(*a, **kw)
    except SsrfBlocked:
        return True
    return False


print("§3.5 IPv4 number parser")
check("十进制", ipv4_number_parser("127") == (127, False))
check("十六进制(报 validation error)", ipv4_number_parser("0x7f") == (127, True))
check("八进制(报 validation error)", ipv4_number_parser("0177") == (127, True))
check("裸 0x → (0, true)", ipv4_number_parser("0x") == (0, True))
check("十六进制含非法位 → failure", ipv4_number_parser("0xg") is None)
check("十进制含字母 → failure", ipv4_number_parser("12a") is None)
check("空串 → failure", ipv4_number_parser("") is None)

print("§3.5 ends in a number checker")
check("纯数字段", ends_in_a_number("1.2.3.4") and ends_in_a_number("2130706433"))
check("裸 0x 结尾也算", ends_in_a_number("a.0x"))
check("域名结尾不算", not ends_in_a_number("example.com") and not ends_in_a_number(""))
check("尾点去掉后再判", ends_in_a_number("127.0.0.1."))

print("§3.5 IPv4 parser:同一台机器的多种写法")
for raw, want in bypass_table().items():
    got = ipv4_parser(raw)
    check(f"{raw:>16} → {want}", got is not None and str(got) == want, got)

print("§3.5 IPv4 parser:失败用例")
check("段数 >4 → failure", ipv4_parser("1.2.3.4.5") is None)
check("短段越界 → failure", ipv4_parser("256.1.1.1") is None)
check("末段超范围 → failure", ipv4_parser("1.2.65536") is None)
check("末段恰好 2^16 → failure", ipv4_parser("1.2.3.65536") is None)
check("含非数字 → failure", ipv4_parser("1.2.3.a") is None)
check("全空 → failure", ipv4_parser("") is None)

print("§4.4 host parser")
check("IPv4 走 IP 分支", host_parser("0x7f.1") == ("ipv4", "127.0.0.1"))
check("域名降小写", host_parser("Example.COM") == ("domain", "example.com"))
check("IPv6 规范化(CPython 保留点分四段写法,WHATWG 序列化器会写成 ::ffff:7f00:1)",
      host_parser("[::ffff:127.0.0.1]") == ("ipv6", "::ffff:127.0.0.1"),
      host_parser("[::ffff:127.0.0.1]"))
check("非法 IPv6 → failure", host_parser("[::zz]") is None)
check("越界数字主机 → failure", host_parser("1.2.3.4.5") is None)
check("AAAA 记录形态的十六进制域名不误判", host_parser("dead.beef") == ("domain", "dead.beef"))

print("解析器分歧(OWASP 明示的 `http://example.com\\@evil.com`)")
d = parser_disagreement("http://example.com\\@evil.com")
check("WHATWG 读到 example.com", d and d[0] == "example.com", d)
check("CPython urllib 读到 evil.com", d and d[1] == "evil.com", d)
check("两者不一致", d[0] != d[1])
check("反斜杠在 WHATWG 下终止 authority", whatwg_authority_host("http://a.com\\b") == "a.com")
check("正常 URL 无分歧",
      parser_disagreement("https://api.example.com/v1/x") == ("api.example.com", "api.example.com"))

print("非公网地址判定(OWASP 阻止列表 + 内嵌 v4 解包)")
for host in ("127.0.0.1", "169.254.169.254", "10.1.2.3", "192.168.1.1", "172.20.0.1",
             "100.64.0.1", "0.0.0.0"):
    check(f"{host} 非公网", not is_public(ipaddress.ip_address(host)))
check("公网地址通过", is_public(ipaddress.ip_address("93.184.216.34")))
check("IPv4-mapped IPv6 被解包", str(unwrap(ipaddress.ip_address("::ffff:169.254.169.254")))
      == "169.254.169.254" and not is_public(ipaddress.ip_address("::ffff:169.254.169.254")))
check("NAT64 内嵌 v4 被解包", not is_public(ipaddress.ip_address("64:ff9b::a9fe:a9fe")))
check("6to4 内嵌 v4 被解包", not is_public(ipaddress.ip_address("2002:7f00:1::"))) 
check("IPv6 回环非公网", not is_public(ipaddress.ip_address("::1")))
check("文档用公网 IPv6 通过", is_public(ipaddress.ip_address("2606:4700::1111")))
check("元数据主机名在阻止列表", "metadata.google.internal" in __import__("ssrf_guard").METADATA_HOSTS)

print("Case 1 允许列表(命中后自行重建请求)")
pol = Policy(allow_domains=("api.example.com",), resolver=lambda d: ["93.184.216.34"])
t = pol.target("https://api.example.com/v1/users?a=1")
check("允许列表命中并重建", t == {"scheme": "https", "host": "api.example.com",
                                "port": 443, "path": "/v1/users"}, t)
check("主机名先按规范降小写再比对(OWASP 的『大小写敏感』指的是比 parser 输出)",
      pol.target("https://API.example.com/v1")["host"] == "api.example.com")
check("尾点 FQDN 形式不等价 → 拒绝(降小写不删尾点)", blocked(pol.target, "https://api.example.com./v1"))
check("子域不在列表 → 拒绝", blocked(pol.target, "https://evil.api.example.com/v1"))
check("相似域名 → 拒绝", blocked(pol.target, "https://api.example.com.evil.io/v1"))
check("端口 8080 保留在重建结果里",
      pol.target("https://api.example.com:8080/x")["port"] == 8080)

print("Case 2 阻止列表 + DNS pinning")
pol2 = Policy(resolver=lambda d: ["93.184.216.34"])
check("公网域名放行", pol2.target("https://hook.example.net/cb")["host"] == "hook.example.net")
pol3 = Policy(resolver=lambda d: ["93.184.216.34", "169.254.169.254"])
check("多 A 记录中有一条内网 → 拒绝(防 pinning)", blocked(pol3.target, "https://evil.net/cb"))
pol4 = Policy(resolver=lambda d: [])
check("无解析结果 → 拒绝", blocked(pol4.target, "https://nope.net/cb"))
check("显式内网 IP 字面量 → 拒绝", blocked(pol2.target, "http://169.254.169.254/latest/meta-data/"))
check("十进制的元数据地址 → 拒绝", blocked(pol2.target, "http://2852039166/latest/meta-data/"))
check("十六进制回环 → 拒绝", blocked(pol2.target, "http://0x7f000001/admin"))
check("八进制回环 → 拒绝", blocked(pol2.target, "http://0177.0.0.1/admin"))
check("IPv4-mapped 回环 → 拒绝", blocked(pol2.target, "http://[::ffff:127.0.0.1]/admin"))
check("元数据主机名 → 拒绝", blocked(pol2.target, "http://metadata.google.internal/"))
check("file scheme → 拒绝", blocked(pol2.target, "file:///etc/passwd"))
check("gopher scheme → 拒绝", blocked(pol2.target, "gopher://127.0.0.1:11211/_stats"))
check("解析器分歧 URL → 拒绝", blocked(pol2.target, "http://example.com\\@evil.com"))

print("重定向与纵深防御")
check("3xx 不跟随(直接拒绝)", blocked(pol2.maybe_redirect, "http://169.254.169.254/"))
check("默认 follow_redirects=False", pol2.follow_redirects is False)
check("允许列表模式下不允许任意域名(即使公网)",
      blocked(Policy(allow_domains=("api.example.com",), resolver=lambda d: ["1.1.1.1"]).target,
              "https://other.example.com/x"))

print(f"\n{PASS} 项断言全部通过 (SSRF 防护 + URL IPv4 解析)")
