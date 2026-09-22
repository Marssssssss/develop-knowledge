"""通知内容修改 demo 的自检：真值出自 Apple 官方文档原文。"""
import service_extension as se

FAILS = []


def check(label, cond):
    if cond:
        print('  ok   -', label)
    else:
        FAILS.append(label)
        print('  FAIL -', label)


def eq(label, got, want):
    check('%s (got=%r)' % (label, got), got == want)


CIPHER = 'PgAGBkUZSEsEDVMRCxdFAV4e'


def secret(**kw):
    return se.Request.secret(CIPHER, **kw)


def identity(text):
    return 'plain:' + text


def test_eligibility():
    print('[1] 扩展启用条件')
    ok, reason = se.extension_eligible(secret())
    check('合法加密通知启用扩展', ok and reason is None)
    eq('mutable-content 的键名', se.MUTABLE_CONTENT_KEY, 'mutable-content')
    eq('自定义数据键名', se.ENCRYPTED_DATA_KEY, 'ENCRYPTED_DATA')
    eq('官方占位文案', se.PLACEHOLDER, '(Encrypted)')

    off = se.Request.secret(CIPHER, alerts_enabled=False)
    eq('关掉 alert -> 不启用', se.extension_eligible(off),
       (False, 'alerts are disabled for your app'))
    for value in (0, '1', True, None):
        req = secret()
        req.aps['mutable-content'] = value
        eq('mutable-content=%r 不启用' % value,
           se.extension_eligible(req)[0], False)
    req = secret()
    req.aps['mutable-content'] = 1
    eq('只有数值 1 才启用', se.extension_eligible(req)[0], True)

    no_alert = se.Request({'mutable-content': 1, 'alert': {}}, {})
    eq('aps.alert 空字典 -> 不启用', se.extension_eligible(no_alert)[0], False)
    badge_only = se.Request({'mutable-content': 1, 'badge': 3}, {})
    eq('只有 badge -> 不启用', se.extension_eligible(badge_only)[0], False)
    sound_only = se.Request({'mutable-content': 1, 'sound': 'ding.aiff'}, {})
    eq('只有 sound -> 不启用', se.extension_eligible(sound_only)[0], False)
    subtitle_only = se.Request({'mutable-content': 1,
                                'alert': {'subtitle': 's'}}, {})
    eq('只有 subtitle 也启用', se.extension_eligible(subtitle_only)[0], True)
    str_alert = se.Request({'mutable-content': 1, 'alert': 'hello'}, {})
    eq('alert 是字符串时等价于 body', str_alert.alert(), {'body': 'hello'})


def test_official_payload():
    print('[2] 官方 Listing 2 的载荷形状')
    req = secret()
    eq('category', req.aps['category'], 'SECRET')
    eq('mutable-content', req.aps['mutable-content'], 1)
    eq('alert.title', req.aps['alert']['title'], 'Secret Message!')
    eq('alert.body 是占位', req.aps['alert']['body'], '(Encrypted)')
    eq('密文在 aps 之外', req.custom['ENCRYPTED_DATA'], CIPHER)
    check('密文不在 aps 里', 'ENCRYPTED_DATA' not in req.aps)


def test_happy_path():
    print('[3] 正常路径')
    ext = se.ServiceExtension(decrypt=identity)
    req = secret()
    eq('解密后 delivered', ext.did_receive(req)['delivered'], 'modified')
    fresh = se.ServiceExtension(decrypt=identity)
    check('解密标志为真', fresh.did_receive(secret())['decrypted'] is True)
    out = ext.outcome(req)
    eq('最终内容是明文', out['content']['body'], 'plain:' + CIPHER)
    eq('标题保留', out['content']['title'], 'Secret Message!')
    check('handler 只被调用过一次', ext.handler_called)


def test_decrypt_failure():
    print('[4] 解密失败回落占位')
    ext = se.ServiceExtension()
    req = secret()
    res = ext.did_receive(req, decrypt_ok=False)
    eq('仍然走 modified', res['delivered'], 'modified')
    check('decrypted 为假', res['decrypted'] is False)
    eq('body 变成占位', ext.outcome(req)['content']['body'], '(Encrypted)')

    no_data = se.Request({'mutable-content': 1, 'alert': {'body': 'hi'}}, {})
    ext2 = se.ServiceExtension(decrypt=identity)
    res2 = ext2.did_receive(no_data)
    eq('没有密文字段时直接交回', res2['decrypted'], False)
    eq('内容保持不变', ext2.outcome(no_data)['content']['body'], 'hi')


def test_timeout():
    print('[5] 30 秒预算与超时降级')
    eq('官方给的是 about 30 秒', se.DEFAULT_BUDGET, 30.0)
    ext = se.ServiceExtension(decrypt=identity)
    eq('预算设置', ext.budget, 30.0)

    # 成对构造：只差 elapsed 一个开关
    before = se.ServiceExtension(decrypt=identity)
    r_before = before.did_receive(secret(), elapsed=29.9)
    check('29.9 秒不算超时', 'timed_out' not in r_before)
    check('29.9 秒时 from_time_will_expire 为假',
          r_before['from_time_will_expire'] is False)

    at = se.ServiceExtension(decrypt=identity)
    r_at = at.did_receive(secret(), elapsed=30.0)
    check('30.0 秒触发超时', r_at.get('timed_out') is True)
    check('超时后由 time_will_expire 交回', r_at['from_time_will_expire'] is True)
    req = secret()
    at2 = se.ServiceExtension(decrypt=identity)
    at2.did_receive(req, elapsed=30.0)
    out = at2.outcome(req)
    eq('超时内容 body 被清空', out['content']['body'], '')
    eq('超时内容 subtitle 标成占位', out['content']['subtitle'], '(Encrypted)')
    eq('超时仍然算 modified 而不是 original', out['delivered'], 'modified')


def test_no_handler():
    print('[6] 两个方法都没调 handler -> 展示原始内容')
    ext = se.ServiceExtension()
    ext.time_will_expire()
    req = secret(body='RAW-CIPHERTEXT')
    out = ext.outcome(req)
    eq('delivered 是 original', out['delivered'], 'original')
    eq('展示的是原始 body（密文）', out['content']['body'], 'RAW-CIPHERTEXT')

    ext2 = se.ServiceExtension()
    eq('连 time_will_expire 都没触发时也是 original',
       ext2.outcome(req)['delivered'], 'original')

    ext3 = se.ServiceExtension()
    ext3.time_will_expire()
    ext3.complete({'body': 'degraded'})
    eq('time_will_expire 之后还是可以交回内容',
       ext3.outcome(req)['content']['body'], 'degraded')

    ext4 = se.ServiceExtension()
    ext4.complete({'body': 'x'})
    try:
        ext4.complete({'body': 'y'})
        check('completion handler 只能调一次', False)
    except RuntimeError:
        check('completion handler 只能调一次', True)

    ext5 = se.ServiceExtension()
    ext5.complete({'body': 'x'})
    eq('已经交回后再收到 time_will_expire 不会覆盖',
       ext5.time_will_expire(), {'body': 'x'})


def main():
    test_eligibility()
    test_official_payload()
    test_happy_path()
    test_decrypt_failure()
    test_timeout()
    test_no_handler()
    print()
    if FAILS:
        print('FAILED %d' % len(FAILS))
        for f in FAILS:
            print('  -', f)
        raise SystemExit(1)
    print('ALL PASS')


if __name__ == '__main__':
    main()
