"""APNs push type demo 的自检：真值来自 Apple DocC 原文（termList 定义列表）。"""
import push_type as pt

FAILS = []


def check(label, cond):
    if cond:
        print('  ok   -', label)
    else:
        FAILS.append(label)
        print('  FAIL -', label)


def eq(label, got, want):
    check('%s (got=%r)' % (label, got), got == want)


BUNDLE = 'com.example.MyApp'


def test_catalog():
    print('[1] 取值集合')
    eq('共 11 个 push type', len(pt.PUSH_TYPES), 11)
    eq('取值集合', sorted(pt.PUSH_TYPES),
       sorted(['alert', 'background', 'complication', 'controls', 'fileprovider',
               'liveactivity', 'location', 'mdm', 'pushtotalk', 'voip', 'widgets']))
    for name in pt.PUSH_TYPES:
        check('%s 已知' % name, pt.is_known(name))
    check('未知取值被识别', not pt.is_known('alert2'))
    check('controls / widgets 的官方文案没给平台约束',
          pt.PUSH_TYPES['controls'].unavailable == frozenset()
          and pt.PUSH_TYPES['widgets'].unavailable == frozenset())


def test_topics():
    print('[2] topic 后缀')
    eq('alert 用裸 bundle ID', pt.topic_for('alert', BUNDLE), BUNDLE)
    eq('background 用裸 bundle ID', pt.topic_for('background', BUNDLE), BUNDLE)
    eq('complication', pt.topic_for('complication', BUNDLE), BUNDLE + '.complication')
    eq('controls', pt.topic_for('controls', BUNDLE), BUNDLE + '.push-type.controls')
    eq('fileprovider', pt.topic_for('fileprovider', BUNDLE),
       BUNDLE + '.pushkit.fileprovider')
    eq('liveactivity', pt.topic_for('liveactivity', BUNDLE),
       BUNDLE + '.push-type.liveactivity')
    eq('location', pt.topic_for('location', BUNDLE), BUNDLE + '.location-query')
    eq('pushtotalk', pt.topic_for('pushtotalk', BUNDLE), BUNDLE + '.voip-ptt')
    eq('voip', pt.topic_for('voip', BUNDLE), BUNDLE + '.voip')
    eq('widgets', pt.topic_for('widgets', BUNDLE), BUNDLE + '.push-type.widgets')
    eq('mdm 的 topic 来自证书 subject 的 UID',
       pt.topic_for('mdm', BUNDLE, mdm_topic='com.apple.mgmt.X'), 'com.apple.mgmt.X')
    try:
        pt.topic_for('mdm', BUNDLE)
        check('mdm 缺证书 topic 时报错', False)
    except ValueError:
        check('mdm 缺证书 topic 时报错', True)

    # 官方那张表的字面量与更权威写法不一致的两处
    eq('complication 的官方字面量是 h.complication',
       pt.PUSH_TYPES['complication'].suffix_doc, 'h.complication')
    eq('liveactivity 的官方字面量不带前导点',
       pt.PUSH_TYPES['liveactivity'].suffix_doc, 'push-type.liveactivity')
    check('两处不一致都被登记', pt.has_doc_discrepancy('complication')
          and pt.has_doc_discrepancy('liveactivity'))
    check('其余 type 没有不一致登记',
          not any(pt.has_doc_discrepancy(n) for n in
                  ('alert', 'background', 'voip', 'widgets', 'pushtotalk',
                   'location', 'fileprovider', 'controls', 'mdm')))
    eq('按字面量拼出来的 liveactivity topic 少一个点',
       pt.topic_for('liveactivity', BUNDLE, canonical=False),
       BUNDLE + 'push-type.liveactivity')


def test_priority():
    print('[3] 优先级')
    eq('省略 apns-priority 时默认 10', pt.DEFAULT_PRIORITY, 10)
    eq('background 只允许 5', pt.PUSH_TYPES['background'].priorities, (5,))
    check('background 用 10 报错',
          pt.validate('background', BUNDLE, priority=10) != [])
    check('background 用 5 通过',
          pt.validate('background', BUNDLE, priority=5, bundle_id=BUNDLE) == [])
    check('background 用 1 报错',
          pt.validate('background', BUNDLE, priority=1) != [])
    eq('alert 允许 10 与 5', pt.PUSH_TYPES['alert'].priorities, (10, 5))
    check('alert 用 10 通过', pt.validate('alert', BUNDLE, priority=10, bundle_id=BUNDLE) == [])
    check('alert 用 5 通过', pt.validate('alert', BUNDLE, priority=5, bundle_id=BUNDLE) == [])
    check('alert 用 1 报错', pt.validate('alert', BUNDLE, priority=1) != [])
    eq('location 允许 10 与 5', pt.PUSH_TYPES['location'].priorities, (10, 5))
    check('location 用 5 通过',
          pt.validate('location', pt.topic_for('location', BUNDLE), priority=5,
                      bundle_id=BUNDLE) == [])
    unconstrained = [n for n, s in pt.PUSH_TYPES.items() if s.priorities is None]
    eq('文档没约束优先级的 type 有 8 个', len(unconstrained), 8)
    check('voip 的优先级文档未约束', pt.PUSH_TYPES['voip'].priorities is None)
    check('未约束时不因 priority 报错',
          pt.validate('voip', pt.topic_for('voip', BUNDLE), priority=1,
                      bundle_id=BUNDLE) == [])


def test_platform():
    print('[4] 平台可用性')
    eq('complication 不可用平台集合',
       pt.PUSH_TYPES['complication'].unavailable,
       frozenset(('macOS', 'tvOS', 'iPadOS')))
    eq('liveactivity 不可用平台集合',
       pt.PUSH_TYPES['liveactivity'].unavailable,
       frozenset(('watchOS', 'macOS', 'tvOS')))
    eq('location 不可用平台集合',
       pt.PUSH_TYPES['location'].unavailable,
       frozenset(('macOS', 'tvOS', 'watchOS')))
    eq('pushtotalk 不可用平台集合',
       pt.PUSH_TYPES['pushtotalk'].unavailable,
       frozenset(('watchOS', 'macOS', 'tvOS')))
    eq('fileprovider / mdm / voip 都只在 watchOS 不可用',
       [sorted(pt.PUSH_TYPES[n].unavailable) for n in ('fileprovider', 'mdm', 'voip')],
       [['watchOS'], ['watchOS'], ['watchOS']])
    eq('alert / background 全平台可用',
       [pt.PUSH_TYPES[n].unavailable for n in ('alert', 'background')],
       [frozenset(), frozenset()])
    eq('watchOS 上 alert / background 是 required',
       [sorted(pt.PUSH_TYPES[n].required_on) for n in ('alert', 'background')],
       [['watchOS'], ['watchOS']])
    check('liveactivity 打到 macOS 报错',
          pt.validate('liveactivity', pt.topic_for('liveactivity', BUNDLE),
                      platform='macOS') != [])
    check('voip 打到 macOS 不报错',
          pt.validate('voip', pt.topic_for('voip', BUNDLE), platform='macOS') == [])
    check('location 打到 watchOS 报错',
          pt.validate('location', pt.topic_for('location', BUNDLE),
                      platform='watchOS') != [])
    for plat in pt.PLATFORMS:
        for name, spec in pt.PUSH_TYPES.items():
            if spec.recommended and plat not in spec.recommended \
                    and plat not in spec.unavailable and plat not in spec.required_on:
                check('%s 在 %s 既非 recommended 也非 unavailable/required' % (name, plat), False)


def test_auth_and_payload():
    print('[5] 认证方式与载荷上限')
    eq('只有 location 限定 token-based',
       [n for n, s in pt.PUSH_TYPES.items() if s.auth == ('token',)], ['location'])
    check('location 用证书被拒',
          pt.validate('location', pt.topic_for('location', BUNDLE), auth='cert') != [])
    check('location 用 token 通过',
          pt.validate('location', pt.topic_for('location', BUNDLE), auth='token') == [])
    eq('voip 载荷 5120', pt.payload_limit('voip'), 5120)
    eq('其余载荷 4096', sorted({pt.payload_limit(n) for n in pt.PUSH_TYPES
                                if n != 'voip'}), [4096])
    eq('complication 的证书扩展',
       pt.PUSH_TYPES['complication'].cert_ext, (pt.CERT_EXT_WATCHKIT,))
    eq('voip 的证书扩展有两个',
       pt.PUSH_TYPES['voip'].cert_ext, (pt.CERT_EXT_VOIP, pt.CERT_EXT_WATCHKIT))
    eq('WatchKit 扩展 OID', pt.CERT_EXT_WATCHKIT, '1.2.840.113635.100.6.3.6')
    eq('VoIP 扩展 OID', pt.CERT_EXT_VOIP, '1.2.840.113635.100.6.3.4')
    check('mdm 的 topic 来源有说明',
          'UID' in pt.PUSH_TYPES['mdm'].cert_topic_from)


def test_validate():
    print('[6] 组合校验')
    check('voip 用裸 bundle ID 被判 topic 不符',
          pt.validate('voip', BUNDLE, bundle_id=BUNDLE) != [])
    check('未知 push type 直接报错',
          pt.validate('nope', BUNDLE)[0].startswith('unknown'))
    check('alert + 正确 topic + priority 10 全通过',
          pt.validate('alert', BUNDLE, priority=10, platform='iOS',
                      auth='token', bundle_id=BUNDLE) == [])
    check('不传 bundle_id 时不做 topic 校验',
          pt.validate('voip', BUNDLE) == [])
    # 成对构造：只差 priority 一个开关
    good = pt.validate('background', BUNDLE, priority=5, bundle_id=BUNDLE)
    bad = pt.validate('background', BUNDLE, priority=10, bundle_id=BUNDLE)
    check('background 的 5 与 10 必须给出不同结论', good != bad)


def main():
    test_catalog()
    test_topics()
    test_priority()
    test_platform()
    test_auth_and_payload()
    test_validate()
    print()
    if FAILS:
        print('FAILED %d' % len(FAILS))
        for f in FAILS:
            print('  -', f)
        raise SystemExit(1)
    print('ALL PASS')


if __name__ == '__main__':
    main()
