"""演示 Web Push 的端到端加密：从订阅密钥到 aes128gcm 报文体。"""
import base64

import webpush
import p256

PLAIN = b'When I grow up, I want to be a watermelon'


def b64u(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()


def main():
    ua_private = 0x6a5b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f9 % p256.N
    ua_public = p256.point_to_bytes(p256.public_key(ua_private))
    auth_secret = b'0123456789abcdef'

    print('== 1. 订阅时用户代理交出两样东西 ==')
    print('ua_public  :', b64u(ua_public)[:44], '...  (%d 字节)' % len(ua_public))
    print('auth_secret:', b64u(auth_secret), '(%d 字节)' % len(auth_secret))

    print()
    print('== 2. 应用服务器每次发送都现造一对 ECDH 密钥和一个 salt ==')
    body, d = webpush.encrypt(ua_public, auth_secret, PLAIN)
    print('ecdh_secret:', b64u(d['ecdh_secret']))
    print('IKM        :', b64u(d['ikm']))
    print('CEK        :', b64u(d['cek']), '(%d 字节)' % len(d['cek']))
    print('NONCE      :', b64u(d['nonce']), '(%d 字节)' % len(d['nonce']))
    head = webpush.decode_header(body[:webpush.HEADER_FIXED + 65])
    print('header rs  :', head['rs'])
    print('header len :', webpush.HEADER_FIXED + 65)
    print('body 总长  :', len(body))

    print()
    print('== 3. 用户代理侧解密 ==')
    pt, _ = webpush.decrypt(ua_private, auth_secret, body)
    print('plaintext  :', pt)
    print('roundtrip  :', pt == PLAIN)

    print()
    print('== 4. rs 与填充的预算 ==')
    for rs in (4096, 128, 64):
        print('rs=%-5d 明文 %d 字节时可填 %d 字节'
              % (rs, len(PLAIN), webpush.padding_budget(rs, len(PLAIN))))
    print('4096 字节 body 下的明文上限:', webpush.max_plaintext())

    print()
    print('== 5. 换任何一个输入，解密都是认证失败而不是"解出乱码" ==')
    for label, fn in [
        ('换 auth_secret', lambda: webpush.decrypt(ua_private, b'ffffffffffffffff', body)),
        ('换 ua 私钥', lambda: webpush.decrypt((ua_private + 1) % p256.N, auth_secret, body)),
    ]:
        try:
            fn()
            print('  %-16s -> 竟然解开了' % label)
        except ValueError as exc:
            print('  %-16s -> ValueError: %s' % (label, exc))


if __name__ == '__main__':
    main()
