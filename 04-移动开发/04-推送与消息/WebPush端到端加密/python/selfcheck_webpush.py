"""Web Push 加密 demo 的自检：真值全部取自 RFC 8291 附录 A 的官方中间值。"""
import base64
import hashlib
import hmac

import aesgcm
import p256
import webpush

FAILS = []


def check(label, cond):
    if cond:
        print('  ok   -', label)
    else:
        FAILS.append(label)
        print('  FAIL -', label)


def eq(label, got, want):
    check('%s (got=%r)' % (label, got), got == want)


def b64d(text):
    return base64.urlsafe_b64decode(''.join(text.split()) + '=' * (-len(''.join(text.split())) % 4))


def b64u(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()


# RFC 8291 §5 + Appendix A 的输入
AUTH = b64d('BTBZMqHH6r4Tts7J_aSIgg')
UA_PRIV = int.from_bytes(b64d('q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94'), 'big')
UA_PUB = b64d('BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcx'
              'aOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4')
AS_PRIV = int.from_bytes(b64d('yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw'), 'big')
AS_PUB = b64d('BP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIg'
              'Dll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8')
SALT = b64d('DGv6ra1nlYgDCS1FRnbzlw')
PLAIN = b'When I grow up, I want to be a watermelon'
CT = b64d('8pfeW0KbunFT06SuDKoJH9Ql87S1QUrdirN6GcG7sFz1y1sqLgVi1VhjVkHsUoEs'
          'bI_0LpXMuGvnzQ')


def test_inputs():
    print('[1] 官方输入的形态')
    eq('auth_secret 16 字节', len(AUTH), 16)
    eq('salt 16 字节', len(SALT), 16)
    eq('ua_public 65 字节', len(UA_PUB), 65)
    eq('as_public 65 字节', len(AS_PUB), 65)
    eq('未压缩点以 0x04 开头', UA_PUB[0], 0x04)
    eq('as_public 与私钥一致', p256.point_to_bytes(p256.public_key(AS_PRIV)), AS_PUB)
    eq('ua_public 与私钥一致', p256.point_to_bytes(p256.public_key(UA_PRIV)), UA_PUB)
    check('ua_public 在曲线上', p256.is_on_curve(p256.bytes_to_point(UA_PUB)))
    eq('明文长度', len(PLAIN), 41)


def test_ecdh():
    print('[2] ECDH（RFC 8291 §3.1）')
    s_as = webpush.ecdh(AS_PRIV, UA_PUB)
    s_ua = webpush.ecdh(UA_PRIV, AS_PUB)
    eq('共享 secret', b64u(s_as), 'kyrL1jIIOHEzg3sM2ZWRHDRB62YACZhhSlknJ672kSs')
    eq('两侧算出的 secret 相同', s_as, s_ua)
    eq('共享 secret 是 32 字节的 X 坐标', len(s_as), 32)


def test_derivation():
    print('[3] 密钥派生（RFC 8291 §3.4）')
    d = webpush.derive(webpush.ecdh(AS_PRIV, UA_PUB), AUTH, UA_PUB, AS_PUB, SALT)
    eq('PRK_key', b64u(d['prk_key']),
       'Snr3JMxaHVDXHWJn5wdC52WjpCtd2EIEGBykDcZW32k')
    eq('IKM', b64u(d['ikm']),
       'S4lYMb_L0FxCeq0WhDx813KgSYqU26kOyzWUdsXYyrg')
    eq('CEK', b64u(d['cek']), 'oIhVW04MRdy2XN9CiKLxTg')
    eq('NONCE', b64u(d['nonce']), '4h_95klXJ5E_qnoN')
    eq('CEK 长度 16', len(d['cek']), 16)
    eq('NONCE 长度 12', len(d['nonce']), 12)
    eq('IKM 长度 32（SHA-256 输出）', len(d['ikm']), 32)
    eq('key_info 前缀', d['key_info'][:13], b'WebPush: info')
    eq('key_info 的第 14 字节是 0', d['key_info'][13], 0)
    eq('key_info = 前缀||0||ua_pub||as_pub',
       d['key_info'], b'WebPush: info\x00' + UA_PUB + AS_PUB)
    eq('key_info 总长', len(d['key_info']), 13 + 1 + 65 + 65)

    # 顺序敏感：把两个公钥调换，IKM 必须不同
    swapped = webpush.derive(webpush.ecdh(AS_PRIV, UA_PUB), AUTH, AS_PUB, UA_PUB, SALT)
    check('ua_pub / as_pub 顺序反了 IKM 就变', swapped['ikm'] != d['ikm'])
    # auth_secret 参与派生：换成别的 auth 得到不同 CEK
    other = webpush.derive(webpush.ecdh(AS_PRIV, UA_PUB), b'\x00' * 16, UA_PUB, AS_PUB, SALT)
    check('auth_secret 变了 CEK 就变', other['cek'] != d['cek'])
    # HKDF-Expand 只有一轮时的 info 尾巴是 0x01
    eq('cek_info', base64.b64encode(b'Content-Encoding: aes128gcm\x00'),
       b'Q29udGVudC1FbmNvZGluZzogYWVzMTI4Z2NtAA==')
    eq('nonce_info', base64.b64encode(b'Content-Encoding: nonce\x00'),
       b'Q29udGVudC1FbmNvZGluZzogbm9uY2UA')
    eq('手动 HKDF-Extract 与 webpush.hkdf_extract 一致',
       webpush.hkdf_extract(SALT, d['ikm']),
       hmac.new(SALT, d['ikm'], hashlib.sha256).digest())


def test_header_and_body():
    print('[4] aes128gcm 头部与报文体（RFC 8188 §2.1 / RFC 8291 §4）')
    body, d = webpush.encrypt(UA_PUB, AUTH, PLAIN, as_private=AS_PRIV, salt=SALT)
    head = webpush.decode_header(body[:webpush.HEADER_FIXED + 65])
    eq('header 86 字节', webpush.HEADER_FIXED + 65, 86)
    eq('salt 字段', head['salt'], SALT)
    eq('rs 字段', head['rs'], 4096)
    eq('idlen 字段', len(head['keyid']), 65)
    eq('keyid 就是 as_public', head['keyid'], AS_PUB)
    eq('header 与官方值逐字节相同',
       b64u(body[:86]),
       'DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27ml'
       'mlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8')
    eq('密文+tag 与官方值逐字节相同', b64u(body[86:]), b64u(CT))
    eq('body 总长', len(body), 86 + len(PLAIN) + 1 + 16)

    pt, _ = webpush.decrypt(UA_PRIV, AUTH, body)
    eq('解密回原文', pt, PLAIN)
    eq('加分隔符后的记录', b64u(PLAIN + b'\x02'),
       'V2hlbiBJIGdyb3cgdXAsIEkgd2FudCB0byBiZSBhIHdhdGVybWVsb24C')


def test_aesgcm_layer():
    print('[5] AES-128-GCM 层')
    d = webpush.derive(webpush.ecdh(AS_PRIV, UA_PUB), AUTH, UA_PUB, AS_PUB, SALT)
    eq('gcm_encrypt 复现官方密文', aesgcm.gcm_encrypt(d['cek'], d['nonce'], PLAIN + b'\x02'), CT)
    eq('gcm_decrypt 还原记录', aesgcm.gcm_decrypt(d['cek'], d['nonce'], CT), PLAIN + b'\x02')
    eq('AAD 为空（本内容编码不认证 HTTP 头）', len(b''), 0)
    try:
        aesgcm.gcm_decrypt(d['cek'], b'\x00' * 12, CT)
        check('换 nonce 解不开', False)
    except ValueError:
        check('换 nonce 解不开', True)
    tampered = bytearray(CT)
    tampered[0] ^= 1
    try:
        aesgcm.gcm_decrypt(d['cek'], d['nonce'], bytes(tampered))
        check('改一个密文位就认证失败', False)
    except ValueError:
        check('改一个密文位就认证失败', True)
    eq('S 盒第 0 项', aesgcm.SBOX[0], 0x63)
    eq('S 盒第 1 项', aesgcm.SBOX[1], 0x7c)
    eq('S 盒第 255 项', aesgcm.SBOX[255], 0x16)


def test_limits():
    print('[6] 单记录、rs 与长度上限')
    eq('单记录下 nonce 不再与序号异或（序号为 0）',
       bytes(a ^ b for a, b in zip(webpush.derive(
           webpush.ecdh(AS_PRIV, UA_PUB), AUTH, UA_PUB, AS_PUB, SALT)['nonce'],
           (0).to_bytes(12, 'big'))),
       webpush.derive(webpush.ecdh(AS_PRIV, UA_PUB), AUTH, UA_PUB, AS_PUB, SALT)['nonce'])
    eq('4096 body 下的明文上限是 3993', webpush.max_plaintext(), 3993)
    eq('上限推导：4096-86-1-16', 4096 - 86 - 1 - 16, 3993)
    eq('rs=4096 / 41 字节明文的可填充量', webpush.padding_budget(4096, 41), 4038)
    eq('rs=64 / 41 字节明文的可填充量', webpush.padding_budget(64, 41), 6)
    eq('rs=58 / 41 字节明文刚好装下', webpush.padding_budget(58, 41), 0)
    check('rs=57 装不下（预算为负）', webpush.padding_budget(57, 41) < 0)
    try:
        webpush.encrypt(UA_PUB, AUTH, PLAIN, rs=57)
        check('rs 太小时 encrypt 报错', False)
    except ValueError:
        check('rs 太小时 encrypt 报错', True)

    padded, _ = webpush.encrypt(UA_PUB, AUTH, PLAIN, pad=200)
    eq('带填充的 body 长度', len(padded), 86 + 41 + 1 + 200 + 16)
    eq('带填充也能解回原文', webpush.decrypt(UA_PRIV, AUTH, padded)[0], PLAIN)


def test_record_and_auth():
    print('[7] 记录拆分与认证失败')
    eq('parse_record 取最后一个 0x02 之前的内容',
       webpush.parse_record(b'abc\x02\x00\x00')[0], b'abc')
    eq('明文里含 0x02 也能正确拆分',
       webpush.parse_record(b'a\x02b\x02\x00')[0], b'a\x02b')
    try:
        webpush.parse_record(b'abc\x00\x00')
        check('分隔符不是 0x02 时丢弃', False)
    except ValueError:
        check('分隔符不是 0x02 时丢弃', True)

    body, _ = webpush.encrypt(UA_PUB, AUTH, PLAIN)
    for label, args in [
        ('auth_secret 不对', (UA_PRIV, b'\x11' * 16, body)),
        ('私钥不对', ((UA_PRIV + 1) % p256.N, AUTH, body)),
    ]:
        try:
            webpush.decrypt(*args)
            check('%s 时解密失败' % label, False)
        except ValueError:
            check('%s 时解密失败' % label, True)
    eq('正确凭据仍能解开', webpush.decrypt(UA_PRIV, AUTH, body)[0], PLAIN)

    try:
        p256.bytes_to_point(b'\x04' + b'\x00' * 64)
        check('不在曲线上的 keyid 被拒', False)
    except ValueError:
        check('不在曲线上的 keyid 被拒', True)
    try:
        p256.bytes_to_point(b'\x05' + b'\x01' * 64)
        check('只接受 0x04 未压缩点', False)
    except ValueError:
        check('只接受 0x04 未压缩点', True)


def main():
    test_inputs()
    test_ecdh()
    test_derivation()
    test_header_and_body()
    test_aesgcm_layer()
    test_limits()
    test_record_and_auth()
    print()
    if FAILS:
        print('FAILED %d' % len(FAILS))
        for f in FAILS:
            print('  -', f)
        raise SystemExit(1)
    print('ALL PASS')


if __name__ == '__main__':
    main()
