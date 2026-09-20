"""Trusted Types(W3C)最小模型 —— 把"注入汇点只接受类型"写成可断言代码。

依据 W3C *Trusted Types* 规范(https://www.w3.org/TR/trusted-types/ 全文实读)：

  * §2.3 类型对象**只能**由 policy 创建,构造函数不暴露;
    create* 回调的调用点就是整个程序里唯一的安全关键代码。
  * §3.1 Create a Trusted Type Policy:先过 §4.2.5 的 CSP 检查("Blocked" 抛
    TypeError);`default` 已存在再创建也抛 TypeError;名字追加进 created policy names。
  * §3.3 Get Trusted Type policy value:类型 → 回调名映射
    TrustedHTML→createHTML / TrustedScript→createScript / TrustedScriptURL→createScriptURL;
    回调缺失时 throwIfMissing 为真则抛 TypeError。
  * §3.4 Get Trusted Type compliant string(核心):
      ① input 已是期望类型 → 直接返回其字符串
      ② 该 sink 组**不要求** TT(且含 report-only)→ 直接返回
      ③ 否则走 default policy(§3.5)
      ④ default policy 产出 null/undefined → 报违规;强制模式抛 TypeError,
         **report-only 模式返回原始值**(规范原话:"default policy rejection
         will be reported, but ignored in a report-only mode")
  * §3.8 Get Trusted Type data for attribute:事件处理器内容属性 → TrustedScript,
    sink = "Element " + 属性名;iframe srcdoc → TrustedHTML;script src /
    SVG script href → TrustedScriptURL。不在表里 → 不强制。
  * §4.2.4 违规 sample = sink + "|" + source 的**前 40 个字符**,
    resource 固定为 "trusted-types-sink"。
  * §4.2.5 policy 创建违规:resource = "trusted-types-policy",
    sample = policyName 前 40 字符;`'none'` 与其它值并存时**被忽略**;
    `'allow-duplicates'` 允许重名;`*` 是通配符。
  * §4.2.1.1 javascript: 导航的 pre-navigation check:剥掉 "javascript:" 后
    过 default policy 的 createScript(sink = "Location href"),拿不到
    TrustedScript 就 "Blocked"。
"""

HTML_NS = "http://www.w3.org/1999/xhtml"
SVG_NS = "http://www.w3.org/2000/svg"
MATHML_NS = "http://www.w3.org/1998/Math/MathML"
XLINK_NS = "http://www.w3.org/1999/xlink"

FUNCTION_NAME = {"TrustedHTML": "createHTML",
                 "TrustedScript": "createScript",
                 "TrustedScriptURL": "createScriptURL"}


class TrustedType:
    """TrustedHTML / TrustedScript / TrustedScriptURL 的统一表示。"""

    def __init__(self, type_name, data):
        self.type_name = type_name
        self.data = data

    def __str__(self):
        return self.data


class TrustedTypeError(TypeError):
    pass


def stringify(value):
    return value.data if isinstance(value, TrustedType) else str(value)


# ------------------------------------------------------------------ policy
class Policy:
    def __init__(self, name, options):
        self.name = name
        self.options = dict(options or {})


class Factory:
    def __init__(self):
        self.created_policy_names = []
        self.default_policy = None


class Violation:
    def __init__(self, directive, disposition, resource, sample):
        self.directive = directive
        self.disposition = disposition      # "enforce" / "report"
        self.resource = resource
        self.sample = sample


class CSPPolicy:
    """一条 CSP(可以是 enforce 也可以是 report-only)。"""

    def __init__(self, disposition="enforce", **directives):
        self.disposition = disposition
        self.directives = {k: v for k, v in directives.items() if v is not None}

    def get(self, name):
        return self.directives.get(name)


class Global:
    def __init__(self, csp_list=None):
        self.csp_list = list(csp_list or [])
        self.factory = Factory()
        self.violations = []


def _report(global_obj, violation):
    global_obj.violations.append(violation)


# ------------------------------------------------------- §4.2.3 是否需要 TT
def does_sink_type_require_trusted_types(global_obj, sink_group,
                                         include_report_only=True):
    for policy in global_obj.csp_list:
        directive = policy.get("require-trusted-types-for")
        if not directive:
            continue
        if sink_group not in directive:
            continue
        if policy.disposition == "enforce":
            return True
        if include_report_only:
            return True
    return False


# ------------------------------------------- §4.2.5 policy 创建是否被 CSP 拦
def should_policy_creation_be_blocked(global_obj, policy_name, created_names):
    result = "Allowed"
    for policy in global_obj.csp_list:
        directive = policy.get("trusted-types")
        if directive is None:
            continue
        tokens = directive.split() if directive.strip() else []
        create_violation = False
        if tokens and all(t.lower() == "'none'" or t == "'none'" for t in tokens):
            create_violation = True
        if policy_name in created_names and "'allow-duplicates'" not in tokens \
                and "\"allow-duplicates\"" not in tokens:
            create_violation = True
        names = [t for t in tokens if not t.startswith("'")]
        if policy_name not in names and "*" not in names:
            create_violation = True
        if not create_violation:
            continue
        _report(global_obj, Violation("trusted-types", policy.disposition,
                                      "trusted-types-policy", policy_name[:40]))
        if policy.disposition == "enforce":
            result = "Blocked"
    return result


# ------------------------------------------------------------ §3.1 创建 policy
def create_policy(global_obj, policy_name, options):
    factory = global_obj.factory
    if should_policy_creation_be_blocked(global_obj, policy_name,
                                         factory.created_policy_names) == "Blocked":
        raise TrustedTypeError("policy creation blocked by CSP: %s" % policy_name)
    if policy_name == "default" and factory.default_policy is not None:
        raise TrustedTypeError("default policy already exists")
    policy = Policy(policy_name, options)
    if policy_name == "default":
        factory.default_policy = policy
    factory.created_policy_names.append(policy_name)
    return policy


# ------------------------------------------------- §3.3 取 policy 回调的返回值
def get_policy_value(policy, type_name, value, extra_args, throw_if_missing):
    func_name = FUNCTION_NAME[type_name]
    func = policy.options.get(func_name)
    if func is None:
        if throw_if_missing:
            raise TrustedTypeError("%s 未实现 %s" % (policy.name, func_name))
        return None
    return func(value, *extra_args)


# ------------------------------------------------------------- §3.2 创建类型
def create_trusted_type(policy, type_name, value, extra_args=()):
    policy_value = get_policy_value(policy, type_name, value, list(extra_args), True)
    data = "" if policy_value is None else stringify(policy_value)
    return TrustedType(type_name, data)


# ------------------------------------------------------- §3.5 default policy
def process_value_with_default_policy(global_obj, type_name, in_value, sink):
    default_policy = global_obj.factory.default_policy
    if default_policy is None:
        return None
    args = [type_name, sink]
    policy_value = get_policy_value(default_policy, type_name,
                                    stringify(in_value), args, False)
    if policy_value is None:
        return None
    return TrustedType(type_name, stringify(policy_value))


# ------------------------------------------- §4.2.4 sink 类型不匹配是否阻断
def should_sink_mismatch_be_blocked(global_obj, sink, sink_group, source):
    result = "Allowed"
    sample = source
    for prefix in ("function anonymous", "async function anonymous",
                   "function* anonymous", "async function* anonymous"):
        if sink == "Function" and sample.startswith(prefix):
            sample = sample[len(prefix):]
            break
    for policy in global_obj.csp_list:
        directive = policy.get("require-trusted-types-for")
        if not directive or sink_group not in directive:
            continue
        trimmed = sample[:40]
        _report(global_obj, Violation("require-trusted-types-for",
                                      policy.disposition, "trusted-types-sink",
                                      sink + "|" + trimmed))
        if policy.disposition == "enforce":
            result = "Blocked"
    return result


# -------------------------------------------------- §3.4 汇点取"合规字符串"
def get_trusted_type_compliant_string(global_obj, expected_type, in_value,
                                      sink, sink_group="script"):
    if isinstance(in_value, TrustedType) and in_value.type_name == expected_type:
        return in_value.data
    if not does_sink_type_require_trusted_types(global_obj, sink_group, True):
        return stringify(in_value)
    converted = process_value_with_default_policy(global_obj, expected_type,
                                                  in_value, sink)
    if converted is None:
        disposition = should_sink_mismatch_be_blocked(global_obj, sink,
                                                      sink_group,
                                                      stringify(in_value))
        if disposition == "Allowed":
            return stringify(in_value)
        raise TrustedTypeError("sink %s requires %s" % (sink, expected_type))
    if converted.type_name != expected_type:
        raise TrustedTypeError("default policy produced wrong type")
    return converted.data


# ------------------------------------------------------------- 汇点(§2.1.1)
def set_inner_html(global_obj, value):
    return get_trusted_type_compliant_string(global_obj, "TrustedHTML", value,
                                             "Element innerHTML")


def set_script_src(global_obj, value):
    return get_trusted_type_compliant_string(global_obj, "TrustedScriptURL",
                                             value, "HTMLScriptElement src")


def set_script_text(global_obj, value):
    return get_trusted_type_compliant_string(global_obj, "TrustedScript",
                                             value, "HTMLScriptElement text")


# ------------------------------------------------- §3.8 属性 → 类型映射表
def get_trusted_type_data_for_attribute(element_ns, tag, attr, attr_ns):
    """返回 (期望类型, sink 名),或 None 表示该属性不受 TT 约束。"""
    if attr_ns is None and element_ns in (HTML_NS, SVG_NS, MATHML_NS) \
            and attr.startswith("on") and len(attr) > 2:
        return ("TrustedScript", "Element " + attr)
    if attr_ns is None:
        if tag == "iframe" and attr == "srcdoc":
            return ("TrustedHTML", "HTMLIFrameElement srcdoc")
        if tag == "script" and attr == "src":
            return ("TrustedScriptURL", "HTMLScriptElement src")
        if tag == "script" and element_ns == SVG_NS and attr == "href":
            return ("TrustedScriptURL", "SVGScriptElement href")
    if attr_ns == XLINK_NS and tag == "script" and attr == "href":
        return ("TrustedScriptURL", "SVGScriptElement href")
    return None


def set_attribute(global_obj, element_ns, tag, attr, value, attr_ns=None):
    data = get_trusted_type_data_for_attribute(element_ns, tag, attr, attr_ns)
    if data is None:
        return stringify(value)
    expected_type, sink = data
    return get_trusted_type_compliant_string(global_obj, expected_type, value,
                                             sink)


# --------------------------------------------- §4.2.1.1 javascript: 预导航检查
def require_tt_pre_navigation_check(global_obj, url):
    if not url.startswith("javascript:"):
        return ("Allowed", url)
    encoded = url[len("javascript:"):]
    converted = process_value_with_default_policy(global_obj, "TrustedScript",
                                                  encoded, "Location href")
    if converted is None or converted.type_name != "TrustedScript":
        return ("Blocked", url)
    return ("Allowed", "javascript:" + converted.data)
