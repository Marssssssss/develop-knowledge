"""CSRF Token 防御体系演示(OWASP 推荐五层)
- Synchronizer Token Pattern (stateful)
- Signed Double-Submit Cookie (stateless)
- SameSite Lax/Strict/None 判定
- Fetch Metadata 跨站拒绝
- Origin/Referer 精确校验
纯 stdlib,可执行 5 demo。
"""
import secrets
import hmac
import hashlib
from urllib.parse import urlparse

# ─────────────── 模拟 session 存储(实际是 Redis / DB) ───────────────

SESSIONS: dict[str, dict] = {}
SAFE_METHODS = {'GET', 'HEAD', 'OPTIONS'}


# ─────────────── 1. CSPRNG token 生成 ───────────────

def gen_csrf_token() -> str:
    """OWASP 要求:128+ bits 熵,CSPRNG,URL-safe 字符串。"""
    return secrets.token_urlsafe(32)


def demo_token_gener():
    print('─' * 65)
    print('[Demo 1] CSPRNG token 生成 — Python secrets 模块')
    print('─' * 65)
    token = gen_csrf_token()
    print(f'  生成 token: {token}')
    print(f'  长度: {len(token)} 字符')
    print(f'  base64url(32 bytes raw) = 43 chars + 0 padding')
    print(f'  熵: 256 bits (OWASP 要求 ≥ 128 bits)')
    print(f'  → 反例:`random.token_urlsafe(32)` 使用 Mersenne Twister,可预测')
    print()


# ─────────────── 2. Synchronizer Token Pattern ───────────────

def issue_form_with_token(session_id: str) -> str:
    if 'csrf' not in SESSIONS[session_id]:
        SESSIONS[session_id]['csrf'] = gen_csrf_token()
    token = SESSIONS[session_id]['csrf']
    return f'''
<form action="/transfer" method="POST">
  <input type="hidden" name="csrf_token" value="{token}">
  <input name="to" placeholder="收款人">
  <input name="amount" placeholder="金额">
  <button>提交</button>
</form>'''


def validate_sync_token(session_id: str, form_data: dict) -> tuple[bool, str]:
    expected = SESSIONS.get(session_id, {}).get('csrf')
    submitted = form_data.get('csrf_token', '')
    if not expected:
        return False, 'no token in session'
    if not submitted:
        return False, 'no token in form (likely CSRF)'
    if not hmac.compare_digest(expected, submitted):
        return False, 'token mismatch (CSRF or session expired)'
    return True, 'token matches'


def demo_sync_token():
    print('─' * 65)
    print('[Demo 2] Synchronizer Token Pattern(Stateful)')
    print('─' * 65)
    SESSIONS.clear()
    SESSIONS['sess_alice'] = {'user': 'alice'}

    # 1) 合法表单渲染
    print('  [Step 1] 合法 GET /transfer → 服务端生成 token,渲染表单')
    print(issue_form_with_token('sess_alice')[:200], '...')
    token = SESSIONS['sess_alice']['csrf']
    print(f'  服务端 session 中保存 csrf = {token[:20]}...')
    print()

    # 2) 合法提交
    print('  [Step 2] Alice 提交表单(token 正确)')
    ok, reason = validate_sync_token('sess_alice', {'to': 'bob', 'amount': '100', 'csrf_token': token})
    print(f'  {"✅ PASS" if ok else "🛑 FAIL"}  {reason}')
    print()

    # 3) CSRF 攻击:攻击者构造表单但无 token
    print('  [Step 3] CSRF 攻击(无 token)')
    ok, reason = validate_sync_token('sess_alice', {'to': 'evil', 'amount': '999'})
    print(f'  {"✅ PASS" if ok else "🛑 BLOCK"}  {reason}')
    print()

    # 4) Token 篡改
    print('  [Step 4] Token 篡改(攻击者猜 token)')
    ok, reason = validate_sync_token('sess_alice', {'to': 'evil', 'amount': '999', 'csrf_token': 'fakeToken123'})
    print(f'  {"✅ PASS" if ok else "🛑 BLOCK"}  {reason}')
    print()


# ─────────────── 3. Signed Double-Submit Cookie ───────────────

SERVER_SECRET = b'32-byte-long-server-secret-XXXXXXXX'  # 实际应从 KMS 取


def issue_signed_csrf(session_id: str) -> tuple[str, str]:
    """返回 (Set-Cookie 值, header 值) — 两者一致 + HMAC 签名。
    实际服务端可只下发 Set-Cookie,前端 JS 读 cookie 后放 header。"""
    rand = secrets.token_hex(16)
    msg = f'{len(session_id)}!{session_id}!{len(rand)}!{rand}'.encode()
    sig = hmac.new(SERVER_SECRET, msg, hashlib.sha256).hexdigest()
    token = f'{sig}.{rand}'
    return token, token   # cookie 与 header 内容相同


def validate_signed_csrf(session_id: str, cookie_tok: str, header_tok: str) -> tuple[bool, str]:
    # 1. cookie 与 header 一致(Naive Double-Submit 必备)
    if not hmac.compare_digest(cookie_tok, header_tok):
        return False, 'cookie ≠ header (Naive mismatch)'
    # 2. 校验 HMAC 签名
    try:
        sig, rand = cookie_tok.split('.', 1)
    except ValueError:
        return False, 'malformed token'
    msg = f'{len(session_id)}!{session_id}!{len(rand)}!{rand}'.encode()
    expected_sig = hmac.new(SERVER_SECRET, msg, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected_sig):
        return False, 'HMAC signature invalid (token not issued by us)'
    return True, 'token valid + signature valid'


def demo_signed_double_submit():
    print('─' * 65)
    print('[Demo 3] Signed Double-Submit Cookie(Stateless)')
    print('─' * 65)
    cookie_tok, header_tok = issue_signed_csrf('sess_alice')
    print(f'  Set-Cookie: csrf={cookie_tok[:50]}...')
    print(f'  Header X-CSRF-Token: {header_tok[:50]}...')
    print(f'  → 两者内容相同,客户端 JS 把 cookie 值复制到 header')
    print()

    # 合法
    ok, reason = validate_signed_csrf('sess_alice', cookie_tok, header_tok)
    print(f'  [合法] {"✅ PASS" if ok else "🛑 FAIL"}  {reason}')

    # 攻击者偷 cookie 但尝试不同 header
    ok, reason = validate_signed_csrf('sess_alice', cookie_tok, 'malicious.header')
    print(f'  [cookie = stolen, header 篡改] {"✅ PASS" if ok else "🛑 BLOCK"}  {reason}')

    # 攻击者没 cookie 伪造 header
    ok, reason = validate_signed_csrf('sess_alice', '', 'fake.fake')
    print(f'  [无 cookie 伪造 header] {"✅ PASS" if ok else "🛑 BLOCK"}  {reason}')

    # Naive 漏洞演示:同源子域写覆盖 cookie
    print()
    print('  ⚠️  Naive Double-Submit 风险:')
    print('  若 Domain=.example.com 错误配置,evil.example.com 可写同名 cookie')
    print('  → 必须用 Signed 版本(token 由 secret 签名,无法伪造)')
    print()


# ─────────────── 4. SameSite 判定 ───────────────

def samesite_decision(samesite_attr: str, request_method: str, is_top_level: bool) -> tuple[bool, str]:
    """给定 SameSite 值 + 请求上下文,判断浏览器是否发送 cookie。"""
    if samesite_attr == 'Strict':
        return False, 'Strict: 不发任何跨站 cookie(连顶级导航都不发)'
    if samesite_attr == 'Lax':
        if request_method in SAFE_METHODS and is_top_level:
            return True, 'Lax: 顶级 GET/HEAD/OPTIONS 跨站导航 → 发'
        if request_method in SAFE_METHODS:
            return False, 'Lax: 跨站 GET 但非顶级导航(iframe/img)→ 不发'
        return False, 'Lax: 跨站 POST/PUT/DELETE → 不发'
    if samesite_attr == 'None':
        return True, 'None: 任何跨站都发(必须 Secure)'
    return True, '未设置 → 浏览器默认值(Chrome=Lax, Firefox=Lax, Safari=None)'


def demo_samesite():
    print('─' * 65)
    print('[Demo 4] SameSite 跨站 cookie 发送判定')
    print('─' * 65)
    cases = [
        ('Strict', 'GET', True,  '外链点击进入(顶级导航)'),
        ('Strict', 'POST', True, '外链表单提交'),
        ('Lax',    'GET',  True, '外链点击进入'),
        ('Lax',    'GET',  False, 'evil iframe/img 加载'),
        ('Lax',    'POST', True, '表单 POST 跨站'),
        ('Lax',    'POST', False, 'fetch() 跨站 POST'),
        ('None',   'POST', False, 'fetch() 跨站 POST(Secure=True)'),
    ]
    for attr, method, top, ctx in cases:
        send, reason = samesite_decision(attr, method, top)
        marker = '📤 发' if send else '🚫 不发'
        print(f'  {marker}  SameSite={attr:<7} {method:<5} top={top!s:<5} ({ctx})')
        print(f'        {reason}')
    print()
    print('  ⚠️  关键陷阱:Lax 不阻止 GET 状态变更!')
    print('  若 /admin/delete?id=123 走 GET,Lax 失效 → 必须配合 token 或改 POST')
    print()


# ─────────────── 5. Fetch Metadata 决策 ───────────────

def fetch_metadata_decision(method: str, sec_fetch_site: str, sec_fetch_mode: str,
                             sec_fetch_dest: str) -> tuple[bool, str]:
    """OWASP 推荐策略:跨站 + 不安全方法 → 拒绝。"""
    # 例外:顶级导航 + GET 总是允许(用户主动操作)
    if sec_fetch_mode == 'navigate' and method == 'GET' \
            and sec_fetch_dest not in ('object', 'embed'):
        return True, 'top-level GET navigation (允许)'

    if sec_fetch_site == 'same-origin':
        return True, 'same-origin → 直接允许'
    if sec_fetch_site == 'cross-site' and method not in SAFE_METHODS:
        return False, 'cross-site + 状态变更方法 → 拒绝(主策略)'
    if sec_fetch_site == 'same-site':
        # 默认不信任兄弟子域,生产可按子域白名单放宽
        return False, 'same-site + 状态变更方法 → 默认拒绝(需显式白名单子域)'
    if sec_fetch_site == 'none':
        # none:用户主动书签/键入 URL
        if method in SAFE_METHODS:
            return True, 'Sec-Fetch-Site: none + 安全方法 → 允许'
        return False, 'Sec-Fetch-Site: none + 状态变更方法 → 拒绝'
    return False, 'unknown Sec-Fetch-Site → fail-closed'


def demo_fetch_metadata():
    print('─' * 65)
    print('[Demo 5] Fetch Metadata Headers 决策')
    print('─' * 65)
    cases = [
        ('POST',   'cross-site', 'cors',       'empty',     'CSRF 攻击:fetch POST'),
        ('POST',   'same-origin','cors',       'empty',     '合法:同源 fetch'),
        ('POST',   'same-site',  'cors',       'empty',     '兄弟子域 fetch(默认拒绝)'),
        ('GET',    'cross-site', 'navigate',   'document',  '外链点击 GET'),
        ('GET',    'cross-site', 'no-cors',    'image',     'evil 站点 <img>'),
        ('POST',   'none',       'navigate',   'document',  '书签/手动 URL POST'),
        ('DELETE', 'cross-site', 'cors',       'empty',     'CSRF DELETE'),
    ]
    for method, site, mode, dest, ctx in cases:
        ok, reason = fetch_metadata_decision(method, site, mode, dest)
        marker = '✅ ALLOW' if ok else '🛑 BLOCK'
        print(f'  {marker}  {method:<6} site={site:<11} mode={mode:<9} dest={dest:<8} ({ctx})')
        print(f'        {reason}')
    print()
    print('  ⚠️  强制要求:必须配置 Origin/Referer 兜底(旧浏览器不发 Sec-Fetch-*)')
    print()


# ─────────────── 6. Origin 校验 ───────────────

def verify_origin(origin_header: str | None, referer_header: str | None,
                  allowed_origins: list[str]) -> tuple[bool, str]:
    """精确字符串匹配,防止 example.org.attacker.com 前缀绕过。"""
    if origin_header:
        if origin_header in allowed_origins:
            return True, 'Origin 在白名单'
        return False, f'Origin 不匹配白名单(origin={origin_header!r})'
    if referer_header:
        try:
            ref = urlparse(referer_header)
            ref_origin = f'{ref.scheme}://{ref.netloc}/'   # 注意尾 /
            if ref_origin in allowed_origins:
                return True, 'Referer origin 在白名单'
            return False, f'Referer 不匹配(ref={ref_origin!r})'
        except Exception as e:
            return False, f'Referer 解析失败:{e}'
    return False, 'Origin 与 Referer 都缺失 → fail-closed'


def demo_origin_verify():
    print('─' * 65)
    print('[Demo 6] Origin/Referer 兜底校验')
    print('─' * 65)
    allowed = ['https://app.example.com/']
    cases = [
        ('https://app.example.com', None,                          '合法 POST'),
        ('https://evil.com',        None,                          'CSRF 攻击'),
        (None,                      'https://app.example.com/page', 'Referer 兜底合法'),
        (None,                      'https://app.example.com.attacker.com/', '前缀绕过攻击'),
        ('https://app.example.com.attacker.com', None,              'Origin 前缀绕过'),
        (None,                      None,                          '两个头都缺失(隐私浏览器)'),
    ]
    for origin, referer, ctx in cases:
        ok, reason = verify_origin(origin, referer, allowed)
        marker = '✅ ALLOW' if ok else '🛑 BLOCK'
        print(f'  {marker}  {ctx}')
        print(f'        {reason}')
    print()
    print('  关键:必须用 == 精确匹配,而不是 startswith/endswith')
    print('  否则 example.org.attacker.com 可通过 .example.com 前缀匹配')
    print()


# ─────────────── main ───────────────

def main():
    print('=' * 65)
    print('CSRF Token 防御体系 — OWASP 推荐五层纵深')
    print('=' * 65)
    print()
    demo_token_gener()
    demo_sync_token()
    demo_signed_double_submit()
    demo_samesite()
    demo_fetch_metadata()
    demo_origin_verify()

if __name__ == '__main__':
    main()