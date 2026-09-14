#!/usr/bin/env python3
"""JA3 / JA4 TLS ClientHello 指纹计算器（含 ClientHello 构造与解析回环）。

用官方向量做端到端验证:
  JA4: t13d1516h2_8daaf6152771_e5627efa2ab1   (FoxIO 规范例)
  JA3: ada70206e40642a3e4461f35503241d5       (salesforce/ja3 README 例)
用法: python tls_fingerprint.py
"""
import hashlib
import struct

# ---------- GREASE (RFC 8701): 0x0a0a, 0x1a1a, ... 0xfafa ----------
def is_grease(v: int) -> bool:
    return (v & 0x0F0F) == 0x0A0A

VER_CODE = {0x0304: '13', 0x0303: '12', 0x0302: '11', 0x0301: '10',
            0x0300: 's3', 0x0200: 's2'}

# ---------- ClientHello 解析 ----------
class ClientHello:
    def __init__(self):
        self.legacy_version = 0
        self.ciphers = []          # wire 顺序
        self.exts = []             # wire 顺序的扩展类型
        self.sni = False
        self.alpn_first = ''       # 首个 ALPN 值
        self.supported_versions = []
        self.sigalgs = []          # wire 顺序
        self.curves = []
        self.point_formats = []

def parse_clienthello(data: bytes) -> ClientHello:
    ch = ClientHello()
    off = 0
    def take(n):
        nonlocal off
        b = data[off:off + n]; off += n
        return b
    assert take(1) == b'\x16'                      # Record: Handshake
    take(2)                                        # record version
    rec_len = struct.unpack('>H', take(2))[0]
    body = data[off:off + rec_len]
    assert take(1) == b'\x01'                      # Handshake: ClientHello
    hlen = int.from_bytes(take(3), 'big')
    hello = body[4:4 + hlen]                       # 握手头 1+3 字节
    # ---- hello 内部 ----
    p = 0
    ch.legacy_version = int.from_bytes(hello[p:p+2], 'big'); p += 2
    p += 32                                          # random
    sid_len = hello[p]; p += 1 + sid_len             # session id
    cs_len = int.from_bytes(hello[p:p+2], 'big'); p += 2
    ch.ciphers = [int.from_bytes(hello[p+i:p+i+2], 'big')
                  for i in range(0, cs_len, 2)]
    p += cs_len
    cm_len = hello[p]; p += 1 + cm_len               # compression
    exts_len = int.from_bytes(hello[p:p+2], 'big'); p += 2
    end = p + exts_len
    while p < end:
        et = int.from_bytes(hello[p:p+2], 'big'); p += 2
        el = int.from_bytes(hello[p:p+2], 'big'); p += 2
        ed = hello[p:p+el]; p += el
        ch.exts.append(et)
        if et == 0x0000:                             # server_name
            ch.sni = True
        elif et == 0x0010 and len(ed) >= 4:          # ALPN
            plen = ed[2]                             # 首个协议长度
            ch.alpn_first = ed[3:3+plen].decode('latin1')
        elif et == 0x002b:                           # supported_versions
            n = ed[0]
            ch.supported_versions = [int.from_bytes(ed[1+i:3+i], 'big')
                                     for i in range(0, n, 2)]
        elif et == 0x000d:                           # signature_algorithms
            n = int.from_bytes(ed[0:2], 'big')
            ch.sigalgs = [int.from_bytes(ed[2+i:4+i], 'big')
                          for i in range(0, n, 2)]
        elif et == 0x000a:                           # supported_groups
            n = int.from_bytes(ed[0:2], 'big')
            ch.curves = [int.from_bytes(ed[2+i:4+i], 'big')
                         for i in range(0, n, 2)]
        elif et == 0x000b:                           # ec_point_formats
            ch.point_formats = list(ed[1:1+ed[0]])
    return ch

# ---------- ClientHello 构造 (用于回环测试) ----------
def ext(t, data):
    return struct.pack('>HH', t, len(data)) + data

def build_clienthello(version=0x0301, ciphers=(), exts=(), random32=None,
                      sigalgs=(), curves=(), point_formats=(), sni=None,
                      alpn=(), supported_versions=(), legacy=0x0301):
    """exts 参数 = 已编码的扩展字节串列表 (用 ext() 生成)。"""
    blob = struct.pack('>H', version) + (random32 or b'\x11'*32)
    blob += b'\x00'                                   # session id 空
    blob += struct.pack('>H', len(ciphers)*2) + b''.join(
        struct.pack('>H', c) for c in ciphers)
    blob += b'\x01\x00'                               # compression: null
    parts = list(exts)
    if sni is not None:
        parts.append(ext(0x0000, struct.pack('>HB', len(sni) + 3, 0)
                         + struct.pack('>H', len(sni)) + sni))
    if curves:
        parts.append(ext(0x000a, struct.pack('>H', len(curves)*2)
                         + b''.join(struct.pack('>H', c) for c in curves)))
    if point_formats:
        parts.append(ext(0x000b, bytes([len(point_formats)])
                         + bytes(point_formats)))
    if sigalgs:
        parts.append(ext(0x000d, struct.pack('>H', len(sigalgs)*2)
                         + b''.join(struct.pack('>H', s) for s in sigalgs)))
    if alpn:
        al = b''.join(bytes([len(a)]) + a for a in alpn)
        parts.append(ext(0x0010, struct.pack('>H', len(al)) + al))
    if supported_versions:
        sv = bytes([len(supported_versions)*2]) + b''.join(
            struct.pack('>H', v) for v in supported_versions)
        parts.append(ext(0x002b, sv))
    blob += struct.pack('>H', sum(len(x) for x in parts)) + b''.join(parts)
    hs = b'\x01' + len(blob).to_bytes(3, 'big') + blob
    return struct.pack('>BHH', 0x16, legacy, len(hs)) + hs

# ---------- JA3 ----------
def ja3(ch: ClientHello):
    f = lambda xs: '-'.join(str(v) for v in xs if not is_grease(v))
    raw = ','.join([str(ch.legacy_version), f(ch.ciphers), f(ch.exts),
                    f(ch.curves), f(ch.point_formats)])
    return hashlib.md5(raw.encode()).hexdigest(), raw

# ---------- JA4 ----------
def _h12(items):
    if not items:
        return '000000000000'
    return hashlib.sha256(','.join(items).encode()).hexdigest()[:12]

def ja4(ch: ClientHello) -> str:
    ver = max((v for v in ch.supported_versions if not is_grease(v)),
              default=ch.legacy_version)
    a = ('t' + VER_CODE.get(ver, '00') + ('d' if ch.sni else 'i')
         + '%02d' % len([c for c in ch.ciphers if not is_grease(c)])
         + '%02d' % len([e for e in ch.exts if not is_grease(e)])
         + (ch.alpn_first[:2] if ch.alpn_first else '00'))
    b = _h12(['%04x' % c for c in sorted(
        [c for c in ch.ciphers if not is_grease(c)])])
    ext_list = ','.join('%04x' % e for e in sorted(
        [e for e in ch.exts if not is_grease(e)
         and e not in (0x0000, 0x0010)]))
    if ch.sigalgs:
        ext_list += '_' + ','.join('%04x' % s for s in ch.sigalgs)
    c = _h12([ext_list])
    return a + '_' + b + '_' + c

# ---------- 官方向量回环 ----------
def main():
    # 向量A: JA4 规范例 t13d1516h2_8daaf6152771_e5627efa2ab1
    CIPHERS_A = [0x002f, 0x0035, 0x009c, 0x009d, 0x1301, 0x1302, 0x1303,
                 0xc013, 0xc014, 0xc02b, 0xc02c, 0xc02f, 0xc030, 0xcca8, 0xcca9]
    EXTS_A = [0x0005, 0x000a, 0x000b, 0x000d, 0x0012, 0x0015, 0x0017, 0x001b,
              0x0023, 0x002b, 0x002d, 0x0033, 0x4469, 0xff01]
    SIG_A = [0x0403, 0x0804, 0x0401, 0x0503, 0x0805, 0x0501, 0x0806, 0x0601]
    # EXTS_A 已含 0x000a/0x000b/0x000d/0x002b → 构造时不重复追加
    data_a = build_clienthello(
        ciphers=CIPHERS_A,
        exts=[ext(e, b'') for e in EXTS_A
              if e not in (0x000a, 0x000b, 0x000d, 0x002b)],
        curves=[0x001d], point_formats=[0], sigalgs=SIG_A,
        sni=b'example.com', alpn=[b'h2'], supported_versions=[0x0304])
    ch = parse_clienthello(data_a)
    j4 = ja4(ch)
    print('== 向量A (FoxIO 规范例) ==')
    print('  ciphers:', len(ch.ciphers), ' exts(wire):', len(ch.exts),
          ' sni:', ch.sni, ' alpn:', ch.alpn_first)
    print('  JA4 =', j4)
    assert j4 == 't13d1516h2_8daaf6152771_e5627efa2ab1', 'JA4 向量不匹配!'
    print('  ✓ 与官方发布值一致')
    print()
    # 向量B: JA3 README 例
    CIPHERS_B = [0x2f, 0x35, 0x05, 0x0a, 0xc009, 0xc00a, 0xc013, 0xc014,
                 0x32, 0x38, 0x13, 0x04]
    data_b = build_clienthello(version=0x0301, ciphers=CIPHERS_B, exts=[],
                               curves=[23, 24, 25], point_formats=[0],
                               sni=b'www.example.com')
    chb = parse_clienthello(data_b)
    j3, raw = ja3(chb)
    print('== 向量B (salesforce/ja3 README 例) ==')
    print('  raw  =', raw)
    print('  JA3  =', j3)
    assert raw == ('769,47-53-5-10-49161-49162-49171-49172-'
                   '50-56-19-4,0-10-11,23-24-25,0'), 'JA3 原串不匹配!'
    assert j3 == 'ada70206e40642a3e4461f35503241d5', 'JA3 向量不匹配!'
    print('  ✓ 与官方发布值一致')
    print()
    # GREASE 剔除演示: 向量A 注入 GREASE 套件/扩展, JA4 不变
    data_g = build_clienthello(
        ciphers=[0x0a0a] + CIPHERS_A + [0x1a1a],
        exts=[ext(e, b'') for e in EXTS_A
              if e not in (0x000a, 0x000b, 0x000d, 0x002b)]
             + [ext(0x2a2a, b'')],
        curves=[0x001d], point_formats=[0], sigalgs=SIG_A,
        sni=b'example.com', alpn=[b'h2'],
        supported_versions=[0x0a0a, 0x0304])
    j4g = ja4(parse_clienthello(data_g))
    print('== GREASE 鲁棒性: 注入 0x0a0a/0x1a1a 套件 + 0x2a2a 扩展 ==')
    print('  JA4 =', j4g)
    assert j4g == j4, 'GREASE 未被正确剔除!'
    print('  ✓ 指纹不变 (GREASE 全程忽略)')

if __name__ == '__main__':
    main()
