"""Same-Origin Policy 与 CORS 演示(浏览器安全基石)
5 demo:
- Demo 1: 同源判定(scheme/host/port 三元组)
- Demo 2: Simple Request vs Preflight 分类
- Demo 3: CORS 响应头校验(凭据 + * 拒绝)
- Demo 4: 预检响应构造(OPTIONS 完整头集合)
- Demo 5: Vary: Origin 必要性(防 CDN 缓存串味)
"""
from urllib.parse import urlparse

# ───────────── Demo 1: 同源判定 ─────────────

DEFAULT_PORTS = {'http': 80, 'https': 443, 'ws': 80, 'wss': 443}


def port_of(url: str) -> int:
    p = urlparse(url)
    return p.port if p.port else DEFAULT_PORTS.get(p.scheme, 0)


def is_same_origin(url_a: str, url_b: str) -> tuple[bool, str]:
    a, b = urlparse(url_a), urlparse(url_b)
    if a.scheme != b.scheme:
        return False, f'不同 scheme({a.scheme} vs {b.scheme})'
    if a.hostname.lower() != b.hostname.lower():
        return False, f'不同 host({a.hostname} vs {b.hostname})'
    pa, pb = port_of(url_a), port_of(url_b)
    if pa != pb:
        return False, f'不同 port({pa} vs {pb})'
    return True, '同源'


def demo_same_origin():
    print('─' * 65)
    print('[Demo 1] 同源判定(scheme + host + port 三元组)')
    print('─' * 65)
    cases = [
        ('https://app.example.com/a', 'https://app.example.com/b'),
        ('https://app.example.com/a', 'http://app.example.com/a'),
        ('https://app.example.com/a', 'https://api.example.com/a'),
        ('https://app.example.com/a', 'https://APP.example.com/a'),
        ('https://app.example.com/a', 'https://app.example.com:8080/a'),
        ('http://app.example.com/a',  'http://app.example.com:80/a'),
        ('https://app.example.com',   'https://app.example.com:443/'),
    ]
    for a, b in cases:
        ok, reason = is_same_origin(a, b)
        marker = '✅ 同源' if ok else '❌ 跨源'
        print(f'  {marker}  {a}')
        print(f'           vs {b}')
        print(f'           → {reason}')
    print()
    print('  → SOP 严格遵循三元组:scheme/host/port 任一不同 = 跨源')
    print('  → host 大小写不敏感(APP.example.com == app.example.com)')
    print()


# ───────────── Demo 2: Simple Request vs Preflight ─────────────

CORS_SAFELISTED_HEADERS = {'accept', 'accept-language', 'content-language',
                            'range'}
CORS_SAFELISTED_CONTENT_TYPES = {'application/x-www-form-urlencoded',
                                  'multipart/form-data', 'text/plain'}
SAFE_METHODS = {'GET', 'HEAD', 'POST'}


def classify_request(method: str, content_type: str | None,
                     custom_headers: list[str]) -> tuple[str, str]:
    """返回 ('simple', reason) 或 ('preflight', reason)。"""
    if method not in SAFE_METHODS:
        return 'preflight', f'非安全方法({method})→ 预检'
    if custom_headers:
        non_safelist = [h.lower() for h in custom_headers if h.lower() not in CORS_SAFELISTED_HEADERS]
        if non_safelist:
            return 'preflight', f'自定义头({non_safelist})不在 safelist → 预检'
    if content_type and content_type.lower() not in CORS_SAFELISTED_CONTENT_TYPES:
        return 'preflight', f'Content-Type({content_type})不在 safelist → 预检'
    return 'simple', 'GET/HEAD/POST + safelisted headers + safelisted Content-Type'


def demo_classify():
    print('─' * 65)
    print('[Demo 2] Simple Request vs Preflight 分类')
    print('─' * 65)
    cases = [
        ('GET',  None,                                       [],                       'GET 简单 GET'),
        ('POST', 'application/x-www-form-urlencoded',        [],                       '表单提交'),
        ('POST', 'application/json',                          [],                       'JSON API 提交'),
        ('POST', 'application/json',                          ['X-Auth-Token'],        'JSON + 自定义头'),
        ('PUT',  None,                                       [],                       'PUT 方法'),
        ('DELETE', None,                                     [],                       'DELETE 方法'),
        ('POST', 'multipart/form-data',                       ['X-Request-Id'],        'multipart + 自定义头'),
    ]
    for m, ct, headers, ctx in cases:
        kind, reason = classify_request(m, ct, headers)
        marker = '✈️ SIMPLE  ' if kind == 'simple' else '🔄 PREFLIGHT'
        print(f'  {marker}  {ctx}')
        print(f'              {reason}')
    print()


# ───────────── Demo 3: CORS 响应头校验(凭据 + 通配符拒绝) ─────────────

def validate_cors_response(req_origin: str,
                           acao: str,
                           acac: str | None,
                           method: str = 'GET') -> tuple[bool, str]:
    """模拟浏览器 CORS 校验(MDN 强制规则)。"""
    # 1. ACAO 必须存在
    if not acao:
        return False, 'missing Access-Control-Allow-Origin'

    # 2. 凭据请求硬约束(★ MDN 强制)
    if acac == 'true':
        if acao == '*':
            return False, '凭据请求 + ACAO=* → 浏览器拒绝(MDN 硬约束)'
        if acao != req_origin:
            return False, f'凭据请求必须具体 origin(ACAO={acao!r} ≠ req={req_origin!r})'
        return True, f'凭据请求 + 具体 origin({acao}) → 通过'

    # 3. 非凭据请求:ACAO=* 或具体 origin 均可
    if acao == '*':
        return True, '非凭据请求 + ACAO=* → 通过(通配符允许)'
    if acao == req_origin:
        return True, f'非凭据请求 + 具体 origin({acao}) → 通过'
    return False, f'ACAO({acao!r}) ≠ req Origin({req_origin!r}) → 拒绝'


def demo_cors_response_check():
    print('─' * 65)
    print('[Demo 3] CORS 响应头校验(MDN 凭据 + 通配符硬约束)')
    print('─' * 65)
    cases = [
        ('https://app.example.com', '*',           None,     '简单 GET, 通配符'),
        ('https://app.example.com', 'https://app.example.com', None, '简单 GET, 具体 origin'),
        ('https://app.example.com', 'https://evil.com', None, '简单 GET, 但 origin 不匹配'),
        ('https://app.example.com', '*',           'true',  '凭据请求 + 通配符(★ 强制拒绝)'),
        ('https://app.example.com', 'https://app.example.com', 'true', '凭据 + 具体 origin'),
        ('https://app.example.com', 'https://other.com', 'true', '凭据 + 不匹配 origin'),
        ('https://app.example.com', None,          'true',  '凭据 + 缺失 ACAO'),
    ]
    for origin, acao, acac, ctx in cases:
        ok, reason = validate_cors_response(origin, acao, acac)
        marker = '✅ ALLOW' if ok else '🛑 BLOCK'
        print(f'  {marker}  {ctx}')
        print(f'        {reason}')
    print()


# ───────────── Demo 4: 预检响应构造 ─────────────

def build_preflight_response(req_origin: str,
                             req_method: str,
                             req_headers: list[str],
                             allowed_origins: set[str],
                             allowed_methods: set[str],
                             allowed_headers: set[str],
                             max_age: int = 86400) -> dict[str, str]:
    """构造合法预检响应。"""
    headers = {}

    # ACAO
    if req_origin not in allowed_origins:
        return {'status': '403', 'body': 'Origin not allowed'}
    headers['Access-Control-Allow-Origin'] = req_origin
    headers['Vary'] = 'Origin'   # 必须,防 CDN 缓存串味

    # ACAM
    if not (allowed_methods & {req_method}):
        return {'status': '403', 'body': f'Method {req_method} not allowed'}
    headers['Access-Control-Allow-Methods'] = ', '.join(sorted(allowed_methods))

    # ACAH
    bad_headers = [h for h in req_headers if h.lower() not in {x.lower() for x in allowed_headers}]
    if bad_headers:
        return {'status': '403', 'body': f'Headers {bad_headers} not allowed'}
    headers['Access-Control-Allow-Headers'] = ', '.join(sorted(allowed_headers))

    # Max-Age
    headers['Access-Control-Max-Age'] = str(max_age)

    return {'status': '200', 'headers': headers}


def demo_preflight():
    print('─' * 65)
    print('[Demo 4] 预检响应构造(OPTIONS 完整头集合)')
    print('─' * 65)
    allowed_origins  = {'https://app.example.com'}
    allowed_methods  = {'GET', 'POST', 'DELETE'}
    allowed_headers  = {'Content-Type', 'X-Auth-Token', 'Authorization'}

    cases = [
        ('https://app.example.com', 'DELETE', ['X-Auth-Token'],         '合法:同源 + DELETE + 自定义头'),
        ('https://evil.com',        'DELETE', ['X-Auth-Token'],         '跨源:ACAO 拒绝'),
        ('https://app.example.com', 'PATCH',  ['X-Auth-Token'],         'PATCH 不在 allowed methods'),
        ('https://app.example.com', 'DELETE', ['X-Evil-Header'],        '自定义头不在 allowed'),
        ('https://app.example.com', 'GET',    [],                        '简单 GET:不预检但服务端仍可返头'),
    ]
    for origin, method, headers, ctx in cases:
        result = build_preflight_response(origin, method, headers,
                                           allowed_origins, allowed_methods, allowed_headers)
        print(f'  [{result["status"]}]  {ctx}')
        if 'headers' in result:
            for k, v in result['headers'].items():
                print(f'        {k}: {v}')
        else:
            print(f'        body: {result["body"]}')
    print()
    print('  关键:ACAH = Access-Control-Allow-Headers(允许请求头)')
    print('  ACM  = Access-Control-Allow-Methods(允许方法)')
    print('  Vary: Origin 必须加,否则 CDN 缓存串味(见 Demo 5)')
    print()


# ───────────── Demo 5: Vary: Origin 必要性 ─────────────

def demo_vary_origin():
    print('─' * 65)
    print('[Demo 5] Vary: Origin 必要性(防 CDN 缓存串味)')
    print('─' * 65)

    print('  场景:CDN 缓存 GET /api/data 响应')
    print()
    print('  ❌ 不写 Vary:')
    print('    第一次 app-a.com 请求:服务端返回 ACAO: https://app-a.com')
    print('    第二次 app-b.com 请求:CDN 命中缓存,返回 ACAO: https://app-a.com')
    print('    → app-b.com 浏览器拒绝(ACAO 不匹配 Origin)')
    print('    → 缓存串味,CORS 间歇失败,debug 困难')
    print()
    print('  ✅ 正确:写 Vary: Origin')
    print('    第一次 app-a.com 请求:服务端返回 ACAO: https://app-a.com + Vary: Origin')
    print('    CDN 按 (URL, Origin) 二元组缓存')
    print('    第二次 app-b.com 请求:cache miss → 服务端重新生成 ACAO')
    print('    → 各自拿到正确 ACAO,CORS 通过')
    print()
    print('  ⚠️  Vary: Origin 增加缓存键基数,大流量场景考虑:')
    print('    - 显式列出允许 origin 子集(返回时 Vary: Origin, 但源可枚举)')
    print('    - 用动态源时仅返回允许源白名单(不要反射 Origin — 高危漏洞)')
    print()


# ───────────── main ─────────────

def main():
    print('=' * 65)
    print('Same-Origin Policy 与 CORS — 浏览器安全基石')
    print('=' * 65)
    print()
    demo_same_origin()
    demo_classify()
    demo_cors_response_check()
    demo_preflight()
    demo_vary_origin()


if __name__ == '__main__':
    main()