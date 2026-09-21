"""IPv6 隐私地址（临时地址）与 SLAAC：RFC 4941 + RFC 8981。

转写对象：
  RFC 4941 §3.2.1  基于 MD5 与 history value 的随机化接口标识符
  RFC 4941 §3.3    收到 RA 后创建临时地址的步骤与生命周期
  RFC 4941 §3.4    过期与重新生成
  RFC 4941 §3.5    重新生成的频率
  RFC 8981 §3.3.2  用 PRF（HMAC-SHA-256 / BLAKE3）生成临时 IID 的推荐算法
  RFC 8981 §3.8    协议参数与配置变量的默认值
"""

import hashlib
import hmac

IID_BITS = 64
IID_BYTES = 8
# RFC 4291：IID 第 0 字节的 bit 6 是 universal/local 位（左起第 0 位编号）
UL_BIT_MASK = 0x02

# ---------------------------------------------------------------- RFC 8981 §3.8

TEMP_VALID_LIFETIME = 2 * 86400          # 2 天
TEMP_PREFERRED_LIFETIME = 1 * 86400      # 1 天
TEMP_IDGEN_RETRIES = 3
DUP_ADDR_DETECT_TRANSMITS = 1            # RFC 4862 默认
RETRANS_TIMER_MS = 1000                  # RFC 4861 默认


def regen_advance(idgen_retries=TEMP_IDGEN_RETRIES,
                  dad_transmits=DUP_ADDR_DETECT_TRANSMITS,
                  retrans_timer_ms=RETRANS_TIMER_MS):
    """REGEN_ADVANCE = 2 + (TEMP_IDGEN_RETRIES * DupAddrDetectTransmits *
    RetransTimer / 1000)，单位是秒。"""
    return 2 + (idgen_retries * dad_transmits * retrans_timer_ms / 1000.0)


def max_desync_factor(preferred=TEMP_PREFERRED_LIFETIME):
    """MAX_DESYNC_FACTOR = 0.4 * TEMP_PREFERRED_LIFETIME。"""
    return 0.4 * preferred


def desync_valid(d, preferred=TEMP_PREFERRED_LIFETIME, advance=None):
    """DESYNC_FACTOR 必须落在 [0, MAX_DESYNC_FACTOR] 且小于
    TEMP_PREFERRED_LIFETIME - REGEN_ADVANCE。"""
    if advance is None:
        advance = regen_advance()
    return 0 <= d <= max_desync_factor(preferred) and d < preferred - advance


# ---------------------------------------------------------------- §3.2.1 MD5 方案

def md5_iid(history_value, public_iid):
    """RFC 4941 §3.2.1：MD5(history || public_iid)。

    返回 (随机化 IID, 下一个 history value)。
    左 64 位清掉 bit 6 后作为 IID，右 64 位作为下一次的 history。
    """
    digest = hashlib.md5(history_value.to_bytes(8, "big")
                         + public_iid.to_bytes(8, "big")).digest()
    left = int.from_bytes(digest[:8], "big")
    right = int.from_bytes(digest[8:], "big")
    return (left & ~(UL_BIT_MASK << 56)) & ((1 << 64) - 1), right


def generate_iid_with_retries(history_value, public_iid, reserved,
                              retries=TEMP_IDGEN_RETRIES):
    """§3.3 step 7：DAD 冲突时把 history 换成 MD5 的右 64 位重来，最多 retries 次。

    返回 (IID, 新 history) 或 (None, None) 表示放弃。
    """
    hist, pub = history_value, public_iid
    for _ in range(retries):
        iid, nxt = md5_iid(hist, pub)
        if iid not in reserved:
            return iid, nxt
        hist = nxt                      # 用右 64 位替代 history 重来
    return None, None


def eui64_from_mac(mac_bytes):
    """RFC 4291 附录：MAC -> EUI-64（插入 fffe，翻转 U/L 位）。"""
    assert len(mac_bytes) == 6
    b = bytearray(mac_bytes[:3]) + b"\xff\xfe" + bytearray(mac_bytes[3:])
    b[0] ^= UL_BIT_MASK
    return int.from_bytes(bytes(b), "big")


# ---------------------------------------------------------------- §3.3 生命周期

def temp_address_lifetimes(prefix_valid, prefix_preferred, desync_factor=0):
    """§3.3 step 4：

    Valid Lifetime     = min(公开地址的 Valid Lifetime,     TEMP_VALID_LIFETIME)
    Preferred Lifetime = min(公开地址的 Preferred Lifetime,
                             TEMP_PREFERRED_LIFETIME - DESYNC_FACTOR)

    注意：只有 preferred 减 DESYNC_FACTOR，valid 不减。
    """
    valid = min(prefix_valid, TEMP_VALID_LIFETIME)
    preferred = min(prefix_preferred, TEMP_PREFERRED_LIFETIME - desync_factor)
    return valid, preferred


def should_create_temp_address(prefix_preferred, desync_factor=0, advance=None):
    """§3.3 step 5：只有算出来的 Preferred Lifetime 大于 REGEN_ADVANCE 才创建，
    且绝不能创建 Preferred Lifetime 为 0 的临时地址。"""
    if advance is None:
        advance = regen_advance()
    _, preferred = temp_address_lifetimes(prefix_preferred, prefix_preferred, desync_factor)
    return preferred > advance


def clamp_existing(creation_time, now, ra_preferred, desync_factor=0):
    """§3.3 step 2：更新已有临时地址的 preferred 时取
    min(RA 给的到期时刻, CREATION_TIME + TEMP_PREFERRED_LIFETIME - DESYNC_FACTOR)。"""
    ra_expiry = now + ra_preferred
    cap = creation_time + TEMP_PREFERRED_LIFETIME - desync_factor
    return min(ra_expiry, cap)


def regeneration_interval(desync_factor=0, advance=None):
    """§3.5：至少每 (TEMP_PREFERRED_LIFETIME - REGEN_ADVANCE - DESYNC_FACTOR)
    生成一次新的随机化接口标识符。"""
    if advance is None:
        advance = regen_advance()
    return TEMP_PREFERRED_LIFETIME - advance - desync_factor


def deprecated_after_regeneration():
    """§3.4：Preferred Lifetime 为 0 的 RA 触发废弃时**不得**生成新临时地址。"""
    return False


# ---------------------------------------------------------------- RFC 8981 §3.3.2

def rid_hmac_sha256(secret_key, prefix, net_iface, network_id, time_s, dad_counter):
    """RID = F(Prefix, Net_Iface, Network_ID, Time, DAD_Counter, secret_key)。

    文档点名 HMAC-SHA-256 是一种可接受的 F()。各输入按固定顺序拼接。
    """
    msg = b"|".join([
        prefix.to_bytes(16, "big"),
        net_iface,
        network_id,
        int(time_s).to_bytes(8, "big"),
        int(dad_counter).to_bytes(4, "big"),
    ])
    return hmac.new(secret_key, msg, hashlib.sha256).digest()


def iid_from_rid_low(rid):
    """§3.3.2 step 2：取 RID 中**从最低有效位开始**的所需位数。"""
    return int.from_bytes(rid[-IID_BYTES:], "big")


def iid_from_rid_high(rid):
    """对照口径：从最高有效位开始取（RFC 4941 的 MD5 方案就是取左 64 位）。"""
    return int.from_bytes(rid[:IID_BYTES], "big")


def generate_iid_rfc8981(secret_key, prefix, net_iface, network_id, time_s,
                         reserved, retries=TEMP_IDGEN_RETRIES):
    """§3.3.2 step 3：命中保留 IID 或已用 IID 时把 DAD_Counter 加 1 重来。"""
    counter = 0
    for _ in range(retries):
        rid = rid_hmac_sha256(secret_key, prefix, net_iface, network_id, time_s, counter)
        iid = iid_from_rid_low(rid)
        if iid not in reserved:
            return iid, counter
        counter += 1
    return None, counter
