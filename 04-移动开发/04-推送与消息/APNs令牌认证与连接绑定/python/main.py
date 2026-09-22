"""演示 APNs provider token 的签发、刷新窗口与连接绑定判定。"""
import apns_jwt
import apns_token as policy
import p256

TEAM = 'C86NV9JX3D'
TOPICS = ['com.example.MyApp', 'com.example.MyApp.watch']
T0 = 1459143580


def build_registry():
    """按官方配额登记三把钥匙：1 把 team-scoped + 一对 related topic key。"""
    reg = policy.KeyRegistry()
    reg.add(policy.SigningKey('TEAMKEY01', 'team', 'production', TEAM))
    reg.add(policy.SigningKey('TOPICKEY1', 'topic', 'production', TEAM,
                              topics=['com.example.MyApp']))
    reg.add(policy.SigningKey('TOPICKEY2', 'topic', 'production', TEAM,
                              topics=['com.example.MyApp.watch'],
                              related='TOPICKEY1'))
    return reg


def issue_token(d, kid, iat):
    return apns_jwt.make_token(d, kid, TEAM, iat)


def main():
    d = 0xC9AFA9D845BA75166B5C215767B1D6934E50C3DB36E89B127B8A622B120F6721
    pub = p256.public_key(d)
    reg = build_registry()

    print('== 1. 签一个真正的 ES256 provider token ==')
    tok = issue_token(d, 'TEAMKEY01', T0)
    head, claims, sig = tok.split('.')
    print('header  :', head[:40], '...')
    print('claims  :', claims)
    print('alg/kid :', apns_jwt.token_header(tok))
    print('iss/iat :', apns_jwt.token_claims(tok))
    print('verify  :', apns_jwt.verify_token(pub, tok))
    print('authz   :', apns_jwt.authorization_header(tok)[:28], '...')

    print()
    print('== 2. 同一连接上的刷新窗口 ==')
    conn = policy.Connection('conn-a')
    key = reg.get('TEAMKEY01')
    print('first push      :', conn.push(key, TOPICS[0], T0, T0, reg))
    print('+10min 换令牌   :', conn.push(key, TOPICS[0], T0 + 600, T0 + 600, reg))
    print('+30min 换令牌   :', conn.push(key, TOPICS[0], T0 + 1800, T0 + 1800, reg))
    print('iat 2 小时前    :', conn.push(key, TOPICS[0], T0 + 9000, T0, reg))

    print()
    print('== 3. 首推之后连接被绑死 ==')
    conn2 = policy.Connection('conn-b')
    print('team key 首推   :', conn2.push(reg.get('TEAMKEY01'), TOPICS[0], T0, T0, reg))
    print('换 topic key    :', conn2.push(reg.get('TOPICKEY1'), TOPICS[0], T0, T0, reg))
    other = policy.SigningKey('OTHERTEAM', 'team', 'production', 'DEF123GHIJ')
    print('换团队          :', conn2.push(other, TOPICS[0], T0, T0, reg))

    print()
    print('== 4. topic-specific 首推 + related key ==')
    conn3 = policy.Connection('conn-c')
    print('topic key 首推  :', conn3.push(reg.get('TOPICKEY1'), TOPICS[0], T0, T0, reg))
    print('related key     :', conn3.push(reg.get('TOPICKEY2'), TOPICS[1], T0, T0, reg))
    print('非关联 topic    :', conn3.push(reg.get('TOPICKEY1'), 'com.example.Other', T0, T0, reg))


if __name__ == '__main__':
    main()
