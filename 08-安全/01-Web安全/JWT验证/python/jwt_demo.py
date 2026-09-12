"""JWT (RFC 7519) 验证演示 — 纯标准库实现 HS256
6 demo:
- Demo 1: base64url 编解码(RFC 7515 §2)
- Demo 2: HS256 签发 JWT
- Demo 3: 验签 + 过期校验
- Demo 4: Algorithm Confusion 攻击演示
- Demo 5: RFC 7519 附录 A 示例向量
- Demo 6: 篡改检测
"""
import base64
import hashlib
import hmac
import json
import time

# ───────────── base64url 编解码 ─────────────

def b64url_encode(data: bytes) -> str:
    """RFC 7515 §2:URL-safe base64,无 padding。"""
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')

def b64url_decode(s: str) -> bytes:
    padding = '=' * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + padding)


# ───────────── JWT 签发/验签(HS256) ─────────────

class InvalidSignatureError(Exception): pass
class ExpiredSignatureError(Exception): pass
class InvalidAlgorithmError(Exception): pass


def jwt_encode(header: dict, payload: dict, secret: bytes) -> str:
    """HS256 签发 JWT(RFC 7519 §7.1)。"""
    if header.get('alg') != 'HS256':
        raise InvalidAlgorithmError(f'only HS256 supported, got {header.get("alg")}')
    # ★ JSON 必须无空格(RFC 7519 §7.2 附录 A 用紧凑格式)
    h_b64 = b64url_encode(json.dumps(header,  separators=(',', ':')).encode())
    p_b64 = b64url_encode(json.dumps(payload, separators=(',', ':')).encode())
    signing_input = f'{h_b64}.{p_b64}'.encode()
    sig = hmac.new(secret, signing_input, hashlib.sha256).digest()
    return f'{h_b64}.{p_b64}.{b64url_encode(sig)}'


def jwt_decode(token: str, secret: bytes, *, algorithms: list[str],
               now: float | None = None) -> dict:
    """HS256 验签 + 校验 exp/nbf(RFC 7519 §7.2)。
    ★ algorithms 白名单强制(防御 alg confusion 与 alg:none)。"""
    parts = token.split('.')
    if len(parts) != 3:
        raise ValueError(f'JWT must have 3 parts, got {len(parts)}')
    h_b64, p_b64, sig_b64 = parts
    if not sig_b64:
        raise InvalidSignatureError('empty signature')
    # 1. 解 header 并校验算法(★ 关键)
    header = json.loads(b64url_decode(h_b64))
    if header.get('alg') not in algorithms:
        raise InvalidAlgorithmError(
            f'alg={header.get("alg")!r} not in allowed {algorithms} (RFC 8725)')
    # 2. 重新计算签名
    signing_input = f'{h_b64}.{p_b64}'.encode()
    expected_sig = hmac.new(secret, signing_input, hashlib.sha256).digest()
    actual_sig = b64url_decode(sig_b64)
    if not hmac.compare_digest(expected_sig, actual_sig):
        raise InvalidSignatureError('signature mismatch')
    # 3. 解 payload + 时间校验
    payload = json.loads(b64url_decode(p_b64))
    if now is None:
        now = time.time()
    if 'exp' in payload and now >= payload['exp']:
        raise ExpiredSignatureError(f'token expired at {payload["exp"]} (now={now})')
    if 'nbf' in payload and now < payload['nbf']:
        raise ExpiredSignatureError(f'token not before {payload["nbf"]} (now={now})')
    return payload


# ───────────── Demo 1: base64url ─────────────

def demo_base64url():
    print('─' * 65)
    print('[Demo 1] base64url 编解码(RFC 7515 §2)')
    print('─' * 65)

    raw = b'\xfb\xff\xfe\x00\x01\x80\xab\xcd'
    enc = b64url_encode(raw)
    dec = b64url_decode(enc)
    print(f'  原始字节(hex): {raw.hex()}')
    print(f'  base64url:     {enc}')
    print(f'  base64 标准:   {base64.b64encode(raw).decode()}  ← 含 + / = ')
    print(f'  base64url:     {enc}  ← 含 - _ 无 padding')
    print(f'  解码回:        {dec.hex()}')
    assert dec == raw, 'round-trip 失败'
    print('  ✓ round-trip 一致')
    print()


# ───────────── Demo 2 & 3: 签发 + 验签 ─────────────

SECRET = b'32-byte-long-shared-secret-XYZabc123456'

def demo_sign_verify():
    print('─' * 65)
    print('[Demo 2] HS256 签发 JWT')
    print('─' * 65)
    header = {'typ': 'JWT', 'alg': 'HS256'}
    payload = {
        'iss': 'demo-server',
        'sub': 'alice',
        'aud': 'demo-client',
        'exp': int(time.time()) + 3600,
        'iat': int(time.time()),
    }
    token = jwt_encode(header, payload, SECRET)
    print(f'  header:  {header}')
    print(f'  payload: {payload}')
    print(f'  token:   {token}')
    print(f'  长度:    {len(token)} chars (3 段 base64url)')
    print()
    print('─' * 65)
    print('[Demo 3] 验签 + exp 校验')
    print('─' * 65)
    # 合法
    try:
        claims = jwt_decode(token, SECRET, algorithms=['HS256'])
        print(f'  ✅ 合法 token: sub={claims["sub"]}, aud={claims["aud"]}, '
              f'exp 剩余 {(claims["exp"] - time.time()):.0f}s')
    except Exception as e:
        print(f'  ❌ {e}')

    # 过期
    expired_token = jwt_encode(header, {**payload, 'exp': int(time.time()) - 100},
                                SECRET)
    try:
        jwt_decode(expired_token, SECRET, algorithms=['HS256'])
    except ExpiredSignatureError as e:
        print(f'  🛑 过期 token: {e}')
    print()


# ───────────── Demo 4: Algorithm Confusion 攻击 ─────────────

def demo_alg_confusion():
    print('─' * 65)
    print('[Demo 4] ⚠️ Algorithm Confusion 攻击演示 + 防御')
    print('─' * 65)
    print('  攻击场景:服务端用 RSA 公钥验签,但库按 header.alg 选算法')
    print('  攻击者改 header.alg=HS256,用公钥当 HMAC secret 伪造签名')
    print()

    # 模拟"公钥"(实际是 RSA 公钥的 PEM/DER 字节)
    fake_public_key = b'-----BEGIN PUBLIC KEY-----\nMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA...\n-----END PUBLIC KEY-----'

    # 攻击者构造 token:alg=HS256,payload={"sub":"admin"},secret=公钥字节
    attacker_token = jwt_encode(
        {'typ': 'JWT', 'alg': 'HS256'},
        {'sub': 'admin', 'exp': int(time.time()) + 3600},
        fake_public_key,   # ← 用公钥当 HMAC secret!
    )
    print(f'  攻击 token: {attacker_token[:80]}...')
    print()

    # ❌ 不安全:服务端按 header 选算法,误用 RSA 公钥当 HS256 secret
    print('  [不安全] 服务端按 header.alg 选算法,误用公钥当 secret:')
    try:
        claims = jwt_decode(attacker_token, fake_public_key, algorithms=['HS256'])
        print(f'    ⚠️ 攻击成功!  身份 = {claims["sub"]}')
    except Exception as e:
        print(f'    {type(e).__name__}: {e}')

    # ✅ 安全:服务端用真实共享 secret 验签
    print('  [安全]   服务端用真实共享 secret 验签:')
    try:
        claims = jwt_decode(attacker_token, SECRET, algorithms=['HS256'])
        print(f'    ⚠️ 攻击成功(不应该)')
    except InvalidSignatureError as e:
        print(f'    🛑 攻击被阻断: {e}')

    # ✅ 最佳:alg 必须与密钥类型匹配(HS256 用对称 secret,RS256 用 RSA 公钥)
    print()
    print('  RFC 8725 §3.1 BCP:服务端不应按 header 选算法 — 应当按业务决定算法 + 检查 alg 与密钥类型匹配')
    print('  本 demo algorithms=["HS256"] + secret 与伪造 secret 不一致 → 阻断')
    print()


# ───────────── Demo 5: RFC 7519 附录 A 向量 ─────────────

def demo_rfc7519_appendix_a():
    print('─' * 65)
    print('[Demo 5] RFC 7519 §A.1 HS256 示例向量验证')
    print('─' * 65)
    # RFC 7519 Appendix A.1 给出的 HS256 示例
    # https://datatracker.ietf.org/doc/html/rfc7519#appendix-A.1
    header_b64 = 'eyJ0eXAiOiJKV1QiLA0KICJhbGciOiJIUzI1NiJ9'
    payload_b64 = 'eyJpc3MiOiJqb2UiLA0KICJleHAiOjEzMDA4MTkzODAsDQogImh0dHA6Ly9leGFtcGxlLmNvbS9pc19yb290Ijp0cnVlfQ'
    # 注:RFC 7519 文本换行用 CRLF(\r\n),签发前需还原
    secret = bytes([
        3, 35, 53, 75, 43, 15, 165, 188, 131, 126, 6, 101, 119, 123, 166,
        143, 90, 179, 40, 230, 240, 84, 201, 40, 169, 15, 132, 178, 210, 80, 46,
        191, 211, 251, 90, 146, 210, 6, 71, 239, 150, 138, 180, 195, 119,
    ])
    # 还原 RFC CRLF
    header_json = b64url_decode(header_b64).replace(b'\n', b'\r\n')
    payload_json = b64url_decode(payload_b64).replace(b'\n', b'\r\n')
    h_b64_re = b64url_encode(header_json)
    p_b64_re = b64url_encode(payload_json)
    signing_input = f'{h_b64_re}.{p_b64_re}'.encode()
    sig = hmac.new(secret, signing_input, hashlib.sha256).digest()
    sig_b64 = b64url_encode(sig)
    expected_sig_b64 = 'dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk'

    print(f'  还原 header (含 CRLF): {header_json!r}')
    print(f'  还原 payload(含 CRLF): {payload_json!r}')
    print(f'  计算签名 b64url:       {sig_b64}')
    print(f'  RFC 期望签名 b64url:   {expected_sig_b64}')
    print(f'  匹配: {"✅" if sig_b64 == expected_sig_b64 else "❌"}')
    print()
    print('  → 关键:JSON 必须保留 RFC CRLF,否则签名不一致')
    print('  → 现代库用 json.dumps(separators=(",", ":")) 紧凑格式,RFC 文本示例为可读格式')
    print()


# ───────────── Demo 6: 篡改检测 ─────────────

def demo_tampering():
    print('─' * 65)
    print('[Demo 6] 篡改检测(改 payload 任何字符)')
    print('─' * 65)
    token = jwt_encode(
        {'typ': 'JWT', 'alg': 'HS256'},
        {'sub': 'alice', 'role': 'user', 'exp': int(time.time()) + 3600},
        SECRET,
    )
    print(f'  原始 token: {token}')
    print()

    # 改 payload 一字节
    h, p, s = token.split('.')
    tampered = f'{h}.{p[:-2]}XX.{s}'
    print(f'  篡改 token: {tampered}')
    try:
        jwt_decode(tampered, SECRET, algorithms=['HS256'])
    except InvalidSignatureError as e:
        print(f'  🛑 BLOCK  {e}')

    # 改 signature 一字节
    h, p, s = token.split('.')
    s_tampered = s[:-2] + 'XX'
    tampered2 = f'{h}.{p}.{s_tampered}'
    print(f'  篡改 sig:   {tampered2}')
    try:
        jwt_decode(tampered2, SECRET, algorithms=['HS256'])
    except InvalidSignatureError as e:
        print(f'  🛑 BLOCK  {e}')

    # alg: none 攻击
    h, p, _ = token.split('.')
    none_token = f'{h}.{p}.'   # signature 为空
    print(f'  alg:none:  {none_token}')
    try:
        jwt_decode(none_token, SECRET, algorithms=['HS256'])
    except (InvalidSignatureError, InvalidAlgorithmError, ValueError) as e:
        print(f'  🛑 BLOCK  {type(e).__name__}: {e}')

    # 不同 secret 验签
    fake_secret = b'attacker-guessed-secret-XXXXXXXX'
    try:
        jwt_decode(token, fake_secret, algorithms=['HS256'])
    except InvalidSignatureError as e:
        print(f'  🛑 BLOCK  错 secret: {e}')
    print()


# ───────────── main ─────────────

def main():
    print('=' * 65)
    print('JWT 验证 (RFC 7519) — HS256 完整实现 + 攻击演示')
    print('=' * 65)
    print()
    demo_base64url()
    demo_sign_verify()
    demo_alg_confusion()
    demo_rfc7519_appendix_a()
    demo_tampering()


if __name__ == '__main__':
    main()