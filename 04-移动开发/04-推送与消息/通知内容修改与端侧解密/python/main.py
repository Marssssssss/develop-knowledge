"""演示 Notification Service Extension 的启用条件、解密与超时降级。"""
import base64

import service_extension as se


def xor_decrypt(key):
    """演示用的"解密"：真实实现应换成 AEAD（例如 RFC 8291 那套）。"""

    def _decrypt(text):
        raw = base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))
        return bytes(b ^ key[i % len(key)] for i, b in enumerate(raw)).decode('utf-8')

    return _decrypt


def encrypt(text, key):
    raw = text.encode('utf-8')
    enc = bytes(b ^ key[i % len(key)] for i, b in enumerate(raw))
    return base64.urlsafe_b64encode(enc).rstrip(b'=').decode()


KEY = b'secret-key'


def main():
    cipher = encrypt('Meet me at the usual place', KEY)
    print('== 1. 官方 Listing 2 的加密载荷 ==')
    req = se.Request.secret(cipher)
    print('  aps    :', req.aps)
    print('  custom :', {k: (v[:24] + '...') for k, v in req.custom.items()})
    print('  启用?  :', se.extension_eligible(req))

    print()
    print('== 2. 正常路径：解密成功，替换 body ==')
    ext = se.ServiceExtension(decrypt=xor_decrypt(KEY))
    r = ext.did_receive(req)
    print(' ', r)
    print('  最终   :', ext.outcome(req))

    print()
    print('== 3. 解密失败：官方示例回落到 "(Encrypted)" ==')
    ext2 = se.ServiceExtension(decrypt=xor_decrypt(b'wrong-key'))
    try:
        ext2.did_receive(req)
    except Exception:
        pass
    print('  官方示例里失败时 body 置为 (Encrypted)')
    ext3 = se.ServiceExtension()
    print(' ', ext3.did_receive(req, decrypt_ok=False))
    print('  最终   :', ext3.outcome(req))

    print()
    print('== 4. 超时：`serviceExtensionTimeWillExpire()` 里还得交回内容 ==')
    ext4 = se.ServiceExtension(decrypt=xor_decrypt(KEY))
    print(' ', ext4.did_receive(req, elapsed=30.0))
    print('  最终   :', ext4.outcome(req))

    print()
    print('== 5. 两个方法都没调 handler -> 系统展示**原始**内容（密文）==')
    ext5 = se.ServiceExtension()
    ext5.time_will_expire()          # 系统催了，但实现忘了调 handler
    print(' ', ext5.outcome(req))

    print()
    print('== 6. 什么情况下扩展根本不会被调用 ==')
    cases = [
        ('关掉 alert', se.Request.secret(cipher, alerts_enabled=False)),
        ('没有 mutable-content',
         se.Request(dict((k, v) for k, v in
                              se.Request.secret(cipher).aps.items()
                              if k != se.MUTABLE_CONTENT_KEY), {})),
        ('aps.alert 没有标题/副标题/正文',
         se.Request({'mutable-content': 1, 'alert': {}},
                         {se.ENCRYPTED_DATA_KEY: cipher})),
        ('只有 badge', se.Request({'mutable-content': 1, 'badge': 3}, {})),
    ]
    for label, r2 in cases:
        print('  %-28s -> %s' % (label, se.extension_eligible(r2)))


if __name__ == '__main__':
    main()
