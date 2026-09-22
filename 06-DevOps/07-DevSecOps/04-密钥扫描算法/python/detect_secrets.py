#!/usr/bin/env python3
"""Yelp/detect-secrets 的两个核心插件的转写。

对应文件（master 分支实读）：

* ``detect_secrets/plugins/high_entropy_strings.py``
  —— ``HighEntropyStringsPlugin`` / ``Base64HighEntropyString`` / ``HexHighEntropyString``
* ``detect_secrets/plugins/keyword.py``
  —— ``KeywordDetector`` 与各 filetype 的 denylist 正则
"""

from __future__ import annotations

import math
import re
import string
from typing import Any, Dict, Generator, List, Optional, Pattern, Set

# --------------------------------------------------------------------------
# 一、高熵字符串插件
# --------------------------------------------------------------------------


class HighEntropyStringsPlugin:
    """``HighEntropyStringsPlugin``：字符集 + 熵阈值。"""

    secret_type = "High Entropy String"

    def __init__(self, charset: str, limit: float) -> None:
        if limit < 0 or limit > 8:
            raise ValueError("The limit set for HighEntropyStrings must be between 0.0 and 8.0")
        self.charset = charset
        self.entropy_limit = limit
        # 必须带引号以降低噪声；第 1 组是捕获组以便反向引用 \1
        self.regex = re.compile(r'([\'"])([{}]+)(\1)'.format(re.escape(charset)))

    def analyze_string(self, string_: str) -> Generator[str, None, None]:
        for result in self.regex.findall(string_):
            if isinstance(result, tuple):
                result = result[1]
            yield result

    def calculate_shannon_entropy(self, data: str) -> float:
        """**只遍历 self.charset**，不在 charset 里的字符完全不计入。

        这是本插件与「教科书香农熵」最大的差别：分母是 len(data)，分子只统计
        charset 内的字符，因此含大量 charset 外字符的串会得到**偏低**的熵。
        """
        if not data:
            return 0
        entropy = 0.0
        for x in self.charset:
            p_x = float(data.count(x)) / len(data)
            if p_x > 0:
                entropy += -p_x * math.log(p_x, 2)
        return entropy

    def analyze_line(self, line: str) -> Set[str]:
        """返回通过阈值的候选；过滤条件是**严格大于**。"""
        return {
            s for s in self.analyze_string(line)
            if self.calculate_shannon_entropy(s) > self.entropy_limit
        }

    def format_scan_result(self, value: str) -> str:
        entropy = round(self.calculate_shannon_entropy(value), 3)
        if entropy < self.entropy_limit:
            return "False (%s)" % entropy
        return "True  (%s)" % entropy

    def non_quoted_string_regex(self, is_exact_match: bool = True):
        import contextlib

        @contextlib.contextmanager
        def _cm():
            old = self.regex
            alt = r"([{}]+)".format(re.escape(self.charset))
            if is_exact_match:
                alt = r"^" + alt + r"$"
            self.regex = re.compile(alt)
            try:
                yield
            finally:
                self.regex = old

        return _cm()


class Base64HighEntropyString(HighEntropyStringsPlugin):
    secret_type = "Base64 High Entropy String"

    def __init__(self, limit: float = 4.5) -> None:
        super().__init__(
            charset=(
                string.ascii_letters
                + string.digits
                + "+/"          # 标准 base64
                + "\\-_"        # URL-safe base64（源码里写的是 '\\-_'，含反斜杠）
                + "="           # padding
            ),
            limit=limit,
        )


class HexHighEntropyString(HighEntropyStringsPlugin):
    secret_type = "Hex High Entropy String"

    def __init__(self, limit: float = 3.0) -> None:
        super().__init__(charset=string.hexdigits, limit=limit)

    def calculate_shannon_entropy(self, data: str) -> float:
        """纯数字串要减去一个惩罚项。

        源码注释：全数字输入的误报远多于真阳性；最大熵约 3.32（"0123456789"），
        希望把它压到 3.0 以下；同时串越长越可能是真阳性，所以惩罚随长度衰减。
        """
        entropy = super().calculate_shannon_entropy(data)
        if len(data) == 1:
            return entropy
        try:
            int(data)
        except ValueError:
            return entropy
        # 这个乘数是试出来的，目标是「简单又能达成上述意图」
        return entropy - 1.2 / math.log(len(data), 2)


# --------------------------------------------------------------------------
# 二、关键词插件
# --------------------------------------------------------------------------

# 注意：所有值都是小写（源码注释明说）
DENYLIST = (
    "api_?key", "auth_?key", "service_?key", "account_?key", "db_?key",
    "database_?key", "priv_?key", "private_?key", "client_?key",
    "db_?pass", "database_?pass", "key_?pass",
    "password", "passwd", "pwd", "secret",
    "contraseña", "contrasena",
)

CLOSING = r"[]\'\"]{0,2}"          # 源码: r'[]\'"]{0,2}' —— 包含 ] ' " 作为闭合
AFFIX_REGEX = r"\w*"
DENYLIST_REGEX = r"|".join(DENYLIST)
# 支持后缀：password_secure = "value"
DENYLIST_REGEX = r"({denylist}){suffix}".format(denylist=DENYLIST_REGEX, suffix=AFFIX_REGEX)
# 支持前缀：反向比较 if ("value" == my_password_secure) {}
DENYLIST_REGEX_WITH_PREFIX = r"{prefix}{denylist}".format(prefix=AFFIX_REGEX, denylist=DENYLIST_REGEX)

OPTIONAL_WHITESPACE = r"\s*"
OPTIONAL_NON_WHITESPACE = r"[^\s]{0,50}?"
QUOTE = r"[\'\"`]"
SECRET = r"(?=[^\v\'\"]*)(?=\w+)[^\v\'\"]*[^\v,\'\"`]"
SQUARE_BRACKETS = r"(\[[0-9]*\])"

FOLLOWED_BY_COLON_REGEX = re.compile(
    r"{denylist}({closing})?:{whitespace}({quote}?)({secret})(\3)".format(
        denylist=DENYLIST_REGEX, closing=CLOSING, quote=QUOTE,
        whitespace=OPTIONAL_WHITESPACE, secret=SECRET),
    flags=re.IGNORECASE)

FOLLOWED_BY_EQUAL_SIGNS_REGEX = re.compile(
    r"{denylist}({closing})?{whitespace}(={{1,3}}|!==?){whitespace}({quote}?)({secret})(\4)".format(
        denylist=DENYLIST_REGEX, closing=CLOSING, quote=QUOTE,
        whitespace=OPTIONAL_WHITESPACE, secret=SECRET),
    flags=re.IGNORECASE)

FOLLOWED_BY_EQUAL_SIGNS_QUOTES_REQUIRED_REGEX = re.compile(
    r"{denylist}({closing})?{whitespace}(={{1,3}}|!==?){whitespace}({quote})({secret})(\4)".format(
        denylist=DENYLIST_REGEX, closing=CLOSING, quote=QUOTE,
        whitespace=OPTIONAL_WHITESPACE, secret=SECRET),
    flags=re.IGNORECASE)

PRECEDED_BY_EQUAL_COMPARISON_SIGNS_QUOTES_REQUIRED_REGEX = re.compile(
    r"({quote})({secret})(\1){whitespace}[!=]{{2,3}}{whitespace}{denylist}".format(
        denylist=DENYLIST_REGEX_WITH_PREFIX, quote=QUOTE,
        whitespace=OPTIONAL_WHITESPACE, secret=SECRET),
    flags=re.IGNORECASE)

FOLLOWED_BY_QUOTES_AND_SEMICOLON_REGEX = re.compile(
    r"{denylist}{nonWhitespace}{whitespace}({quote})({secret})(\2);".format(
        denylist=DENYLIST_REGEX, nonWhitespace=OPTIONAL_NON_WHITESPACE,
        quote=QUOTE, whitespace=OPTIONAL_WHITESPACE, secret=SECRET),
    flags=re.IGNORECASE)

FOLLOWED_BY_COLON_QUOTES_REQUIRED_REGEX = re.compile(
    r"{denylist}({closing})?:({whitespace})({quote})({secret})(\4)".format(
        denylist=DENYLIST_REGEX, closing=CLOSING, quote=QUOTE,
        whitespace=OPTIONAL_WHITESPACE, secret=SECRET),
    flags=re.IGNORECASE)

FOLLOWED_BY_ARROW_FUNCTION_SIGN_QUOTES_REQUIRED_REGEX = re.compile(
    r"{denylist}({closing})?{whitespace}=>?{whitespace}({quote})({secret})(\3)".format(
        denylist=DENYLIST_REGEX, closing=CLOSING, quote=QUOTE,
        whitespace=OPTIONAL_WHITESPACE, secret=SECRET),
    flags=re.IGNORECASE)

FOLLOWED_BY_OPTIONAL_ASSIGN_QUOTES_REQUIRED_REGEX = re.compile(
    r"{denylist}(.assign)?\((\")({secret})(\3)".format(denylist=DENYLIST_REGEX, secret=SECRET),
    flags=re.IGNORECASE)

FOLLOWED_BY_EQUAL_SIGNS_OPTIONAL_BRACKETS_QUOTES_REQUIRED_REGEX = re.compile(
    r"{denylist}({square})?{ws}[!=]{{1,2}}{ws}(@)?(\")({secret})(\5)".format(
        denylist=DENYLIST_REGEX, square=SQUARE_BRACKETS,
        ws=OPTIONAL_WHITESPACE, secret=SECRET),
    flags=re.IGNORECASE)

FOLLOWED_BY_COLON_EQUAL_SIGNS_REGEX = re.compile(
    r"{denylist}({closing})?{ws}:={ws}({quote}?)({secret})(\3)".format(
        denylist=DENYLIST_REGEX, closing=CLOSING, quote=QUOTE,
        ws=OPTIONAL_WHITESPACE, secret=SECRET),
    flags=re.IGNORECASE)

QUOTES_REQUIRED_DENYLIST_REGEX_TO_GROUP = {
    FOLLOWED_BY_COLON_QUOTES_REQUIRED_REGEX: 5,
    PRECEDED_BY_EQUAL_COMPARISON_SIGNS_QUOTES_REQUIRED_REGEX: 2,
    FOLLOWED_BY_EQUAL_SIGNS_QUOTES_REQUIRED_REGEX: 5,
    FOLLOWED_BY_QUOTES_AND_SEMICOLON_REGEX: 3,
    FOLLOWED_BY_ARROW_FUNCTION_SIGN_QUOTES_REQUIRED_REGEX: 4,
}
CONFIG_DENYLIST_REGEX_TO_GROUP = {
    FOLLOWED_BY_COLON_REGEX: 4,
    PRECEDED_BY_EQUAL_COMPARISON_SIGNS_QUOTES_REQUIRED_REGEX: 2,
    FOLLOWED_BY_EQUAL_SIGNS_REGEX: 5,
    FOLLOWED_BY_QUOTES_AND_SEMICOLON_REGEX: 3,
}
GOLANG_DENYLIST_REGEX_TO_GROUP = {
    FOLLOWED_BY_COLON_EQUAL_SIGNS_REGEX: 4,
    PRECEDED_BY_EQUAL_COMPARISON_SIGNS_QUOTES_REQUIRED_REGEX: 2,
    FOLLOWED_BY_EQUAL_SIGNS_REGEX: 5,
    FOLLOWED_BY_QUOTES_AND_SEMICOLON_REGEX: 3,
}
COMMON_C_DENYLIST_REGEX_TO_GROUP = {
    FOLLOWED_BY_EQUAL_SIGNS_OPTIONAL_BRACKETS_QUOTES_REQUIRED_REGEX: 6,
}
C_PLUS_PLUS_REGEX_TO_GROUP = {
    FOLLOWED_BY_OPTIONAL_ASSIGN_QUOTES_REQUIRED_REGEX: 4,
    FOLLOWED_BY_EQUAL_SIGNS_QUOTES_REQUIRED_REGEX: 5,
}

REGEX_BY_FILETYPE: Dict[str, Dict[Pattern, int]] = {
    "go": GOLANG_DENYLIST_REGEX_TO_GROUP,
    "objc": COMMON_C_DENYLIST_REGEX_TO_GROUP,
    "csharp": COMMON_C_DENYLIST_REGEX_TO_GROUP,
    "c": COMMON_C_DENYLIST_REGEX_TO_GROUP,
    "cpp": C_PLUS_PLUS_REGEX_TO_GROUP,
    "cls": QUOTES_REQUIRED_DENYLIST_REGEX_TO_GROUP,
    "java": QUOTES_REQUIRED_DENYLIST_REGEX_TO_GROUP,
    "javascript": QUOTES_REQUIRED_DENYLIST_REGEX_TO_GROUP,
    "python": QUOTES_REQUIRED_DENYLIST_REGEX_TO_GROUP,
    "swift": QUOTES_REQUIRED_DENYLIST_REGEX_TO_GROUP,
    "terraform": QUOTES_REQUIRED_DENYLIST_REGEX_TO_GROUP,
    "yaml": CONFIG_DENYLIST_REGEX_TO_GROUP,
    "config": CONFIG_DENYLIST_REGEX_TO_GROUP,
    "ini": CONFIG_DENYLIST_REGEX_TO_GROUP,
    "properties": CONFIG_DENYLIST_REGEX_TO_GROUP,
    "toml": CONFIG_DENYLIST_REGEX_TO_GROUP,
}


class KeywordDetector:
    """``KeywordDetector``：变量名听起来像密钥就报。"""

    secret_type = "Secret Keyword"

    def __init__(self, keyword_exclude: Optional[str] = None) -> None:
        self.keyword_exclude = None
        if keyword_exclude:
            self.keyword_exclude = re.compile(keyword_exclude, re.IGNORECASE)

    def analyze_string(self, string_: str,
                       denylist_regex_to_group: Optional[Dict[Pattern, int]] = None
                       ) -> Generator[str, None, None]:
        if self.keyword_exclude and self.keyword_exclude.search(string_):
            return
        attempts = [denylist_regex_to_group] if denylist_regex_to_group is not None \
            else [QUOTES_REQUIRED_DENYLIST_REGEX_TO_GROUP]
        for group in attempts:
            has_results = False
            for regex, group_number in group.items():
                match = regex.search(string_)
                if match:
                    has_results = True
                    yield match.group(group_number)
            if has_results:
                break

    def analyze_line(self, line: str, filetype: str = "python") -> Set[str]:
        group = REGEX_BY_FILETYPE.get(filetype, QUOTES_REQUIRED_DENYLIST_REGEX_TO_GROUP)
        return set(self.analyze_string(line, group))
