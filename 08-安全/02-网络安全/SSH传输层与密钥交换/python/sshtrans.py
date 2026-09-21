"""SSH 传输层协议：二进制包协议 + 算法协商 + 密钥派生（RFC 4253 + RFC 8308）。

转写对象：
  RFC 4253 §6  二进制包协议（packet_length / padding_length / padding / mac）
  RFC 4253 §7.1 算法协商（KEX 的三条件 vs cipher/mac 的单条件）
  RFC 4253 §7.2 密钥派生与扩展（K1..Kn）
  RFC 4253 §8   DH 交换与 exchange hash H
  RFC 8308 §2   扩展协商（ext-info-c / ext-info-s / SSH_MSG_EXT_INFO）
"""

import hashlib

MIN_PADDING = 4
MAX_PADDING = 255
MIN_PACKET = 16
MAX_UNCOMPRESSED_PAYLOAD = 32768
MAX_TOTAL_PACKET = 35000

# RFC 4253 §10：消息号
SSH_MSG_KEXINIT = 20
SSH_MSG_NEWKEYS = 21
SSH_MSG_EXT_INFO = 7


def HASH(data):
    return hashlib.sha256(data).digest()


# ---------------------------------------------------------------- mpint

def mpint(value):
    """RFC 4251 §5：两 complement 大端，去掉前导零；最高位为 1 时补一个 0x00。

    0 编码成空串。
    """
    if value == 0:
        return b""
    if value > 0:
        n = (value.bit_length() + 7) // 8
        if (value >> (8 * n - 8)) & 0x80:
            n += 1                       # 正数最高位为 1 -> 前置一个 0x00
    else:
        # 最小的 n 使得 -2^(8n-1) <= value，等价于 (~value).bit_length() <= 8n-1
        n = 1
        while (~value).bit_length() > 8 * n - 1:
            n += 1
    return (value & ((1 << (n * 8)) - 1)).to_bytes(n, "big")


def ssh_string(b):
    return len(b).to_bytes(4, "big") + b


def name_list(names):
    return ssh_string(",".join(names).encode())


# ---------------------------------------------------------------- §6 包格式

def block_size_for(cipher_block_size):
    """§6：对齐单位是 cipher block size 与 8 中较大的那个。"""
    return max(cipher_block_size, 8)


def padding_length(payload_len, cipher_block_size=8):
    """求满足全部约束的最小 padding。

    约束：(4 + 1 + payload + padding) % max(block,8) == 0
          4 <= padding <= 255
          4 + 1 + payload + padding >= max(16, block)
    """
    blk = block_size_for(cipher_block_size)
    pad = (blk - ((4 + 1 + payload_len) % blk)) % blk
    if pad < MIN_PADDING:
        pad += blk
    return pad


def build_packet(payload, cipher_block_size=8, mac=b"", pad_extra=0):
    """组装一个 SSH 二进制包（padding 用确定性字节填充，便于测试）。"""
    pad = padding_length(len(payload), cipher_block_size)
    pad += pad_extra
    assert MIN_PADDING <= pad <= MAX_PADDING, "padding 越界"
    packet_length = pad + 1 + len(payload)
    assert 4 + packet_length >= max(MIN_PACKET, cipher_block_size), "小于最小包长"
    assert (4 + packet_length) % block_size_for(cipher_block_size) == 0, "未按块对齐"
    return (packet_length.to_bytes(4, "big") + bytes([pad]) + payload
            + bytes((i * 7 + 3) & 0xFF for i in range(pad)) + mac)


def parse_packet(raw, cipher_block_size=8, mac_len=0):
    """解析并校验一个包，返回 dict 或抛 ValueError。"""
    if len(raw) < 4:
        raise ValueError("连 packet_length 都不够")
    packet_length = int.from_bytes(raw[:4], "big")
    if packet_length + 4 + mac_len != len(raw):
        raise ValueError("packet_length 与实际长度不符")
    if 4 + packet_length < max(MIN_PACKET, cipher_block_size):
        raise ValueError("小于最小包长（16 或 cipher block size，取大者）")
    if (4 + packet_length) % block_size_for(cipher_block_size) != 0:
        raise ValueError("未按块对齐")
    pad = raw[4]
    if pad < MIN_PADDING:
        raise ValueError("padding 少于 4 字节")
    payload_len = packet_length - pad - 1
    if payload_len < 0:
        raise ValueError("payload 长度为负")
    return {"packet_length": packet_length, "padding_length": pad,
            "payload": raw[5:5 + payload_len], "padding": raw[5 + payload_len:4 + packet_length],
            "mac": raw[4 + packet_length:]}


def max_payload_per_packet(cipher_block_size=8, mac_len=0):
    """在 padding <= 255 且总长 <= 35000 下的最大未压缩载荷。"""
    best = 0
    for L in range(0, MAX_UNCOMPRESSED_PAYLOAD + 1):
        pad = padding_length(L, cipher_block_size)
        if pad > MAX_PADDING:
            break
        if 4 + 1 + L + pad + mac_len > MAX_TOTAL_PACKET:
            break
        best = L
    return best


# ---------------------------------------------------------------- §7.1 协商

class Alg:
    """一个算法名 + 它需要的主机密钥能力。"""

    def __init__(self, name, needs="any"):
        self.name = name
        self.needs = needs          # any / sign / encrypt

    def __repr__(self):
        return "Alg(%s,%s)" % (self.name, self.needs)


def select_kex(client_kex, server_kex, client_host_keys, server_host_keys,
               host_key_caps):
    """§7.1：逐个检查客户端的 KEX 算法，取第一个满足三个条件的。

    条件：① 服务端也支持；② 若需 encryption-capable 主机密钥，存在双方都支持
    且具备该能力的算法；③ 若需 signature-capable 同理。
    """
    for k in client_kex:
        if k.name not in [x.name for x in server_kex]:
            continue
        if k.needs == "any":
            return k
        ok = False
        for hk in client_host_keys:
            if hk not in server_host_keys:
                continue
            if need_ok(hk, k.needs, host_key_caps):
                ok = True
                break
        if ok:
            return k
    return None


def need_ok(host_key_alg, need, host_key_caps):
    """主机密钥算法是否具备 KEX 需要的能力（sign / encrypt）。

    RFC 4253 §7.1：ssh-rsa 两者皆可，ssh-dss 与 ssh-ed25519 只能签名。
    """
    return need in host_key_caps.get(host_key_alg, set())


def select_first_match(client_list, server_list):
    """§7.1：cipher / MAC / compression 的选法 —— 客户端列表中第一个服务端也有的。"""
    server_names = set(server_list)
    for c in client_list:
        if c in server_names:
            return c
    return None


# ---------------------------------------------------------------- §7.2 密钥派生

def derive_key(k_mpint, h, letter, session_id, need_bytes=32, hashmod=None):
    """K1 = HASH(K || H || X || session_id)，不足则按 §7.2 的链式扩展。"""
    hashmod = hashmod or HASH
    k1 = hashmod(k_mpint + h + letter + session_id)
    out = k1
    prev = [k1]
    while len(out) < need_bytes:
        nxt = hashmod(k_mpint + h + b"".join(prev))
        prev.append(nxt)
        out += nxt
    return out[:need_bytes]


LETTERS = {
    "iv_c2s": b"A", "iv_s2c": b"B",
    "enc_c2s": b"C", "enc_s2c": b"D",
    "mac_c2s": b"E", "mac_s2c": b"F",
}


def derive_all(k_mpint, h, session_id, need_bytes=32):
    return {name: derive_key(k_mpint, h, ch, session_id, need_bytes)
            for name, ch in LETTERS.items()}


# ---------------------------------------------------------------- §8 交换哈希

def exchange_hash(v_c, v_s, i_c, i_s, k_s, e, f, k, hashmod=None):
    """H = hash(V_C || V_S || I_C || I_S || K_S || e || f || K)。

    V_C/V_S 与 I_C/I_S 是 string（带 4 字节长度前缀），e/f/K 是 mpint。
    """
    hashmod = hashmod or HASH
    return hashmod(ssh_string(v_c) + ssh_string(v_s) + ssh_string(i_c)
                   + ssh_string(i_s) + ssh_string(k_s)
                   + mpint(e) + mpint(f) + mpint(k))


# ---------------------------------------------------------------- RFC 8308

EXT_INFO_C = "ext-info-c"
EXT_INFO_S = "ext-info-s"


def offers_ext_info(names, role):
    return (EXT_INFO_C if role == "client" else EXT_INFO_S) in names


def ext_negotiation_error(selected_kex, client_kex_names, server_kex_names):
    """§2.2：若 ext-info-c / ext-info-s 被当成 KEX 方法协商出来，必须断开。"""
    if selected_kex in (EXT_INFO_C, EXT_INFO_S):
        return "indicator 被协商为 KEX 方法，必须断开"
    if EXT_INFO_S in client_kex_names:
        return "客户端发送了服务端专用的 indicator"
    if EXT_INFO_C in server_kex_names:
        return "服务端发送了客户端专用的 indicator"
    return ""


def ext_info_allowed(sender_role, peer_offers):
    """§2.2：只有对端给出了对应的 indicator 才「可以」发 EXT_INFO（不是必须）。"""
    return peer_offers
