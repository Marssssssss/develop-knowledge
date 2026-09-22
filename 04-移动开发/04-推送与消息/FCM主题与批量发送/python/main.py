"""演示 FCM 的主题名规范化、主题管理、TTL 编码与批量扇出。"""
import datetime

import fcm_batch as batch
import fcm_topic as topic


def main():
    print('== 1. topic 名规范化 ==')
    for raw in ['news', '/topics/news', 'News-2026_09', 'a b', '', None, 'foo:bar']:
        try:
            print('  %-16r -> %r' % (raw, topic.sanitize_topic_name(raw)))
        except ValueError as exc:
            print('  %-16r -> ValueError: %s' % (raw, exc))

    print()
    print('== 2. 主题订阅请求（IID batchAdd）==')
    req = topic.build_topic_request(['tok-1', 'tok-2'], 'news', 'iid/v1:batchAdd')
    print('  url    :', req['url'])
    print('  headers:', req['headers'])
    print('  body   :', req['json'])
    req2 = topic.build_topic_request('tok-3', '/topics/news', 'iid/v1:batchRemove')
    print('  退订   :', req2['url'], req2['json']['to'])

    print()
    print('== 3. 主题管理响应：成功与失败按 results 逐条计数 ==')
    resp = topic.TopicManagementResponse({
        'results': [{}, {'error': 'INVALID_ARGUMENT'}, {}]})
    print('  success=%d failure=%d' % (resp.success_count, resp.failure_count))
    for err in resp.errors:
        print('  error  : index=%d reason=%s' % (err.index, err.reason))

    print()
    print('== 4. TTL 编码 ==')
    for ttl in [3, 3.5, datetime.timedelta(seconds=120), 0, -1, '3s']:
        try:
            print('  %-28r -> %r' % (ttl, topic.encode_ttl(ttl)))
        except ValueError as exc:
            print('  %-28r -> ValueError: %s' % (ttl, exc))

    print()
    print('== 5. 目标必须恰好一个 ==')
    for kwargs in [{'token': 't'}, {'topic': 'news'}, {'token': 't', 'topic': 'news'},
                   {'condition': "'a' in topics"}, {}]:
        try:
            print('  %-34s -> %s' % (kwargs, batch.encode_message(**kwargs)))
        except ValueError as exc:
            print('  %-34s -> ValueError: %s' % (kwargs, exc))

    print()
    print('== 6. send_each 的扇出：单条失败不影响其它 ==')
    msgs = [{'token': 't%d' % i} for i in range(4)]

    def transport(i):
        if i == 1:
            raise RuntimeError('UNAVAILABLE')
        return {'name': 'projects/p/messages/%d' % i}

    resp_batch, plan = batch.send_each(msgs, transport)
    print('  max_workers:', plan['max_workers'], ' 并发:', plan['concurrent'])
    print('  success=%d failure=%d' % (resp_batch.success_count, resp_batch.failure_count))
    for i, r in enumerate(resp_batch.responses):
        print('   #%d success=%-5s message_id=%s' % (i, r.success, r.message_id))

    print()
    print('== 7. 未登记的 FCM 错误码不分类 ==')
    for code in ['UNREGISTERED', 'QUOTA_EXCEEDED', 'INTERNAL']:
        print('  %-16s -> %r' % (code, topic.classify_fcm_error(code)))


if __name__ == '__main__':
    main()
