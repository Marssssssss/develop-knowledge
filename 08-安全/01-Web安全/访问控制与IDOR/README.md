# 访问控制与 IDOR(对象级授权)

> OWASP Top 10 2021 的 **A01 失效的访问控制**排第一位。它的失守方式往往极其朴素:
> 代码检查了"你有没有这个角色",却忘了检查"这个对象是不是你的"。

## 简介

三类越权,一种根因 —— **把"类型级权限"当成了"对象级权限"**:

```text
水平越权:GET /accountTransactions?acct_id=901   ← 别人的账户,角色检查照样通过
垂直越权:普通用户调用 POST /admin/deleteUser    ← 少了一层"这个动作是否该角色持有"
跨租户:  租户 A 的用户读到租户 B 的对象        ← 只在"应用层"过滤,没在对象层过滤
```

本 demo 实现一个策略引擎,把 OWASP Authorization Cheat Sheet 的三条基线(默认拒绝 / 每请求服务端校验 /
对象级校验)写成可断言代码,并**故意保留一个"纯 RBAC 版本"** 来演示它在哪里失守。

## 原理详解

**1. 决策链路(deny-overrides + 默认拒绝)**

```text
① 显式拒绝规则命中 → 拒绝(优先于任何 allow)
② allow 规则命中   → 通过类型级检查
③ 对象级校验       → 所有权 / 协作者关系 / 租户边界 / 公开只读
④ 都没命中         → 拒绝(默认拒绝,理由是"deny by default",不是"没规则")
⑤ 策略抛异常       → 拒绝(失败必须安全退出,不能因为异常而放行)
```

`Decision` 里同时记下 `reason` 与 `rule`,审计时既能回答"放没放"也能回答"谁批的"。

**2. RBAC / ABAC / ReBAC 的分工**

| 模型 | 判定依据 | 擅长 | 短板 |
| --- | --- | --- | --- |
| RBAC | 角色 → 权限 | 类型级动作(能否 `invoice:read`) | **对象级/水平控制几乎无能为力**(角色爆炸、检查容易漏) |
| ABAC | 主体属性 + 客体属性 + 环境 | `在班 + 同租户` 这类多因素条件 | 属性来源必须可信 |
| ReBAC | 主体与客体之间的关系 | `owner` / `collaborator` / `auditor` 的 AND-NOT 组合 | 关系要随对象生命周期清理 |

OWASP 的结论是"**ABAC 与 ReBAC 通常应优先于 RBAC**";本实现的姿势是三者叠加:RBAC 给类型级许可以外的宽松判断、ABAC 管环境、ReBAC 管对象级 —— 但**每一层都必须显式配置**。

**3. 宽范围访问也要显式授予**

`admin` 这个角色名**不应该**自动等于"能碰所有对象"。本 demo 里把"租户级审计"写成一条关系元组:

```python
relate("root", "auditor", "tenant:t1")      # 显式租户级审计授权
```

没有这条关系时,即使是 admin 也会在对象级校验处被拒(理由 `object-level`);有这条关系时才放行 —— 且仍受租户边界约束。

**4. 客户端属性一律不可信(属性出处分仓)**

`Subject` 把属性分成两仓:`attrs`(服务端从会话/JWT 里取的)和 `untrusted`(客户端自称的,如 `X-Admin: true`)。决策只看前者。OOXML/CSRF 之类的请求体字段、HTTP 头、查询参数都属于后者。

**5. 间接引用与失败安全退出**

- **间接引用**:给用户看到的 ID 是**会话级句柄**(`h1`),`resolve()` 只在自己会话的表里查。别人的句柄解析不到,顺手改 URL 也没用。OWASP 提醒这只是"降低可利用性",**对象级校验仍不可省**。
- **失败安全退出**:`guard()` 对外只抛 `403 forbidden`,细节(`rule`/`reason`)留在 `Decision` 与审计里 —— 避免 CWE-209(错误消息泄露内部信息)。
- **审计**:每条决策一行(主体/动作/对象/结果/规则),顺序与调用一致,且**失败也要留痕**(否则事后无从归因)。

**6. 权限蔓延(privilege creep)**

角色重配必须**显式覆盖**而不是隐式继承。本 demo 断言"重配后旧权限不残留":撤销 `accountant` 的 `invoice:read` 后,同一角色立刻失去该权限 —— 这对应 OWASP 说的"撤销已授予的权限比新增难得多,所以初始就要谨慎"。

## 对比:三种模型对同一批请求的投票

| 请求 | 纯 RBAC | +ABAC(在班) | +ReBAC(对象级) |
| --- | --- | --- | --- |
| 会计读自己的发票 | ✅ | ✅ | ✅ |
| 会计读同事的发票 | ✅ **越权** | ✅ **越权** | ❌ 拦住 |
| 下班的会计读发票 | ✅ **越权** | ❌ 拦住 | — |
| 会计读敏感发票 | ✅ **越权** | ✅ **越权** | ❌ 显式 deny 覆盖 |
| 租户 A 的 admin 读租户 B | ✅ **越权** | ✅ **越权** | ❌ 租户边界拦截 |

只有把三层叠起来,五行全对 —— 这就是"纵深防御"在授权上的含义。

## 环境

- Python 3.9+(标准库 `dataclasses`)
- Go 1.18+(`access_model.go` 为对照实现;**本机无 go 工具链**,走人工审查 + 结构自检,见下)

## 运行方式

```bash
python access_check.py     # Python 侧 41 项断言
# Go 侧(有工具链时):
#   go run access_model.go
python ../../../_docs/tools/bracket_check.py access_model.go   # 括号配平(无工具链时的兜底)
```

## 关键代码

```python
# 对象级校验:每条分支都对应一种"是否真的有关系"
if res.tenant is not None and subj.attrs.get("tenant") != res.tenant:
    return Decision(False, "cross-tenant access", "tenant-isolation")
if (subj.uid, "auditor", f"tenant:{res.tenant}") in self.relations:
    return Decision(True, "tenant auditor", "rebac:auditor")
if res.owner == subj.uid:
    return Decision(True, "owner", "rebac:owner")
if (subj.uid, "collaborator", f"{res.kind}:{res.rid}") in self.relations:
    return Decision(True, "collaborator", "rebac:collaborator")
return Decision(False, "no relation to this object", "object-level")
```

## 性能边界

- 决策是纯内存规则扫描,O(规则数);ReBAC 关系用集合查 O(1)。生产里真正的问题是**关系规模**与撤销及时性,而不是单次判定耗时。
- RBAC 在"用户经 HTTP 头传角色"的场景会撞上 **role explosion**:头部有大小限制,角色多了装不下,只能传 uid 再由服务端查角色 —— 代价是每个请求多一次查询。
- Go 侧无工具链验证:仅经 `bracket_check.py`(BALANCED)与人工逐处审查(含 import 使用、多返回值、struct 字面量字段序)。**这不等于编译通过**。
- 本 demo 未涉及策略语言(XACML/Rego)与执行点分离,也未做属性来源的签名验证。

## 注意事项与常见坑

- **"有权限读这类资源" ≠ "能读这类里的每个对象"**。这是 IDOR 的全部内容。对象级校验必须对**每个请求的每个对象**执行,不能只在列表接口上做过滤。
- **不要靠"ID 猜不到"**。随机化 ID / UUID 属于 security through obscurity,OWASP 明确说"通常不够"。顺序 ID 直接暴露是更明显的反模式。
- **客户端检查只能改善体验**。前端隐藏按钮毫无安全价值;真正的判定必须在服务端/网关/无服务器函数里。
- **逐方法手写 `if hasRole(...)` 必然漏一个**。要的是"全局配置 + 单点执行"(中间件/过滤器/策略引擎),本 demo 的 `Engine.decide()` 就是这个单点。
- **静态资源同样要过策略**:对象存储(S3 之类)配错是近年高频事故;静态资源应与其它资源走同一套访问控制逻辑。
- **失败路径也是攻击面**:策略异常、规则缺失、缓存击穿时的默认行为必须是**拒绝**;顺序反转(先 allow 后判 deny)会让"显式拒绝"形同虚设 —— 本 demo 用 `deny-overrides` 钉死这一点,并有断言覆盖。
- 角色/权限重配后**旧授权不会自动消失**是常态(缓存、长会话、Token 未过期)。断言里的"重配后旧权限不残留"只覆盖引擎内的语义,真实系统还要处理缓存与令牌撤销。

## 参考资料(实际联网读过)

- OWASP *Authorization Cheat Sheet* — <https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html>(最小权限横纵双向、默认拒绝、每请求校验、ABAC/ReBAC 优于 RBAC、对象级授权与 CWE-639、客户端检查不可信、失败安全退出、日志与测试清单)
