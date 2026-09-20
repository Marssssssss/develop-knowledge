# Trusted Types 与 DOM XSS

> CSP 的 `script-src` 是在"**加载**"这一侧设卡;Trusted Types 是在"**写入**"这一侧设卡。
> `el.innerHTML = userInput` 之所以危险,不是因为字符串里有 `<script>`,而是因为
> **汇点(sink)把字符串当成了标记语言来解释**。Trusted Types 的做法很极端:
> 让这些汇点**只接受一个类型化的对象**,而这个对象只能由你亲手写的 policy 产生。

## 1. 简介

规范原文(§2.3)把这件事说得很直白:类型对象的构造函数不暴露,只能通过 policy 创建。
于是"哪些代码能产出可注入的值"变成了一个**可枚举的小集合** ——
`createHTML` / `createScript` / `createScriptURL` 的调用点,
而不需要去审计整个代码库里的每一处 `innerHTML`。

## 2. 原理详解

### 2.1 三个类型、三个回调

| 类型 | 回调 | 典型汇点 |
| --- | --- | --- |
| `TrustedHTML` | `createHTML` | `Element.innerHTML` / `outerHTML` / `iframe.srcdoc` / `document.write` |
| `TrustedScript` | `createScript` | `HTMLScriptElement.text` / `eval` / `new Function` |
| `TrustedScriptURL` | `createScriptURL` | `HTMLScriptElement.src` / `SVGScriptElement.href` |

`§3.8` 的属性表还规定:`on*` 事件处理器内容属性 → `TrustedScript`(sink 名是
`"Element " + 属性名`),`iframe srcdoc` → `TrustedHTML`。

**不在表里的属性(id / class / data-*)**不受约束 —— Trusted Types 只覆盖
DOM XSS 注入汇点,`§4.2.1` 目前也只定义了 `'script'` 这一个 sink 组。

### 2.2 §3.4 是整套机制的心脏

```text
getTrustedTypeCompliantString(expectedType, input, sink, sinkGroup):
  ① input 已经是 expectedType        → 直接返回它的字符串值
  ② 该 sink 组不要求 Trusted Types   → 直接返回字符串（含 report-only 也算"要求"）
  ③ 否则走 default policy（§3.5）
  ④ default policy 产出 null/undefined:
       → 报违规；强制模式抛 TypeError
       → report-only 模式**返回原始值**（规范原话：rejection will be reported,
         but ignored in a report-only mode）
```

第 ④ 步的两个分支是本 demo 重点断言的地方 —— 它决定了
"上了 report-only 之后页面会不会白屏"。

### 2.3 两个 CSP 指令各管一段

| 指令 | 管什么 | 例子 |
| --- | --- | --- |
| `require-trusted-types-for 'script'` | 汇点**是否强制**要类型化值 | 只有 `'script'` 一组 |
| `trusted-types a b default` | **能建哪些 policy** | `*` 通配、`'none'` 全禁、`'allow-duplicates'` 允许重名 |

`§4.2.5` 有个容易踩的 note:**`'none'` 与其它值并存时会被忽略**
(`trusted-types one 'none'` 等价于 `trusted-types one`)。
空值(`trusted-types;`)则表示一个 policy 都不能建。

### 2.4 违规报告长什么样

`§4.2.4`:`resource` 固定为 `"trusted-types-sink"`,
`sample` = `sink + "|" + 值的前 40 个字符`。所以一条真实报告是:

```text
sample = "Element innerHTML|<img src=x onerror=alert(1)>"
```

`sample` 只截断到 40 字符,**这是有意的** —— 它够你定位问题,又不至于把用户的
完整数据搬到报告端点上。`§4.2.5` 的 policy 创建违规则把 `resource` 写成
`"trusted-types-policy"`,`sample` 是 policy 名的前 40 字符。

### 2.5 `javascript:` 导航也过 default policy(§4.2.1.1)

`location.href = "javascript:..."` 这类导航在**预导航检查**阶段就会被拦:
剥掉 `javascript:` 前缀后送进 default policy 的 `createScript`(sink 名
`"Location href"`),拿不到 `TrustedScript` 就是 `"Blocked"`。
注意原文强调:**没有任何其它 CSP 指令在预导航阶段处理 `javascript:` URL**。

## 3. 关键代码

```python
def get_trusted_type_compliant_string(global_obj, expected_type, in_value, sink, sink_group="script"):
    if isinstance(in_value, TrustedType) and in_value.type_name == expected_type:
        return in_value.data
    if not does_sink_type_require_trusted_types(global_obj, sink_group, True):
        return stringify(in_value)
    converted = process_value_with_default_policy(global_obj, expected_type, in_value, sink)
    if converted is None:
        disposition = should_sink_mismatch_be_blocked(global_obj, sink, sink_group, stringify(in_value))
        if disposition == "Allowed":          # report-only
            return stringify(in_value)
        raise TrustedTypeError(...)
    return converted.data
```

## 4. 运行方式

```bash
cd TrustedTypes与DOMXSS
python selfcheck_trustedtypes.py     # 47 条断言,输出 "ALL OK"
go run .
```

## 5. 性能与工程边界

- **运行时开销几乎为零**:类型对象只包一个字符串,汇点处多一次 `instanceof`
  判断。真正的成本在**迁移** —— 每一个 `innerHTML =` 都要改。
- **default policy 是迁移期的拐杖,不是终态**。`§2.3.4` 原话:一个宽松的
  (no-op)default policy 会**抵消掉 Trusted Types 的全部收益**。本 demo 里
  `createHTML: s => s` 这种写法特意留着,是为了展示它会让强制模式形同虚设。
- **report-only 先行**:因为 §3.4 第 ④ 步在 report-only 下返回原始值,
  可以先用 `Content-Security-Policy-Report-Only` 跑一两周收集 `sample`,
  再切强制 —— 不会白屏。

## 6. 注意事项与常见坑

1. **Trusted Types 防的是"开发者手滑",不是"恶意第一方代码"**。§5 第一句就声明:
   它假设应用作者非恶意,不打算防御主动绕过策略的恶意代码。能建 policy 的人
   就能建一个 no-op policy。
2. **policy 引用应当当 capability 管**(§2.3 的 note):把宽松 policy 关在闭包
   或模块内部,只把返回值交给需要它的那一处代码。
3. **`on*` 属性的判定依赖"事件处理器内容属性"这个概念**,而规范自己在 §3.8 里
   挂了一个 issue(https://github.com/w3c/trusted-types/issues/520)说这个
   概念有歧义。本 demo 按"`on` 开头且长度 > 2"实现并注明口径。
4. **类型错配不等于安全**:`TrustedScript` 传给 `innerHTML` 汇点同样会被拒,
   因为它不是 `TrustedHTML` —— 但这也意味着如果你只实现一个 `createHTML`
   就去造 `TrustedScript`,会在 `throwIfMissing` 处直接抛错(自检已覆盖)。
5. **`sample` 会外泄 40 个字符**。如果注入值里可能有敏感数据,报告端点要按
   敏感数据处理。
6. **别把 sanitizer 的返回值当 trusted**:`createHTML` 回调内部返回什么就是什么,
   Trusted Types **不做任何净化**。净化是你的 sanitizer 的责任。

## 7. 参考资料(本 demo 实际读取)

- W3C — *Trusted Types* <https://www.w3.org/TR/trusted-types/>
  (§2.3 Policies、§3.1–§3.8 算法、§4.2.1.1 预导航检查、§4.2.3–§4.2.5、§5 Security Considerations)
- W3C — *Content Security Policy Level 3* <https://www.w3.org/TR/CSP3/>
  (违规对象与报告基础设施)
- MDN — *Trusted Types API* <https://developer.mozilla.org/docs/Web/API/Trusted_Types_API>
