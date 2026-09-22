"""in-toto 布局与链路（layout / link）验证。

规范 in-toto/specification：
- 4.3 layout 格式（steps / inspections / keys / expires）
- 4.3.3 工件规则（规则引擎见 rules.py）
- 4.4 link 格式；文件名 `[name].[KEYID-PREFIX].link`，KEYID-PREFIX 是 keyid 的**前六字节**
- 4.5 sublayout：递归验证后向上呈现一份"虚拟 link"，
  materials 取子布局**第一个** step 的 materials，products 取**最后一个** step 的 products

模型口径：签名不做密码学验算，只比对 keyid 是否在 step 声明的 pubkeys 里。
"""

from rules import verify_expected


class Link:
    """一份 link 元数据。sublayout 非空时它是个子布局（4.5）。"""

    def __init__(self, name, materials=None, products=None, command="", signer="",
                 byproducts=None, sublayout=None, sublayout_links=None):
        self.name = name
        self.materials = dict(materials or {})
        self.products = dict(products or {})
        self.command = command
        self.signer = signer
        self.byproducts = byproducts or {"return-value": 0}
        self.sublayout = sublayout
        self.sublayout_links = dict(sublayout_links or {})

    def is_sublayout(self):
        return self.sublayout is not None


class Step:
    def __init__(self, name, threshold=1, expected_materials=None, expected_products=None,
                 pubkeys=None, expected_command=""):
        self.name = name
        self.threshold = threshold
        self.expected_materials = list(expected_materials or [])
        self.expected_products = list(expected_products or [])
        self.pubkeys = list(pubkeys or [])
        self.expected_command = expected_command


class Inspection:
    def __init__(self, name, expected_materials=None, expected_products=None, run=""):
        self.name = name
        self.expected_materials = list(expected_materials or [])
        self.expected_products = list(expected_products or [])
        self.run = run


class Layout:
    def __init__(self, steps, inspections=None, expires=None):
        self.steps = list(steps)
        self.inspections = list(inspections or [])
        self.expires = expires


class Result:
    def __init__(self):
        self.errors = []
        self.warnings = []

    def ok(self):
        return not self.errors


def link_filename(name, keyid_hex):
    """规范 4.4：文件名是 [name].[KEYID-PREFIX].link，KEYID-PREFIX 取 keyid 前六字节。

    keyid 在实现里是十六进制串，六个字节 = 12 个十六进制字符。
    """
    return "%s.%s.link" % (name, keyid_hex[:12])


def _authorized(step, links):
    """签名者必须在 step 声明的 pubkeys 里。"""
    bad = []
    good = []
    for link in links:
        if link.signer in step.pubkeys:
            good.append(link)
        else:
            bad.append(link.signer)
    return good, bad


def _agree(links):
    """阈值 >1 时要求各执行者"报告相同结果"（规范 4.3.1）。"""
    if not links:
        return True
    first = (links[0].materials, links[0].products)
    return all((l.materials, l.products) == first for l in links[1:])


def verify_step(step, links_by_step, result, prefix=""):
    """验证一个 step，返回验证时使用的那份（可能是虚拟的）link。"""
    links = links_by_step.get(step.name, [])
    authorized, bad = _authorized(step, links)
    for signer in bad:
        result.errors.append("%s: %s 的签名者 %s 不在 pubkeys 里" % (prefix, step.name, signer))
    if len(authorized) < step.threshold:
        result.errors.append("%s: %s 的 link 数 %d < 阈值 %d"
                             % (prefix, step.name, len(authorized), step.threshold))
        return None
    if step.threshold > 1 and not _agree(authorized):
        result.errors.append("%s: %s 的 %d 份 link 内容不一致（阈值要求结果相同）"
                             % (prefix, step.name, len(authorized)))
        return None
    link = authorized[0]
    # 子布局：递归验证，向上呈现虚拟 link（4.5.1）
    if link.is_sublayout():
        virtual = verify_layout(link.sublayout, link.sublayout_links, result,
                                prefix=prefix + step.name + "/")
        if virtual is None:
            return None
        link = virtual
    # MATCH ... FROM <step> 要看到的是"已解析后的"link（子布局用虚拟 link 顶上）
    merged = dict(links_by_step)
    merged[step.name] = [link]
    for kind, rules in (("materials", step.expected_materials),
                        ("products", step.expected_products)):
        passed, err, _ = verify_expected(rules, getattr(link, kind), kind, link,
                                         _flatten(merged))
        if not passed:
            result.errors.append("%s: %s 的 expected_%s 未通过 —— %s"
                                 % (prefix, step.name, kind, err))
    # 规范 4.3.1：命令不匹配只告警，不判失败（PATH、--color 之类会造成合理差异）
    if step.expected_command and link.command and step.expected_command != link.command:
        result.warnings.append("%s: %s 的实际命令 %r 与期望 %r 不一致（按规范只告警）"
                               % (prefix, step.name, link.command, step.expected_command))
    return link


def _flatten(links_by_step):
    """MATCH ... FROM <step> 要能取到任意 step 的 link（子布局里用虚拟 link 顶上）。"""
    out = {}
    for name, links in links_by_step.items():
        if links:
            out[name] = links[0]
    return out


def verify_layout(layout, links_by_step, result=None, prefix=""):
    """验证整份布局。成功时返回一份"虚拟 link"（供上层 MATCH 用），失败返回 None。"""
    if result is None:
        result = Result()
    resolved = {}
    for step in layout.steps:
        link = verify_step(step, links_by_step, result, prefix=prefix)
        if link is not None:
            resolved[step.name] = link
    if not resolved:
        return None
    names = [s.name for s in layout.steps if s.name in resolved]
    first = resolved[names[0]]
    last = resolved[names[-1]]
    return Link(names[0], first.materials, last.products,
                command=last.command, signer=last.signer)


def verify(layout, links_by_step):
    """对外入口：返回 Result。"""
    result = Result()
    verify_layout(layout, links_by_step, result)
    for inspection in layout.inspections:
        inline = links_by_step.get(inspection.name, [])
        link = inline[0] if inline else Link(inspection.name)
        for kind, rules in (("materials", inspection.expected_materials),
                            ("products", inspection.expected_products)):
            passed, err, _ = verify_expected(rules, getattr(link, kind), kind, link,
                                             _flatten(links_by_step))
            if not passed:
                result.errors.append("inspection %s 的 expected_%s 未通过 —— %s"
                                     % (inspection.name, kind, err))
    return result
