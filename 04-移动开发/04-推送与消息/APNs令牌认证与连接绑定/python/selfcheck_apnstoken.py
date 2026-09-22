"""APNs 令牌认证 demo 的自检。全部断言基于官方原文/RFC 向量，不用模型印象当真值。"""
import base64
import hashlib

import apns_jwt
import apns_token as policy
import p256

FAILS = []


def check(label, cond):
    if cond:
        print('  ok   -', label)
    else:
        FAILS.append(label)
        print('  FAIL -', label)


def eq(label, got, want):
    check('%s (got=%r)' % (label, got), got == want)


# ---------------------------------------------------------------- RFC 6979
def test_p256_vector():
    """RFC 6979 A.2.5 的 P-256 / SHA-256 / "sample" 向量。"""
    print('[1] P-256 与 ECDSA（RFC 6979 A.2.5）')
    d = 0xC9AFA9D845BA75166B5C215767B1D6934E50C3DB36E89B127B8A622B120F6721
    pub = p256.public_key(d)
    eq('公钥 Ux', '%064X' % pub[0],
       '60FED4BA255A9D31C961EB74C6356D68C049B8923B61FA6CE669622E60F29FB6')
    eq('公钥 Uy', '%064X' % pub[1],
       '7903FE1008B8BC99A41AE9E95628BC64F2F1B20C2D7E9F5177A3C294D4462299')
    r, s = p256.sign(d, b'sample')
    eq('签名 r', '%064X' % r,
       'EFD48B2AACB6A8FD1140DD9CD45E81D69D2C877B56AAF991C34D0EA84EAF3716')
    eq('签名 s', '%064X' % s,
       'F7CB1C942D657C41D436C7A1B6E29F65F3E900DBB9AFF4064DC4AB2F843ACDA8')
    check('同参数重签得到同结果（RFC 6979 确定性）',
          p256.sign(d, b'sample') == (r, s))
    check('验签通过', p256.verify(pub, b'sample', (r, s)))
    check('改一个字节后验签失败', not p256.verify(pub, b'Sample', (r, s)))
    other = p256.public_key(0x1111111111111111111111111111111111111111111111111111111111111111)
    check('换公钥验签失败', not p256.verify(other, b'sample', (r, s)))
    check('s 与 r 调换后验签失败', not p256.verify(pub, b'sample', (s, r)))
    check('r 取 0 是非法签名', not p256.verify(pub, b'sample', (0, s)))

    # bits2int 是"截左"：blen > qlen 时右移，而不是取低位
    long_in = b'\xff' * 40
    eq('bits2int 截左不截右', p256.bits2int(long_in),
       int.from_bytes(long_in, 'big') >> (40 * 8 - 256))
    check('bits2octets 先模 q 再定长',
          p256.bits2octets(b'\xff' * 40) != p256.int2octets(p256.bits2int(b'\xff' * 40)))
    eq('发明余远点', p256.add((p256.GX, p256.GY), (p256.GX, (-p256.GY) % p256.P)), None)
    check('G 在曲线上', p256.is_on_curve(p256.G))
    check('G 的阶是 n', p256.mul(p256.N, p256.G) is None)


# ---------------------------------------------------------------- JWT 形态
def test_jwt():
    print('[2] JWT 形态（RFC 7515/7518 + Apple 官方样例）')
    d = 0xC9AFA9D845BA75166B5C215767B1D6934E50C3DB36E89B127B8A622B120F6721
    pub = p256.public_key(d)
    tok = apns_jwt.make_token(d, 'ABC123DEFG', 'DEF123GHIJ', 1437179036)
    eq('三段式', len(tok.split('.')), 3)
    check('base64url 段内无 = 填充', '=' not in tok)
    eq('header.alg', apns_jwt.token_header(tok)['alg'], 'ES256')
    eq('header.kid', apns_jwt.token_header(tok)['kid'], 'ABC123DEFG')
    eq('claims.iss', apns_jwt.token_claims(tok)['iss'], 'DEF123GHIJ')
    eq('claims.iat', apns_jwt.token_iat(tok), 1437179036)
    eq('claims 只有两个键', sorted(apns_jwt.token_claims(tok)), ['iat', 'iss'])
    eq('签名值 64 字节', len(apns_jwt.b64u_decode(apns_jwt.split_token(tok)[2])), 64)
    check('验签通过', apns_jwt.verify_token(pub, tok))
    bad = tok[:-4] + 'AAAA'
    check('改签名后验签失败', not apns_jwt.verify_token(pub, bad))
    check('authorization 用小写 bearer',
          apns_jwt.authorization_header(tok).startswith('bearer '))

    # Apple 文档里的示例令牌：解码后能看出两处"示例里没按规矩来"
    h = 'eyAia2lkIjogIjhZTDNHM1JSWDciIH0'
    c = 'eyAiaXNzIjogIkM4Nk5WOUpYM0QiLCAiaWF0IjogIjE0NTkxNDM1ODA2NTAiIH0'
    hb = base64.urlsafe_b64decode(h + '=' * (-len(h) % 4))
    cb = base64.urlsafe_b64decode(c + '=' * (-len(c) % 4))
    check('官方样例 header 不含 alg', b'alg' not in hb)
    check('官方样例 iat 是字符串', b'"1459143580650"' in cb)
    eq('官方样例 iat 是毫秒量级（13 位）', len('1459143580650'), 13)
    check('官方样例 iat 转秒后远大于 1 小时窗口',
          apns_jwt.token_iat(h + '.' + c + '.x') > 10 ** 12)

    # 签名输入必须是 ASCII 的 header.claims，不是 JSON 原文
    si = apns_jwt.signing_input({'alg': 'ES256', 'kid': 'K'}, {'iss': 'I', 'iat': 1})
    check('签名输入以 base64url 段开头', si.startswith(b'eyJ'))
    raw = apns_jwt.signing_input({'alg': 'ES256', 'kid': 'K'}, {'iss': 'I', 'iat': 1})
    eq('签名输入与手工拼接一致', raw.decode('ascii'),
       '%s.%s' % (apns_jwt.b64u_encode(b'{"alg":"ES256","kid":"K"}'),
                  apns_jwt.b64u_encode(b'{"iss":"I","iat":1}')))
    eq('ES256 摘要长度', len(hashlib.sha256(b'x').digest()), 32)


# ---------------------------------------------------------------- 密钥作用域
def test_key_scope():
    print('[3] 密钥作用域配额（Apple 官方文档计数）')
    reg = policy.KeyRegistry()
    eq('team-scoped 每环境上限', policy.TEAM_KEYS_PER_ENV, 2)
    eq('topic-specific 每环境上限', policy.TOPIC_KEYS_PER_ENV, 200)
    eq('每把 topic key 的 topic 上限', policy.TOPICS_PER_TOPIC_KEY, 400)

    reg.add(policy.SigningKey('T1', 'team', 'production', 'TEAM'))
    reg.add(policy.SigningKey('T2', 'team', 'production', 'TEAM'))
    try:
        reg.add(policy.SigningKey('T3', 'team', 'production', 'TEAM'))
        check('第 3 把 production team key 被拒', False)
    except policy.KeyScopeError:
        check('第 3 把 production team key 被拒', True)
    reg.add(policy.SigningKey('T4', 'team', 'sandbox', 'TEAM'))
    check('sandbox 环境单独计数（第 1 把通过）', reg.get('T4') is not None)

    try:
        reg.add(policy.SigningKey('X1', 'team', 'production', 'TEAM',
                                  topics=['com.a']))
        check('team key 不允许带 topic 列表', False)
    except policy.KeyScopeError:
        check('team key 不允许带 topic 列表', True)
    try:
        reg.add(policy.SigningKey('X2', 'topic', 'production', 'TEAM'))
        check('topic key 必须有 topic', False)
    except policy.KeyScopeError:
        check('topic key 必须有 topic', True)
    try:
        reg.add(policy.SigningKey('X3', 'topic', 'production', 'TEAM',
                                  topics=['t%d' % i for i in range(401)]))
        check('401 个 topic 被拒', False)
    except policy.KeyScopeError:
        check('401 个 topic 被拒', True)

    reg.add(policy.SigningKey('P1', 'topic', 'production', 'TEAM', topics=['a']))
    reg.add(policy.SigningKey('P2', 'topic', 'production', 'TEAM', topics=['b'],
                              related='P1'))
    eq('related 关系被回写成双向', reg.get('P1').related, 'P2')
    try:
        reg.add(policy.SigningKey('P3', 'topic', 'production', 'TEAM', topics=['c'],
                                  related='P1'))
        check('一把 topic key 最多一个 related key', False)
    except policy.KeyScopeError:
        check('一把 topic key 最多一个 related key', True)
    try:
        reg.add(policy.SigningKey('P4', 'topic', 'sandbox', 'TEAM', topics=['c'],
                                  related='P1'))
        check('跨环境的 related key 被拒', False)
    except policy.KeyScopeError:
        check('跨环境的 related key 被拒', True)
    eq('team key 的可用 topic 是"任意"', policy.allowed_topics(reg.get('T1'), reg), None)


# ---------------------------------------------------------------- 刷新窗口
def test_refresh_window():
    print('[4] 刷新窗口 [20min, 60min]')
    T = 1459143580
    key = policy.SigningKey('T1', 'team', 'production', 'TEAM')
    reg = policy.KeyRegistry()
    reg.add(key)

    def verdicts(gap):
        c = policy.Connection('c')
        c.push(key, 'com.a', T, T, reg)
        return c.push(key, 'com.a', T + gap, T + gap, reg)

    eq('间隔 19 分钟 -> 429', verdicts(19 * 60)['reason'], 'TooManyProviderTokenUpdates')
    eq('间隔 20 分钟 -> 放行', verdicts(20 * 60)['status'], 'accepted')
    eq('间隔 59 分钟 -> 放行', verdicts(59 * 60)['status'], 'accepted')

    # 年龄判据是严格大于 3600 秒：整 1 小时还在窗口内
    c_age = policy.Connection('c-age')
    c_age.push(key, 'com.a', T, T, reg)
    eq('令牌年龄整 3600 秒仍放行',
       c_age.push(key, 'com.a', T + 3600, T, reg)['status'], 'accepted')

    c = policy.Connection('c2')
    c.push(key, 'com.a', T, T, reg)
    eq('iat 超过 1 小时 -> 403 ExpiredProviderToken',
       c.push(key, 'com.a', T + 3601, T, reg)['reason'], 'ExpiredProviderToken')
    eq('ExpiredProviderToken 的 http 是 403',
       c.push(key, 'com.a', T + 3601, T, reg)['http'], 403)
    eq('复用同一个令牌不算刷新',
       policy.Connection('c3').push(key, 'com.a', T, T, reg)['status'], 'accepted')
    c4 = policy.Connection('c4')
    c4.push(key, 'com.a', T, T, reg)
    eq('同一 iat 重复推送不触发 429',
       c4.push(key, 'com.a', T + 10, T, reg)['status'], 'accepted')


# ---------------------------------------------------------------- 连接绑定
def test_binding():
    print('[5] 首推之后的连接绑定')
    T = 1459143580
    reg = policy.KeyRegistry()
    team = policy.SigningKey('T1', 'team', 'production', 'TEAM')
    topic1 = policy.SigningKey('P1', 'topic', 'production', 'TEAM', topics=['com.a'])
    topic2 = policy.SigningKey('P2', 'topic', 'production', 'TEAM', topics=['com.b'],
                               related='P1')
    for k in (team, topic1, topic2):
        reg.add(k)

    c = policy.Connection('A')
    eq('team key 首推放行', c.push(team, 'com.a', T, T, reg)['status'], 'accepted')
    eq('绑定后 team 固定', c.team, 'TEAM')
    eq('绑定后环境固定', c.env, 'production')
    eq('team key 可推任意 topic', c.push(team, 'com.zzz', T, T, reg)['status'], 'accepted')
    eq('换成 topic key -> UnrelatedKeyIdInToken',
       c.push(topic1, 'com.a', T, T, reg)['reason'], 'UnrelatedKeyIdInToken')

    c2 = policy.Connection('B')
    c2.push(team, 'com.a', T, T, reg)
    sand = policy.SigningKey('S1', 'team', 'sandbox', 'TEAM')
    eq('同团队换环境 -> BadEnvironmentKeyIdInToken',
       c2.push(sand, 'com.a', T, T, reg)['reason'], 'BadEnvironmentKeyIdInToken')

    c3 = policy.Connection('C')
    c3.push(team, 'com.a', T, T, reg)
    other = policy.SigningKey('O1', 'team', 'production', 'OTHER')
    eq('换开发者账号 -> Forbidden',
       c3.push(other, 'com.a', T, T, reg)['reason'], 'Forbidden')
    eq('Forbidden 是 403', c3.push(other, 'com.a', T, T, reg)['http'], 403)

    c4 = policy.Connection('D')
    eq('topic key 首推放行', c4.push(topic1, 'com.a', T, T, reg)['status'], 'accepted')
    eq('topic key 推自己的 topic 放行',
       c4.push(topic1, 'com.a', T, T, reg)['status'], 'accepted')
    eq('topic key 推未关联 topic -> TopicDisallowed',
       c4.push(topic1, 'com.zzz', T, T, reg)['reason'], 'TopicDisallowed')
    eq('related key 可以在同一连接上用',
       c4.push(topic2, 'com.b', T, T, reg)['status'], 'accepted')
    eq('连接被拒绝后仍保持原绑定', c4.first_kid, 'P1')


def main():
    test_p256_vector()
    test_jwt()
    test_key_scope()
    test_refresh_window()
    test_binding()
    print()
    if FAILS:
        print('FAILED %d' % len(FAILS))
        for f in FAILS:
            print('  -', f)
        raise SystemExit(1)
    print('ALL PASS')


if __name__ == '__main__':
    main()
