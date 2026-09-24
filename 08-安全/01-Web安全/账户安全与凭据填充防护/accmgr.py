"""账户安全：口令策略、黑名单、限流与凭据填充防护最小模型。

依据 NIST SP 800-63B《Digital Identity Guidelines — Authentication and
Authenticator Management》（https://pages.nist.gov/800-63-4/sp800-63b.html ，
353757 B 全文实读）第 3.1.1.2 节（Password Verifiers）与 3.2.2 节（Throttling）：

口令长度：
  - 单因素口令 SHALL 至少 **15** 字符
  - 仅用于多因素流程的口令 MAY 更短，但 SHALL 至少 **8** 字符
  - SHOULD 允许最长至少 **64** 字符
  - SHOULD 接受全部可打印 ASCII 与空格；SHOULD 接受 Unicode
  - **每个 Unicode 码点算一个字符**（不是字节、不是 UTF-16 单元）
  - 若接受 Unicode，应在哈希前做 **NFC** 归一化

禁止项（SHALL NOT）：
  - 不得施加其他组成规则（大小写/数字/符号混用之类）
  - 不得要求定期改密（但有失陷证据时 SHALL 强制改）
  - 不得存储可被未认证索取方读取的提示（hint）
  - 不得使用知识型认证（KBA）/ 安全问题

必须项（SHALL）：
  - 请求完整口令（不是其子集），并校验**完整**提交的口令（不得截断）
  - 建立/修改口令时与黑名单比对；**整串**比对，不是子串或其中可能的单词
  - 命中黑名单 → SHALL 要求另选并 SHALL 给出拒绝理由
  - 实现限流，限制账户上连续失败的认证尝试（§3.2.2 上限 100）
  - 加盐哈希存储（口令、盐、成本因子三输入）

其他（实读）：
  - look-up secret SHALL 至少 **6** 位十进制数字
  - 带外（out-of-band）认证 SHALL 在 **10 分钟**内完成，否则无效；
    且同一个 secret 在有效期内只接受一次（重放防护）
  - 激活密钥（activation secret）连续失败重试 SHALL 不超过 **10** 次
"""

import hashlib
import unicodedata

MIN_LEN_SINGLE = 15
MIN_LEN_MFA = 8
RECOMMENDED_MAX = 64
THROTTLE_UPPER_BOUND = 100
LOOKUP_SECRET_DIGITS = 6
OOB_VALIDITY_SECONDS = 600
ACTIVATION_RETRY_LIMIT = 10
ACTIVATION_MIN_LEN = 4


def normalize_password(pw):
    """接受 Unicode 时应在哈希前做 NFC 归一化。"""
    return unicodedata.normalize("NFC", pw)


def password_length(pw):
    """每个 Unicode 码点算一个字符。"""
    return len(normalize_password(pw))


def is_printing_ascii_or_space(pw):
    return all(0x20 <= ord(c) <= 0x7E for c in pw)


class Verifier:
    """口令验证器。默认按 SP 800-63B 的 SHALL 配置。"""

    def __init__(self, mfa=False, min_len=None, max_len=RECOMMENDED_MAX,
                 blocklist=None, context_words=None,
                 throttle_limit=THROTTLE_UPPER_BOUND):
        self.mfa = mfa
        self.min_len = min_len if min_len is not None else (
            MIN_LEN_MFA if mfa else MIN_LEN_SINGLE)
        self.max_len = max_len
        self.blocklist = set(blocklist or ())
        self.context_words = set(context_words or ())
        self.throttle_limit = throttle_limit
        self.failed = {}          # user -> 连续失败次数
        self.stored = {}          # user -> (salt, cost, digest)
        self.cost = 1000

    # ---------- 口令校验 ----------
    def validate_password(self, pw, user=""):
        """返回 (ok, reasons)。reasons 为空表示通过。"""
        reasons = []
        n = password_length(pw)
        if n < self.min_len:
            reasons.append("too_short")
        if n > self.max_len:
            reasons.append("exceeds_max")
        norm = normalize_password(pw)
        if norm in self.blocklist:
            reasons.append("blocklisted")
        for w in self.context_words:
            if norm == w:
                reasons.append("context_specific")
                break
        return (not reasons, reasons)

    def store(self, user, pw):
        """SHALL 加盐哈希存储。digest 用 sha256 占位（真实须用内存困难函数）。"""
        salt = hashlib.sha256((user + "|" + pw).encode("utf-8")).hexdigest()[:16]
        digest = hashlib.sha256(
            (salt + "|" + str(self.cost) + "|" + normalize_password(pw))
            .encode("utf-8")).hexdigest()
        self.stored[user] = (salt, self.cost, digest)
        return self.stored[user]

    def verify(self, user, pw):
        """校验**完整**提交的口令（不截断）。"""
        if user not in self.stored:
            return False
        salt, cost, digest = self.stored[user]
        got = hashlib.sha256(
            (salt + "|" + str(cost) + "|" + normalize_password(pw))
            .encode("utf-8")).hexdigest()
        return got == digest

    def authenticate(self, user, pw):
        """带限流的认证。返回 (ok, locked)。"""
        if self.failed.get(user, 0) >= self.throttle_limit:
            return (False, True)
        if self.verify(user, pw):
            self.failed[user] = 0
            return (True, False)
        self.failed[user] = self.failed.get(user, 0) + 1
        return (False, self.failed[user] >= self.throttle_limit)

    def consecutive_failures(self, user):
        return self.failed.get(user, 0)

    def reset_failures(self, user):
        self.failed[user] = 0


def check_lookup_secret(secret):
    """look-up secret SHALL 至少 6 位十进制数字。"""
    if len(secret) < LOOKUP_SECRET_DIGITS:
        return False
    return all(c in "0123456789" for c in secret)


class OutOfBandSecret:
    """带外认证 secret：10 分钟内有效，且有效期内只接受一次。"""

    def __init__(self, secret, issued_at):
        self.secret = secret
        self.issued_at = issued_at
        self.used = False

    def accept(self, candidate, now):
        if self.used:
            return False
        if now - self.issued_at > OOB_VALIDITY_SECONDS:
            return False
        if candidate != self.secret:
            return False
        self.used = True
        return True


class ActivationSecret:
    """激活密钥：连续失败重试 SHALL 不超过 10 次。"""

    def __init__(self, secret):
        self.secret = secret
        self.retries = 0

    def try_secret(self, candidate):
        if self.retries >= ACTIVATION_RETRY_LIMIT:
            return "locked"
        if candidate == self.secret:
            self.retries = 0
            return "ok"
        self.retries += 1
        return "locked" if self.retries >= ACTIVATION_RETRY_LIMIT else "reject"

    def valid_length(self):
        return len(self.secret) >= ACTIVATION_MIN_LEN
