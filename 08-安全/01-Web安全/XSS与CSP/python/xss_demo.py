"""XSS 与 CSP 演示(OWASP A03 注入 + CSP 纵深防御)
本 demo 模拟服务端 HTML 渲染 + 客户端 DOM 操作,
输出浏览器在每种场景下的"行为"(执行/转义/CSP 阻断),
不依赖真实浏览器 — 纯 stdlib 教学。
"""
import html as html_lib
import secrets

# ─────────────────────────── OWASP HTML Entity 编码 ───────────────────────────

# HTML Body 上下文(规则 #1):转义 & < > " '
def html_body_escape(s: str) -> str:
    return (s.replace('&', '&amp;')
             .replace('<', '&lt;')
             .replace('>', '&gt;')
             .replace('"', '&quot;')
             .replace("'", '&#x27;'))

# HTML Attribute 上下文(规则 #2):必须先"用引号包裹"再转义
# 仅在包裹后才安全的字符集更小,但 OWASP 建议"激进实体编码"
def html_attr_escape(s: str) -> str:
    # 全部非字母数字 → &#xHH;
    return ''.join(c if c.isalnum() else f'&#x{ord(c):02X};' for c in s)

# JavaScript 上下文(规则 #3):\uXXXX Unicode
def js_string_escape(s: str) -> str:
    return ''.join(c if c.isalnum() else f'\\u{ord(c):04X}' for c in s)

# URL 上下文(规则 #5):%HH percent-encoding
def url_param_escape(s: str) -> str:
    return ''.join(c if c.isalnum() or c in '-_.~' else f'%{ord(c):02X}' for c in s)


# ─────────────────────────── CSP 模拟器 ───────────────────────────

class CSPPolicy:
    """最小 CSP 解析:支持 script-src 'self' / 'nonce-XXX' / 'unsafe-inline'
    实际浏览器还要解析 hash、host 白名单等;此处只覆盖演示场景。"""
    def __init__(self, header: str):
        self.directives: dict[str, set[str]] = {}
        for raw in header.split(';'):
            raw = raw.strip()
            if not raw:
                continue
            name, *sources = raw.split()
            self.directives[name] = set(sources)

    def allows_inline_script(self, nonce: str | None) -> tuple[bool, str]:
        sources = self.directives.get('script-src') or self.directives.get('default-src', set())
        if "'unsafe-inline'" in sources:
            return True, "allowed: 'unsafe-inline' enabled"
        if nonce and f"'nonce-{nonce}'" in sources:
            return True, f"allowed: nonce '{nonce}' matches"
        if "'self'" in sources:
            return False, "blocked: inline script requires nonce (no nonce present)"
        return False, "blocked: no script-src / default-src matches"


# ─────────────────────────── Demo 1: Reflected XSS ───────────────────────────

def demo_reflected_unsafe():
    """不安全的反射型 XSS:服务端直接拼接用户输入到 HTML body。"""
    user_input = '<script>alert(document.cookie)</script>'
    response = f'<div>您搜索了:{user_input}</div>'
    print("[Demo 1a] 反射型 XSS(不安全拼接)")
    print(f"  请求:  GET /search?q={user_input!r}")
    print(f"  响应:  {response}")
    print("  浏览器: 脚本直接执行 → 攻击者读到 cookie")
    print()

def demo_reflected_safe():
    """反射型 XSS 防御:HTML Entity 编码后输出。"""
    user_input = '<script>alert(document.cookie)</script>'
    escaped = html_body_escape(user_input)
    response = f'<div>您搜索了:{escaped}</div>'
    print("[Demo 1b] 反射型 XSS(HTML Entity 编码)")
    print(f"  请求:  GET /search?q={user_input!r}")
    print(f"  响应:  {response}")
    print(f"  浏览器: 渲染为字面文本 {user_input!r},脚本不会执行")
    print()


# ─────────────────────────── Demo 2: Stored XSS ───────────────────────────

# 模拟"评论"数据库
COMMENT_DB: list[str] = []

def store_comment(raw: str, encode: bool):
    """评论提交:encode=True 时服务端 HTML Entity 编码后存储。"""
    COMMENT_DB.append(html_body_escape(raw) if encode else raw)

def render_comments():
    """渲染评论页:逐条拼到 <div>。"""
    return '\n'.join(f'<div class="comment">{c}</div>' for c in COMMENT_DB)

def demo_stored():
    raw = '<img src=x onerror="alert(1)">'
    print("[Demo 2] Stored XSS(评论持久化 + 渲染)")
    # 不安全:直接拼接存储
    COMMENT_DB.clear()
    store_comment(raw, encode=False)
    print("  [2a] 不安全:服务端不编码直接存储")
    print(f"  渲染:  {render_comments()}")
    print("  → 任何访问评论页的用户 img onerror 自动执行")
    # 安全:存储前编码
    COMMENT_DB.clear()
    store_comment(raw, encode=True)
    print("  [2b] 安全:服务端存储前 HTML Entity 编码")
    print(f"  渲染:  {render_comments()}")
    print("  → onerror 属性被实体编码,img 标签无法解析为有效属性")
    COMMENT_DB.clear()
    print()


# ─────────────────────────── Demo 3: DOM-based XSS ───────────────────────────

def demo_dom_xss_sink():
    """DOM XSS sink 对比:innerHTML(unsafe) vs textContent(safe)。"""
    user_hash = '#<img src=x onerror=alert(1)>'
    raw = user_hash[1:]  # 去掉 #

    print("[Demo 3] DOM XSS sink 对比")
    print(f"  document.location.hash = {user_hash!r}")
    # 模拟:innerHTML 解析路径
    inner_html_result = f'<h1>查询:{raw}</h1>'   # 浏览器会执行 onerror
    print(f"  [3a] UNSAFE sink:el.innerHTML = '<h1>查询:{raw}</h1>'")
    print(f"        浏览器解析:onerror 执行,弹出 alert")
    # textContent 自动转义
    safe_via_text = html_body_escape(raw)
    print(f"  [3b] SAFE sink:el.textContent = {raw!r}")
    print(f"        浏览器写入 DOM:<h1>查询:{safe_via_text}</h1>")
    print(f"        → onerror 字符串变成文本节点,不解析为属性")
    print()


# ─────────────────────────── Demo 4: CSP 阻断 + nonce 允许 ───────────────────────────

def demo_csp_nonce():
    """CSP 模拟:同一 HTML 内 nonce 脚本通过,无 nonce 被阻断。"""
    nonce = secrets.token_urlsafe(16)
    policy = CSPPolicy(f"default-src 'self'; script-src 'self' 'nonce-{nonce}'")
    body_html = f"""
<script>alert('inline attack')</script>
<script nonce="{nonce}">alert('legit nonce script')</script>
"""
    print("[Demo 4] CSP nonce 验证")
    print(f"  CSP header: default-src 'self'; script-src 'self' 'nonce-{nonce}'")
    print(f"  HTML body:\n{body_html}")
    # 逐 <script> 评估
    for snippet in body_html.strip().split('</script>'):
        if '<script' not in snippet:
            continue
        tag, _, code = snippet.partition('>')
        tag = tag.strip()
        code = code.strip()
        # 提取 nonce
        has_nonce = f'nonce="{nonce}"' in tag
        ok, reason = policy.allows_inline_script(nonce if has_nonce else None)
        label = "✅ ALLOW" if ok else "🛑 BLOCK"
        print(f"  {label}  nonce={'yes' if has_nonce else 'no '}  ← {reason}")
        print(f"        脚本内容: {code}")
    print()


# ─────────────────────────── Demo 5: Anti-pattern 黑名单失败 ───────────────────────────

def demo_waf_bypass():
    """演示为何 OWASP 反对黑名单方案:同一个 <script> 警报有 5+ 种绕过。"""
    target_keyword = '<script>'
    bypasses = [
        '<ScRiPt>alert(1)</ScRiPt>',         # 大小写
        '<scr<script>ipt>alert(1)</script>', # 嵌套(若正则非递归)
        '<\u0073cript>alert(1)</\u0073cript>', # Unicode 转义
        '<img src=x onerror=alert(1)>',      # 事件处理器(非 script 关键字)
        '<svg/onload=alert(1)>',             # SVG 标签 + onload
        '<a href="javascript:alert(1)">x</a>',  # javascript: scheme
    ]
    print("[Demo 5] 黑名单 '<script>' 失败案例")
    print(f"  WAF 规则: filter({target_keyword!r}) → 删除该字符串")
    for payload in bypasses:
        filtered = payload.replace('<script>', '').replace('</script>', '')
        still_works = '<script' in filtered.lower() or 'onerror' in filtered.lower() \
                       or 'onload' in filtered.lower() or 'javascript:' in filtered.lower()
        marker = '⚠️ 绕过' if still_works else '✅ 拦截'
        print(f"  {marker}  输入:{payload}")
        print(f"          过滤后:{filtered}")
    print("  → OWASP 反对黑名单:必须用上下文编码 + CSP 纵深")
    print()


def main():
    print("=" * 70)
    print("XSS 三类型 + CSP 防御(Python 模拟,无真实浏览器)")
    print("=" * 70)
    print()
    demo_reflected_unsafe()
    demo_reflected_safe()
    demo_stored()
    demo_dom_xss_sink()
    demo_csp_nonce()
    demo_waf_bypass()


if __name__ == "__main__":
    main()