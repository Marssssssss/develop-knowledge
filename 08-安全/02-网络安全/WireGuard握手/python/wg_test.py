"""
WireGuard 握手 / cookie / 传输路径的自检（真跑，断言全部对照规范与内核行为）

运行:  python wg_test.py
"""

from __future__ import annotations

import wg_crypto_vec
from wg_crypto import x25519_public
from wg_cookie import (COUNTER_WINDOW_SIZE, ReplayCounter, cookie_reply,
                       data_message, keypair_expired, make_cookie, open_cookie,
                       open_data, should_rekey)
from wg_kdf import blake2s, tai64n_now
from wg_noise import (COOKIE_LEN, HANDSHAKE_NAME, IDENTIFIER_NAME, LEN_COOKIE,
                      LEN_DATA_HEADER, LEN_INITIATION, LEN_RESPONSE,
                      MAX_TIMER_HANDSHAKES, REJECT_AFTER_TIME, REKEY_AFTER_TIME,
                      Peer, check_mac1, compute_mac1, compute_mac2,
                      consume_initiation, consume_response, create_initiation,
                      create_response, handshake_init, mac1_key, macs_for)


def _main() -> int:
    checks = wg_crypto_vec.run(0)          # 密码学原语的 RFC 官方向量先跑一遍

    # ---- 1. 协议常量与初始化 -------------------------------------------------
    ck0, h0 = handshake_init()
    assert HANDSHAKE_NAME == b"Noise_IKpsk2_25519_ChaChaPoly_BLAKE2s"
    assert len(HANDSHAKE_NAME) == 37 and len(IDENTIFIER_NAME) == 34
    assert ck0 == blake2s(HANDSHAKE_NAME)
    assert h0 == blake2s(ck0 + IDENTIFIER_NAME) and h0 != ck0
    checks += 4

    # ---- 2. 消息字节数必须等于内核结构体 sizeof -----------------------------
    assert LEN_INITIATION == 148, LEN_INITIATION      # 148 = 4+4+32+48+28+32
    assert LEN_RESPONSE == 92, LEN_RESPONSE           # 92  = 4+4+4+32+16+32
    assert LEN_COOKIE == 64, LEN_COOKIE               # 64  = 4+4+24+32
    assert LEN_DATA_HEADER == 16                      # 4+4+8
    checks += 4

    # ---- 3. 一轮完整握手 ----------------------------------------------------
    a_priv, b_priv = b"\x11" * 32, b"\x22" * 32
    a_pub, b_pub = x25519_public(a_priv), x25519_public(b_priv)
    psk = b"\x33" * 32
    alice = Peer(a_priv, a_pub, b_pub, psk)           # 发起方
    bob = Peer(b_priv, b_pub, a_pub, psk)             # 响应方
    eph_a, eph_b = b"\x44" * 32, b"\x55" * 32

    msg1, sy_a, eph_a_pub = create_initiation(alice, eph_a, 0x0A0B0C0D)
    assert len(msg1) == LEN_INITIATION
    table = {a_pub: alice}                            # 响应方按静态公钥查对端表
    matched, sy_b, idx, init_static = consume_initiation(
        bob, msg1, lambda pk: table.get(pk))
    assert matched is alice and init_static == a_pub and idx == 0x0A0B0C0D
    checks += 4

    msg2, sy_b2 = create_response(bob, eph_a_pub, a_pub, sy_b, idx, 0x01020304, eph_b)
    assert len(msg2) == LEN_RESPONSE
    k_ar, k_ra = consume_response(alice, sy_a, eph_a, msg2)
    r1, r2 = sy_b2.split()
    # 双方 ck 相同 ⇒ 导出同一对密钥；且方向映射相反
    assert (r1, r2) == (k_ar, k_ra) and k_ar != k_ra and len(k_ar) == 32
    checks += 3

    # ---- 4. PSK 不一致必须失败（psk 经 MixKeyAndHash 混进 ck 与 k）----------
    alice_bad = Peer(a_priv, a_pub, b_pub, b"\x99" * 32)
    _, sy_bad, _ = create_initiation(alice_bad, eph_a, 7)
    try:
        consume_response(alice_bad, sy_bad, eph_a, msg2)
        raise AssertionError("PSK mismatch must fail")
    except ValueError:
        checks += 1

    # ---- 5. 篡改与重放 ------------------------------------------------------
    bad = bytearray(msg1)
    bad[40] ^= 0x01                                   # 改 encrypted_static 一个位
    try:
        consume_initiation(bob, bytes(bad), lambda pk: table.get(pk))
        raise AssertionError("tampered initiation must fail")
    except ValueError:
        checks += 1
    # MAC1 只对目标响应方成立：换一个静态公钥即失败
    other = x25519_public(b"\x66" * 32)
    assert check_mac1(msg1, b_pub) and not check_mac1(msg1, other)
    assert mac1_key(b_pub) != mac1_key(other)
    checks += 3
    # 未知发起方：解密出的静态公钥不在对端表里 → 丢弃
    try:
        consume_initiation(bob, msg1, lambda pk: None)
        raise AssertionError("unknown initiator must be dropped")
    except ValueError:
        checks += 1
    # 时间戳必须严格递增（对端记录的是「发起方上次的 TAI64N」）
    ts_old = tai64n_now(1_700_000_000)
    msg_ts, _, _ = create_initiation(alice, eph_a, 9, timestamp=ts_old)
    alice.latest_timestamp = ts_old
    try:
        consume_initiation(bob, msg_ts, lambda pk: table.get(pk))
        raise AssertionError("stale timestamp must be rejected")
    except ValueError:
        checks += 1
    # 换成更新的时间戳即可通过（同一对端、同一条消息结构）
    alice.latest_timestamp = tai64n_now(1_699_000_000)
    ok_peer, _, _, _ = consume_initiation(bob, msg_ts, lambda pk: table.get(pk))
    assert ok_peer is alice
    checks += 1

    # ---- 6. cookie 机制 -----------------------------------------------------
    secret = b"\x77" * 32
    c1 = make_cookie(secret, b"\x0a\x00\x00\x01", 51820)
    c2 = make_cookie(secret, b"\x0a\x00\x00\x01", 51821)
    assert c1 != c2 and len(c1) == COOKIE_LEN
    assert make_cookie(b"\x88" * 32, b"\x0a\x00\x00\x01", 51820) != c1
    checks += 3
    cm, cookie_plain = cookie_reply(msg1, idx, secret, b_pub,
                                    b"\x0a\x00\x00\x01", 51820)
    assert len(cm) == LEN_COOKIE and cookie_plain == c1
    # 只有持有正确静态私钥对应的公钥才能解开（AD 是发起消息里的 MAC1）
    assert open_cookie(cm, b_pub, msg1[-32:-16]) == c1
    try:
        open_cookie(cm, other, msg1[-32:-16])
        raise AssertionError("cookie must be bound to responder static key")
    except ValueError:
        checks += 1
    # MAC2 必须由 cookie 算出，全零 MAC2 与真 MAC2 不同
    m1 = compute_mac1(msg1, b_pub)
    body = msg1[:-32]
    assert compute_mac2(body + m1 + b"\x00" * 16, c1) != b"\x00" * 16
    assert macs_for(body, b_pub, c1)[16:] == compute_mac2(body + m1 + b"\x00" * 16, c1)
    assert macs_for(body, b_pub)[16:] == b"\x00" * COOKIE_LEN
    checks += 4

    # ---- 7. 传输路径：填充、往返与篡改 --------------------------------------
    dm = data_message(k_ar, 7, b"hello wireguard")
    assert len(dm) == LEN_DATA_HEADER + 16 + 16       # 15 字节明文补到 16
    assert open_data(k_ar, 7, dm) == b"hello wireguard" + bytes(1)
    assert len(data_message(k_ar, 8, b"x" * 16)) == LEN_DATA_HEADER + 32
    assert open_data(k_ra, 9, data_message(k_ra, 9, b"x" * 17)) == (
        b"x" * 17 + bytes(15))
    tampered = bytearray(dm)
    tampered[-1] ^= 0x01
    try:
        open_data(k_ar, 7, bytes(tampered))
        raise AssertionError("tampered data must fail")
    except ValueError:
        checks += 2
    try:
        open_data(k_ra, 7, dm)                        # 方向密钥不同，解不开
        raise AssertionError("wrong direction key must fail")
    except ValueError:
        checks += 1

    # ---- 8. 重放窗口 --------------------------------------------------------
    rc = ReplayCounter()
    assert rc.validate(0) and not rc.validate(0)
    assert rc.validate(1) and rc.validate(2) and not rc.validate(2)
    assert rc.validate(COUNTER_WINDOW_SIZE + 5)       # 跳窗合法
    assert not rc.validate(1)                         # 跳窗后旧号出窗 → 拒
    assert rc.validate(COUNTER_WINDOW_SIZE + 4)       # 窗内未用过 → 收
    checks += 7
    # 边界：恰好落后 COUNTER_WINDOW_SIZE 号仍在窗内
    rc2 = ReplayCounter()
    assert rc2.validate(COUNTER_WINDOW_SIZE) and rc2.validate(0)
    rc3 = ReplayCounter()
    assert rc3.validate(COUNTER_WINDOW_SIZE + 1) and not rc3.validate(0)
    checks += 2

    # ---- 9. 定时器与失效判据 ------------------------------------------------
    assert REKEY_AFTER_TIME == 120 and REJECT_AFTER_TIME == 180
    assert MAX_TIMER_HANDSHAKES == 18 and REKEY_AFTER_TIME < REJECT_AFTER_TIME
    assert not should_rekey(0, 119, 0) and should_rekey(0, 121, 0)
    assert should_rekey(0, 1, (1 << 60) + 1) and not should_rekey(0, 1, 1 << 60)
    assert not keypair_expired(0, 179, 0) and keypair_expired(0, 181, 0)
    checks += 6

    print(f"wg_test: {checks} checks passed (含 wg_crypto_vec 的原语向量)")

    return checks


if __name__ == "__main__":
    _main()
