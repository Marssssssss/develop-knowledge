"""in-toto 工件规则（Artifact Rules）引擎。

规范 in-toto/specification 4.3.3：规则挂在 step / inspection 的
`expected_materials` 与 `expected_products` 上，**顺序执行**，像防火墙规则一样
—— 被某条规则消费掉的工件会从队列里移除，后面的规则再也看不到它。

规则语法（规范 4.3.3）：
    MATCH <pattern> [IN <src-prefix>] WITH (MATERIALS|PRODUCTS)
          [IN <dst-prefix>] FROM <step>
    CREATE <pattern> | DELETE <pattern> | MODIFY <pattern>
    ALLOW <pattern> | REQUIRE <artifact-name> | DISALLOW <pattern>
"""

import fnmatch

MATCH = "MATCH"
CREATE = "CREATE"
DELETE = "DELETE"
MODIFY = "MODIFY"
ALLOW = "ALLOW"
REQUIRE = "REQUIRE"
DISALLOW = "DISALLOW"

KINDS = (MATCH, CREATE, DELETE, MODIFY, ALLOW, REQUIRE, DISALLOW)


class RuleError(ValueError):
    """规则串写不出来。"""


class Rule:
    """一条已解析的规则。"""

    def __init__(self, kind, pattern=None, src_prefix="", dst_kind=None,
                 dst_prefix="", step=None, name=None):
        self.kind = kind
        self.pattern = pattern
        self.src_prefix = src_prefix
        self.dst_kind = dst_kind
        self.dst_prefix = dst_prefix
        self.step = step
        self.name = name

    def __repr__(self):
        return "<Rule %s %s>" % (self.kind, self.pattern or self.name)


def parse_rule(text):
    """把规则串解析成 Rule。"""
    tokens = text.split()
    if not tokens:
        raise RuleError("空规则")
    head = tokens[0].upper()
    if head not in KINDS:
        raise RuleError("未知规则类型: %r" % tokens[0])
    if head == MATCH:
        # MATCH <pattern> [IN <src>] WITH (MATERIALS|PRODUCTS) [IN <dst>] FROM <step>
        if len(tokens) < 5 or tokens[-2].upper() != "FROM":
            raise RuleError("MATCH 必须以 FROM <step> 结尾: %r" % text)
        step = tokens[-1]
        pattern = tokens[1]
        src_prefix = ""
        i = 2
        if i < len(tokens) and tokens[i].upper() == "IN":
            src_prefix = tokens[i + 1]
            i += 2
        if tokens[i].upper() != "WITH":
            raise RuleError("MATCH 缺少 WITH: %r" % text)
        dst_kind = tokens[i + 1].upper()
        # 规范 4.3.3 的语法块写的是 (MATERIALS|PRODUCTS)，而同节的示例写的是
        # "WITH PRODUCT IN build/lib" —— 单复数两种写法都接受，统一归一成复数。
        aliases = {"MATERIAL": "MATERIALS", "MATERIALS": "MATERIALS",
                   "PRODUCT": "PRODUCTS", "PRODUCTS": "PRODUCTS"}
        if dst_kind not in aliases:
            raise RuleError("WITH 后面只能是 MATERIALS 或 PRODUCTS: %r" % text)
        dst_kind = aliases[dst_kind]
        i += 2
        dst_prefix = ""
        if i < len(tokens) and tokens[i].upper() == "IN":
            dst_prefix = tokens[i + 1]
            i += 2
        if i != len(tokens) - 2:
            raise RuleError("MATCH 参数多余: %r" % text)
        return Rule(MATCH, pattern, src_prefix, dst_kind, dst_prefix, step)
    if head == REQUIRE:
        # REQUIRE 不接受通配符，直接给工件名
        if len(tokens) != 2:
            raise RuleError("REQUIRE 只接受一个工件名: %r" % text)
        return Rule(REQUIRE, name=tokens[1])
    if len(tokens) != 2:
        raise RuleError("%s 只接受一个 pattern: %r" % (head, text))
    return Rule(head, pattern=tokens[1])


def strip_prefix(name, prefix):
    """IN 子句：把前缀从工件名上摘掉，摘不掉就原样返回。"""
    if not prefix:
        return name
    if name.startswith(prefix):
        return name[len(prefix):]
    return name


def match_names(artifacts, prefix, pattern):
    """先用前缀过滤，再用 glob 匹配 pattern。"""
    out = {}
    pre = prefix or ""
    for name, digest in artifacts.items():
        if pre and not name.startswith(pre):
            continue
        if fnmatch.fnmatchcase(strip_prefix(name, pre), pattern):
            out[name] = digest
    return out


def apply_rule(rule, queue, kind, link, links):
    """在当前队列上执行一条规则，返回 (被消费的工件名集合, 错误串或 None)。

    kind 是这条规则所在的那一侧（materials / products）。
    """
    other = "products" if kind == "materials" else "materials"
    side = getattr(link, kind)
    opposite = getattr(link, other)

    if rule.kind in (ALLOW, DISALLOW):
        hit = match_names(queue, "", rule.pattern)
        if rule.kind == DISALLOW:
            if hit:
                return set(), "DISALLOW %s 命中了未被授权的工件 %s" % (
                    rule.pattern, sorted(hit))
            return set(), None
        return set(hit), None

    if rule.kind == REQUIRE:
        if rule.name not in queue:
            return set(), "REQUIRE %s 不在剩余工件里" % rule.name
        # 规范伪代码只做存在性检查，不消费
        return set(), None

    if rule.kind == MATCH:
        dst_link = links.get(rule.step)
        if dst_link is None:
            return set(), "MATCH 引用了不存在的步骤 %s" % rule.step
        dst_side = getattr(dst_link, "materials" if rule.dst_kind == "MATERIALS" else "products")
        dst = {}
        for name, digest in dst_side.items():
            if rule.dst_prefix and not name.startswith(rule.dst_prefix):
                continue
            dst[strip_prefix(name, rule.dst_prefix)] = digest
        consumed = set()
        for name, digest in match_names(queue, rule.src_prefix, rule.pattern).items():
            key = strip_prefix(name, rule.src_prefix)
            if key in dst and dst[key] == digest:
                consumed.add(name)
        return consumed, None

    hit = match_names(queue, "", rule.pattern)
    if rule.kind == CREATE:
        for name in hit:
            if name in opposite:
                return set(), "CREATE %s 命中 %s，但它同时也是 %s" % (rule.pattern, name, other)
        return set(hit), None
    if rule.kind == DELETE:
        for name in hit:
            if name in opposite:
                return set(), "DELETE %s 命中 %s，但它同时也是 %s" % (rule.pattern, name, other)
        return set(hit), None
    if rule.kind == MODIFY:
        for name, digest in hit.items():
            if name not in opposite:
                return set(), "MODIFY %s 命中 %s，但它不在 %s 里" % (rule.pattern, name, other)
            if opposite[name] == digest:
                return set(), "MODIFY %s 命中 %s，但哈希没变" % (rule.pattern, name)
        return set(hit), None
    raise RuleError("未知规则类型 %s" % rule.kind)


def verify_expected(rule_texts, artifacts, kind, link, links):
    """顺序执行一整串规则。返回 (是否通过, 错误串, 剩余工件名集合)。

    规范 4.3.3.1：规则列表末尾有一条**隐式的 ALLOW ***，
    显式写 `DISALLOW *` 才能挡住"溜进来"的工件。
    """
    queue = dict(artifacts)
    for text in rule_texts:
        rule = parse_rule(text)
        consumed, err = apply_rule(rule, queue, kind, link, links)
        if err:
            return False, err, set(queue)
        for name in consumed:
            queue.pop(name, None)
    return True, "", set(queue)
