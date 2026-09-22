"""FCM 主题与批量 demo 的自检：真值来自 firebase-admin-python 官方源码。"""
import datetime

import fcm_batch as batch
import fcm_topic as topic

FAILS = []


def check(label, cond):
    if cond:
        print('  ok   -', label)
    else:
        FAILS.append(label)
        print('  FAIL -', label)


def eq(label, got, want):
    check('%s (got=%r)' % (label, got), got == want)


def raises(label, fn, fragment=None):
    try:
        fn()
    except ValueError as exc:
        if fragment is None or fragment in str(exc):
            check(label, True)
            return
        check('%s (message=%s)' % (label, exc), False)
        return
    check(label + ' <- 没有抛异常', False)


def test_topic_name():
    print('[1] topic 名规范化（sanitize_topic_name）')
    eq('plain', topic.sanitize_topic_name('news'), 'news')
    eq('剥掉 /topics/ 前缀', topic.sanitize_topic_name('/topics/news'), 'news')
    raises('前缀只剥一次，剩下的 / 会被字符集拒掉',
           lambda: topic.sanitize_topic_name('/topics//topics/a'))
    eq('空串返回 None 而不是报错', topic.sanitize_topic_name(''), None)
    eq('None 返回 None', topic.sanitize_topic_name(None), None)
    raises('含空格被拒', lambda: topic.sanitize_topic_name('a b'), 'Malformed topic name.')
    raises('含冒号被拒', lambda: topic.sanitize_topic_name('foo:bar'))
    raises('只有前缀被拒（正则要求 1 个以上字符）',
           lambda: topic.sanitize_topic_name('/topics/'))
    for good in ('a', 'A', '0', '-', '_', '.', '~', '%', 'a-b_c.d~%e'):
        check('合法字符 %r' % good, topic.sanitize_topic_name(good) == good)
    # `9-_` 里的 '-' 是字面量，不是区间起点：0x3a~0x40 不该匹配
    for bad in (':', ';', '<', '=', '>', '?', '@'):
        raises('区间字符 %r 不在合法集内' % bad,
               lambda b=bad: topic.sanitize_topic_name(b))
    check('正则里 "-" 确实是字面量',
          topic.TOPIC_NAME_RE.match('-') is not None
          and topic.TOPIC_NAME_RE.match(':') is None)


def test_validators():
    print('[2] 校验器（_Validators）')
    eq('check_string 返回 None', topic.check_string('L', None), None)
    eq('check_string 原样返回', topic.check_string('L', 'x'), 'x')
    raises('check_string 非字符串', lambda: topic.check_string('L', 1),
           'must be a string.')
    raises('check_string 非空约束', lambda: topic.check_string('L', '', non_empty=True),
           'must be a non-empty string.')
    eq('check_string_list 空表原样', topic.check_string_list('L', []), None)
    eq('check_string_list None 原样', topic.check_string_list('L', None), None)
    raises('check_string_list 含空串',
           lambda: topic.check_string_list('L', ['a', '']))
    raises('check_string_list 非列表', lambda: topic.check_string_list('L', 'a'))


def test_topic_request():
    print('[3] 主题管理请求（make_topic_management_request）')
    req = topic.build_topic_request(['t1', 't2'], 'news', 'iid/v1:batchAdd')
    eq('url', req['url'], 'https://iid.googleapis.com/iid/v1:batchAdd')
    eq('前缀被补上', req['json']['to'], '/topics/news')
    eq('token 列表', req['json']['registration_tokens'], ['t1', 't2'])
    eq('IID 需要 access_token_auth 头', req['headers'], {'access_token_auth': 'true'})
    single = topic.build_topic_request('t3', '/topics/n', 'iid/v1:batchRemove')
    eq('单个字符串会被包成列表', single['json']['registration_tokens'], ['t3'])
    eq('已有前缀不重复添加', single['json']['to'], '/topics/n')
    eq('退订端点', single['url'], 'https://iid.googleapis.com/iid/v1:batchRemove')
    raises('空 token 列表', lambda: topic.build_topic_request([], 'n', 'iid/v1:batchAdd'),
           'Tokens must be a string or a non-empty list of strings.')
    raises('token 含空串', lambda: topic.build_topic_request(['a', ''], 'n', 'iid/v1:batchAdd'),
           'Tokens must be non-empty strings.')
    raises('空 topic', lambda: topic.build_topic_request(['a'], '', 'iid/v1:batchAdd'),
           'Topic must be a non-empty string.')
    raises('未知 operation', lambda: topic.build_topic_request(['a'], 'n', 'iid/v1:batchX'))
    # topic 名本身不受字符集校验（只有 MessageEncoder 路径才校验）
    check('IID 路径不校验 topic 字符集',
          topic.build_topic_request(['a'], 'bad name', 'iid/v1:batchAdd')['json']['to']
          == '/topics/bad name')


def test_topic_response():
    print('[4] 主题管理响应（TopicManagementResponse）')
    resp = topic.TopicManagementResponse({'results': [{}, {'error': 'X'}, {}]})
    eq('success_count', resp.success_count, 2)
    eq('failure_count', resp.failure_count, 1)
    eq('errors 长度', len(resp.errors), 1)
    eq('error 的 index 是 results 里的下标', resp.errors[0].index, 1)
    eq('error 的 reason 直接取 error 字段', resp.errors[0].reason, 'X')
    empty = topic.TopicManagementResponse({'results': []})
    eq('空 results 全 0', (empty.success_count, empty.failure_count), (0, 0))
    raises('缺 results 键', lambda: topic.TopicManagementResponse({}),
           'Unexpected topic management response')


def test_ttl():
    print('[5] TTL 编码（encode_ttl）')
    eq('None', topic.encode_ttl(None), None)
    eq('整数秒', topic.encode_ttl(3), '3s')
    eq('0 秒', topic.encode_ttl(0), '0s')
    eq('timedelta', topic.encode_ttl(datetime.timedelta(seconds=120)), '120s')
    eq('带小数走 9 位纳秒', topic.encode_ttl(3.5), '3.500000000s')
    eq('小于 1 秒', topic.encode_ttl(0.25), '0.250000000s')
    # timedelta 只保到微秒：1.000000001 会被舍成整 1 秒，所以用 1 微秒的残留来验证
    # 浮点残差：(1.000001-1)*1e9 在二进制浮点下是 999.999...，int() 直接砍成 999
    eq('floor 到秒后补 9 位纳秒', topic.encode_ttl(1.000001), '1.000000999s')
    eq('纳秒精度不足 1 微秒时回到整秒', topic.encode_ttl(1.000000001), '1s')
    raises('负数', lambda: topic.encode_ttl(-1), 'must not be negative.')
    raises('字符串不接受', lambda: topic.encode_ttl('3s'), 'must be a duration')


def test_target():
    print('[6] 目标恰好一个（MessageEncoder.default）')
    eq('token', batch.encode_message(token='t'), {'token': 't'})
    eq('topic 会被规范化', batch.encode_message(topic='/topics/news'), {'topic': 'news'})
    eq('condition', batch.encode_message(condition="'a' in topics"),
       {'condition': "'a' in topics"})
    eq('fid', batch.encode_message(fid='f'), {'fid': 'f'})
    raises('两个目标', lambda: batch.encode_message(token='t', topic='news'),
           'Exactly one of fid, token, topic or condition must be specified.')
    raises('零个目标', lambda: batch.encode_message(),
           'Exactly one of fid, token, topic or condition must be specified.')
    raises('topic 是空串时等价于没给', lambda: batch.encode_message(topic=''),
           'Exactly one of fid, token, topic or condition must be specified.')
    # 成对：只差一个目标
    check('token 与 token+topic 必须给出不同结论',
          batch.encode_message(token='t') !=
          _safe(lambda: batch.encode_message(token='t', topic='n')))


def _safe(fn):
    try:
        return fn()
    except ValueError:
        return 'ValueError'


def test_send_each():
    print('[7] 批量发送（send_each）')
    eq('条数上限', batch.SEND_EACH_LIMIT, 500)
    raises('超过 500 条', lambda: batch.plan_send_each([{}] * 501),
           'must not contain more than 500 elements.')
    raises('非列表', lambda: batch.plan_send_each('abc'), 'must be a list')
    raises('空列表会让 max_workers=0', lambda: batch.plan_send_each([]),
           'max_workers must be greater than 0')
    plan = batch.plan_send_each([{'token': 'a'}, {'token': 'b'}])
    eq('max_workers = 条数', plan['max_workers'], 2)
    eq('并发发送', plan['concurrent'], True)
    eq('每条独立一个请求体', plan['requests'],
       [{'message': {'token': 'a'}}, {'message': {'token': 'b'}}])
    eq('dry_run 走 validate_only',
       batch.message_data({'token': 'a'}, dry_run=True),
       {'message': {'token': 'a'}, 'validate_only': True})

    ok = batch.SendResponse({'name': 'projects/p/messages/1'})
    check('有 name 即成功', ok.success)
    eq('message_id 取 name', ok.message_id, 'projects/p/messages/1')
    raises('响应体缺 name 直接报错', lambda: batch.SendResponse({}),
           'Unexpected batch response')
    raises('响应体不是 dict 也报错', lambda: batch.SendResponse('x'),
           'Unexpected batch response')
    raised = batch.SendResponse(resp=None, exception=RuntimeError('UNAVAILABLE'))
    check('有异常即失败', not raised.success)
    eq('失败时 message_id 为 None', raised.message_id, None)

    msgs = [{'token': 't%d' % i} for i in range(3)]

    def flaky(i):
        if i == 2:
            raise RuntimeError('INTERNAL')
        return {'name': 'projects/p/messages/%d' % i}

    resp, _ = batch.send_each(msgs, flaky)
    eq('success_count', resp.success_count, 2)
    eq('failure_count', resp.failure_count, 1)
    check('第 3 条失败', not resp.responses[2].success)
    check('前两条成功', resp.responses[0].success and resp.responses[1].success)
    eq('结果条数与输入一致', len(resp.responses), 3)

    def all_fail(i):
        raise RuntimeError('UNREGISTERED')

    resp2, _ = batch.send_each(msgs, all_fail)
    eq('全失败时 success=0', resp2.success_count, 0)
    eq('全失败时 failure=3', resp2.failure_count, 3)


def test_errors():
    print('[8] FCM 错误码分类')
    eq('UNREGISTERED', topic.classify_fcm_error('UNREGISTERED'), 'UnregisteredError')
    eq('QUOTA_EXCEEDED', topic.classify_fcm_error('QUOTA_EXCEEDED'), 'QuotaExceededError')
    eq('SENDER_ID_MISMATCH', topic.classify_fcm_error('SENDER_ID_MISMATCH'),
       'SenderIdMismatchError')
    eq('APNS_AUTH_ERROR 与 THIRD_PARTY_AUTH_ERROR 同类',
       topic.classify_fcm_error('APNS_AUTH_ERROR'),
       topic.classify_fcm_error('THIRD_PARTY_AUTH_ERROR'))
    eq('未登记的错误码返回 None', topic.classify_fcm_error('INTERNAL'), None)
    eq('官方只登记了 5 个错误码', len(topic.FCM_ERROR_TYPES), 5)
    eq('collapse key 同时最多 4 个（官方 docstring）', topic.MAX_ACTIVE_COLLAPSE_KEYS, 4)


def main():
    test_topic_name()
    test_validators()
    test_topic_request()
    test_topic_response()
    test_ttl()
    test_target()
    test_send_each()
    test_errors()
    print()
    if FAILS:
        print('FAILED %d' % len(FAILS))
        for f in FAILS:
            print('  -', f)
        raise SystemExit(1)
    print('ALL PASS')


if __name__ == '__main__':
    main()
