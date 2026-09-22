"""APNs provider token：ES256 签名的 JWT（RFC 7519 / RFC 7515 / RFC 7518 §3.4）。

Apple 官方《Establishing a token-based connection to APNs》给出的令牌只有四个键值对：
    header : {"alg": "ES256", "kid": <密钥 ID>}
    claims : {"iss": <Team ID>,  "iat": <生成时刻>}
签名输入是 ASCII 的 "<header_b64>.<claims_b64>"，签名值是 r||s 各 32 字节定长。
"""
import base64
import json

import p256

ALG = 'ES256'


def b64u_encode(raw):
    """RFC 7515 §2：base64url 且**不带** '=' 填充。"""
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')


def b64u_decode(text):
    pad = '=' * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _seg(obj):
    """序列化一个 JWT 段。用紧凑分隔且不排序，便于与官方样例逐字节比对。"""
    return json.dumps(obj, separators=(',', ':')).encode('utf-8')


def signing_input(header, claims):
    """JWS 签名输入 = b64u(header) + '.' + b64u(claims)。"""
    h = b64u_encode(header if isinstance(header, bytes) else _seg(header))
    c = b64u_encode(claims if isinstance(claims, bytes) else _seg(claims))
    return (h + '.' + c).encode('ascii')


def make_token(d, kid, iss, iat):
    """用 ES256 私钥标量 d 生成 APNs provider token 的紧凑序列化形式。"""
    header = {'alg': ALG, 'kid': kid}
    claims = {'iss': iss, 'iat': iat}
    si = signing_input(header, claims)
    sig = p256.sig_to_bytes(p256.sign(d, si))
    return si.decode('ascii') + '.' + b64u_encode(sig)


def split_token(token):
    parts = token.split('.')
    if len(parts) != 3:
        raise ValueError('JWT must have exactly 3 parts')
    return parts


def token_header(token):
    return json.loads(b64u_decode(split_token(token)[0]))


def token_claims(token):
    return json.loads(b64u_decode(split_token(token)[1]))


def token_kid(token):
    return token_header(token).get('kid')


def token_iss(token):
    return token_claims(token).get('iss')


def token_iat(token):
    """iat 可能是数值也可能是字符串（官方示例就是毫秒字符串），统一转成 int 秒。"""
    raw = token_claims(token).get('iat')
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = int(raw)
    return int(raw)


def verify_token(pub, token):
    """验签；返回 bool。只校验 ES256。"""
    try:
        h, c, s = split_token(token)
    except ValueError:
        return False
    header = json.loads(b64u_decode(h))
    if header.get('alg') != ALG:
        return False
    raw = b64u_decode(s)
    if len(raw) != 64:
        return False
    sig = (int.from_bytes(raw[:32], 'big'), int.from_bytes(raw[32:], 'big'))
    return p256.verify(pub, (h + '.' + c).encode('ascii'), sig)


def authorization_header(token):
    """《Sending notification requests to APNs》要求的值是 `bearer <token>`（小写 bearer）。"""
    return 'bearer ' + token
