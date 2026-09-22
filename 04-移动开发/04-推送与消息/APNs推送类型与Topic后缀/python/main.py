"""演示 11 个 apns-push-type 的 topic 后缀、优先级与平台约束。"""
import push_type as pt

BUNDLE = 'com.example.MyApp'


def main():
    print('== 1. 每个 push type 的 topic ==')
    for name in pt.PUSH_TYPES:
        try:
            topic = pt.topic_for(name, BUNDLE, mdm_topic='com.apple.mgmt.Example')
        except ValueError as exc:
            topic = '<需要证书里的 topic：%s>' % exc
        print('  %-13s -> %s' % (name, topic))

    print()
    print('== 2. 优先级约束 ==')
    for name, spec in pt.PUSH_TYPES.items():
        shown = '任意' if spec.priorities is None else '/'.join(map(str, spec.priorities))
        print('  %-13s priority=%s' % (name, shown))
    print('  background 用 priority 10 ->',
          pt.validate('background', BUNDLE, priority=10, bundle_id=BUNDLE))
    print('  alert 用 priority 1     ->',
          pt.validate('alert', BUNDLE, priority=1, bundle_id=BUNDLE))

    print()
    print('== 3. 平台可用性 ==')
    for name, spec in pt.PUSH_TYPES.items():
        avail = [p for p in pt.PLATFORMS if p not in spec.unavailable]
        print('  %-13s 可用: %s' % (name, ', '.join(avail)))
    print('  liveactivity 打到 macOS ->',
          pt.validate('liveactivity', pt.topic_for('liveactivity', BUNDLE),
                      platform='macOS', bundle_id=BUNDLE))

    print()
    print('== 4. 认证方式与载荷上限 ==')
    for name, spec in pt.PUSH_TYPES.items():
        auth = '任意' if spec.auth is None else '/'.join(spec.auth)
        print('  %-13s auth=%-6s payload=%d' % (name, auth, pt.payload_limit(name)))
    print('  location 用证书 ->',
          pt.validate('location', pt.topic_for('location', BUNDLE),
                      auth='cert', bundle_id=BUNDLE))

    print()
    print('== 5. topic 写错的后果（文档原话：可能报错、延迟投递、或直接丢弃）==')
    print('  voip 却用了裸 bundle ID ->',
          pt.validate('voip', BUNDLE, bundle_id=BUNDLE))


if __name__ == '__main__':
    main()
