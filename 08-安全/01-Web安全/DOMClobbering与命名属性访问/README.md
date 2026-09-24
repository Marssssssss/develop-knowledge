# DOM Clobbering 与 window 命名属性访问

`window.foo` 不一定是你以为的那个 `foo`。HTML 规范规定：**页面里带 `id` 或特定 `name`
的元素会自动成为 `window` 上的命名属性**。攻击者只要能注入一段不含 `<script>` 的 HTML，
就能覆盖应用依赖的全局变量——这就是 DOM Clobbering。

## 一、supported property names 的三部分（§7.2.2.3）

按**贡献元素在文档树中的顺序**取并集，忽略后出现的重复：

1. window 的 *document-tree child navigable target name property set*
2. 所有 **embed / form / img / object** 元素**非空** `name` 内容属性的值
3. 所有带 `id` 的元素的 `id`（**不限标签**）

关键不对称：

| 写法 | 是否成为 `window.x` |
| --- | --- |
| `<div id=x>` | 是 |
| `<a id=x>` | 是 |
| `<img name=x>` | 是（img 在四种标签里） |
| `<form name=x>` | 是 |
| `<div name=x>` | **否**（div 不在四种标签里） |
| `<a name=x>` | **否** |
| `<img name="">` | 否（空串不算） |

`id` 的覆盖面远大于 `name` —— 这也是为什么移除 `id` 属性比移除 `name` 更能止血。

## 二、两段循环：跨源 iframe 会「带走」同源的同名 iframe

property set 的算法是两段循环，不是一段：

```
第一段：按 tree order 遍历 navigable
        空 target name → continue
        集合里已有同名 → continue      ← 只保留第一个
        否则加入 firstNamedChildren
第二段：遍历 firstNamedChildren
        只有与 window 同源的才把 name 记入结果
```

规范原文的 **spices 例子**（页面在 `https://example.org/`）：

```html
<iframe src=https://elsewhere.example.com/></iframe>   <!-- 子文档把 window.name 设为 "spices" -->
<iframe name=spices></iframe>
```

结果 `window.spices` 是 **undefined**。原因是：

1. 第一个 iframe 的 target name 被子文档的 `window.name = "spices"` 改写成了 `spices`；
2. 第一段里它排在前面，占住了 `spices` 这个名；
3. 第二个（同源的）iframe 因此被判为重复而 `continue` 掉；
4. 第二段再按同源过滤，第一个 iframe 是跨源的，被剔除 —— 结果集合为空。

**一个跨源 iframe 能让同源的同名 iframe 一起消失。** 反过来，若同源那个排在前面，
`spices` 就在 supported names 里，且 `named objects` 会把两个 iframe 都算上，
取值时返回 tree order 上第一个的 `WindowProxy`。

## 三、取值优先级：navigable > 单元素 > HTMLCollection

```
objects = named objects（不过滤同源）
1. 若 objects 含 navigable → 返回 tree order 上第一个其 content navigable
   在 objects 里的 navigable container 的 active WindowProxy
2. 否则若 objects 只有一个元素 → 返回该元素
3. 否则 → 返回 HTMLCollection
```

第 3 条是实战中最常被利用的分支：注入**两个**同名元素，`window.x` 会变成
`HTMLCollection`。它是 truthy 的，所以 `if (window.x)` 这类检查能通过，
但 `x.href`、`x.value` 之类的属性访问会给出 `undefined`（集合本身没有这些属性）。

注意 `named objects` 与 `supported property names` **不是同一个集合**：前者不做同源
过滤，后者做。所以「能不能取到值」看后者，「取到什么」看前者。

## 四、典型注入与防御

```html
<!-- 攻击：覆盖应用读取的 window.config -->
<a id=config href="javascript:alert(1)"></a>
```

应用中 `if (window.config) { location = config.href }` 之类的写法就会被劫持。防御口径：

1. **不要用全局变量做配置传递**，用模块作用域或 `data-*` 属性 + 显式 `getElementById`。
2. 净化 HTML 时**一并移除 `id` 与 `name`**，而不是只过滤事件处理器属性。
3. 取值后做**类型校验**（`instanceof HTMLAnchorElement` / `typeof x === "string"`），
   而不是只判真假。
4. 关键路径用 `document.getElementById()` —— 规范自己在 §7.2.2.3 开头就写了
   「relying on this will lead to brittle code」，并建议改用 getElementById/querySelector。

## 五、口径与未建模项

- **`<img id=x name=x>` 计几次**：规范用三条目列举给出 named objects，未明说同一元素
  同时命中两条时是否计两次。本 demo 按**对象同一性去重**（计 1 次），因此单元素走
  「返回该元素」分支；若按字面计 2 次则会走 HTMLCollection 分支。
- **内建属性能否被覆盖**：规范只说 Window 带 `[Global]`，命名属性遵循
  *named properties object* 规则而非 legacy platform object 规则。至于
  `window.document` / `window.location` 这类内建名能否被 `<div id=document>` 覆盖，
  本 demo **未建模也未断言**——需要实浏览器验证，不凭推测下结论。
- `document` 命名属性（`document.forms`、form 的 `elements` 等）本 demo 未建模。
- navigable 的 `target name` 被子文档 `window.name` 改写这一点已建模（`Navigable.target_name`
  可变），但未建模 `window.name` 的完整读写语义。

## 代码结构

| 文件 | 内容 |
| --- | --- |
| `domclobber.py` | 三段并集 + 两段循环 property set + 取值优先级 |
| `domclobber.go` | 同模型的 Go 转写 |
| `selfcheck_domclobber.py` | Python 自检（实跑 **35** 断言） |
| `selfcheck_domclobber.go` | Go 自检（同套断言） |

## 参考资料（实际读过）

- HTML Living Standard §7.2.2.3「Named access on the Window object」—
  `https://html.spec.whatwg.org/multipage/window-object.html`（817681 B 实读）
  含 *document-tree child navigable target name property set* 的两段循环定义、
  supported property names 的三条列举、named objects 的定义、取值算法的三个分支、
  spices 例子、以及「[Global] → named properties object」的说明
