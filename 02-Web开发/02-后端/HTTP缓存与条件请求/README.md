# HTTP 缓存与条件请求（RFC 9111 / RFC 9110 §13）

缓存是后端最容易被「配错还以为对」的一块：`max-age` 和 `s-maxage` 谁生效、`Expires` 减的到底是谁的时间、`age` 到底怎么算、`Vary` 为什么一加就全 miss、条件请求的五个头谁先谁后——每一条规范都给了**确定的算术**，不是经验值。本 demo 把这些算术做成可执行模型。

## 一、新鲜度判据只有一行

```text
response_is_fresh = (freshness_lifetime > current_age)
```

**严格大于**。实测：`max-age=100` 的响应在 `current_age == 100` 的那一刻**已经是 stale**（不是「刚好还新鲜」）。这一秒的差别在「缓存命中率差一点」的排查里经常是答案。

## 二、`freshness_lifetime` 取第一个匹配（§4.2.1）

| 顺序 | 条件 | 取值 |
| --- | --- | --- |
| 1 | 共享缓存 **且** `s-maxage` 存在 | `s-maxage` |
| 2 | `max-age` 存在 | `max-age` |
| 3 | `Expires` 存在 | **Expires − Date** |
| 4 | 都没有 | 无显式过期 → 启发式 |

两条最容易踩的：

- **`s-maxage` 只对共享缓存生效**。同一条 `Cache-Control: s-maxage=60, max-age=600`，CDN 看 60 秒，浏览器看 600 秒（实测 60 vs 600）。所以「我明明配了 10 分钟，CDN 怎么 1 分钟就回源」是**符合规范**的。
- **`Expires` 减的是 `Date`，不是本地时钟**。规范原话：「this calculation is intended to reduce clock skew by using the clock information provided by the origin server whenever possible」。用 `Expires - now` 是错的，会让 origin 与 cache 的时钟偏移直接进到新鲜度里。

## 三、启发式新鲜度：10%，但**有显式过期时禁止**（§4.2.2）

> A cache MUST NOT use heuristics to determine freshness when an explicit expiration time is present in the stored response.

有 `Last-Modified` 时：「caches are encouraged to use a heuristic expiration value that is no more than some fraction of the interval since that time. A typical setting of this fraction might be 10%.」

实测：`Date − Last-Modified = 86400s` → 启发式 `8640s`。而一旦响应带了 `max-age=10`，启发式**必须关闭**，生命周期就是 10 秒——哪怕 `Last-Modified` 是一年前。这就是为什么「我没配缓存头，图片却缓存了一天」和「我配了 max-age=10，缓存却只有 10 秒」会同时成立。

## 四、Age 有三种口径（§4.2.3）

```text
apparent_age        = max(0, response_time - date_value)
response_delay      = response_time - request_time
corrected_age_value = age_value + response_delay

corrected_initial_age = max(apparent_age, corrected_age_value)   # 保守版
current_age           = corrected_initial_age + (now - response_time)
```

三种口径的差异是**可观测**的：

- `Date` 超前于本地时钟时 `apparent_age` 本应为负，被 `max(0, …)` 夹成 0 —— 实测「Date 超前 40 秒」与「Date 恰好等于 `response_time`」算出的 `current_age` **完全相同**。
- 保守版取 `max(apparent, corrected)`：当 `Date` 很久远而 `Age` 头很小的时候，两者差距可以到两个数量级（实测 1011 vs 11 秒）。规范说保守版用于「very old cache implementations that might not correctly insert Age」存在的场合。

## 五、什么时候允许出 stale（§4.2.4）

两条 MUST NOT：

1. 被**显式指令**禁止时——`no-cache`、`must-revalidate`（共享缓存还有 `proxy-revalidate`、`s-maxage`）。实测：`must-revalidate` 下即使客户端发了 `max-stale=100` 也**不许**出 stale。
2. 除非**断网**，或客户端/源站**显式允许**（`max-stale`）。

所以 `stale-while-revalidate` / `stale-if-error`（RFC 5861）本质是在第 2 条上开了一个口子，它们**不能**覆盖 `must-revalidate`。

## 六、Vary 匹配的误报集与漏报集（§4.1）

允许的归一化只有三种：加删空白、合并同名字段行、按该字段规范已知等价的归一化（如大小写）。

| 情形 | 结果 |
| --- | --- |
| `gzip` vs `GZIP`（Accept-Encoding） | 匹配（值不区分大小写） |
| `gzip,br` vs `gzip,  br` | 匹配（空白归一化） |
| `gzip` vs `br` | 不匹配 |
| 一方缺失 | **不匹配**——「absent ... can only match ... if it is also absent there」 |
| 双方都缺失 | 匹配 |
| `Vary: *` | **恒不匹配** |
| `gzip, br` vs `br, gzip` | 不匹配（重排只在「该字段规范已知顺序无意义」时才被许可） |

「一方缺失即不匹配」是 `Vary: Accept-Encoding` 最常见的踩坑：带 AE 头的请求缓存了一份，不带 AE 头的请求**不能**复用它，反之亦然。

多条候选时：有有效 `Vary` 的优先于没有的；再按 `Date` 取最新。

## 七、条件请求的生成与校验（§4.3）

生成（§4.3.1）：

- 有 `ETag` → **MUST** 发 `If-None-Match`
- 单条、非子范围、有 `Last-Modified` → **SHOULD** 同时发 `If-Modified-Since`（为的是兼容不认 ETag 的老中间盒）
- 子范围请求 → 不发 `If-Modified-Since`

校验响应（§4.3.3）：304 → 更新并复用；完整响应 → 替换；**5xx** → 「can either forward this response ... or act as if the server failed to respond」，后者下可以沿用 stale 或重试。

304 更新哪些条目（§4.3.4）——按**第一个匹配**的规则：

1. 新响应含**强校验器** → 更新初始集里带同一强校验器的那些；一个都不匹配 → **MUST NOT 更新任何条目**
2. 只有弱校验器 → 更新匹配项中**最近**的那一条
3. 完全没有校验器、且初始集只有一条、且该条也没有校验器 → 更新它

## 八、前置条件优先级（RFC 9110 §13.2.2）

```text
1. If-Match              （仅 origin server）  不匹配 → 412
2. If-Unmodified-Since   （仅 origin server）  不满足 → 412
3. If-None-Match                               命中   → GET/HEAD 304，其它方法 412
4. If-Modified-Since     （仅 GET/HEAD，且无 If-None-Match）未修改 → 304
5. If-Range + Range      （仅 GET）            满足   → 206
6. 否则执行方法
```

三个反直觉的实测结论：

- **`If-Match` 对缓存不适用**。缓存（非 origin）收到 `If-Match: "v1"` + `If-None-Match: "v2"` 且 ETag 是 `v2` 时，会**跳过第 1 步**直接给出 304；而 origin server 在同样请求下给 **412**。这是「同一请求经过 CDN 和直连源站行为不同」的规范级原因。
- **`If-None-Match` 压过 `If-Modified-Since`**。ETag 命中就直接 304，哪怕 `If-Modified-Since` 显示资源已更新。
- **非 GET/HEAD 的 `If-None-Match` 命中是 412 不是 304**。
- `If-Range` 只在**同时**有 `Range` 且方法是 GET 时才评估。

## 九、运行方式

```bash
python selfcheck_cache.py     # 56 项断言
```

## 十、关键代码

- `main.py` — `freshness_lifetime` / `heuristic_lifetime` / `calculate_age` / `may_serve_stale` / `vary_matches` / `select_stored` / `build_validation_request` / `freshen` / `evaluate_preconditions`
- `cache.go` — Go 侧的新鲜度、Age、stale 判定
- `precond.go` — Go 侧的 Vary 匹配与前置条件优先级（与 `cache.go` 同包，需 `go run .`）
- `selfcheck_cache.py` — 误报集/漏报集与互斥口径对照

## 十一、注意事项与常见坑

1. `max-age=0` 与 `no-cache` 不等价：`max-age=0` 是「立即过期，但可以复用 stale（若允许）」，`no-cache` 是**每次必须校验**。
2. `no-store` 是不许存储，`no-cache` 是可以存但必须校验——两者常被混用。
3. `Expires` 是 HTTP/1.0 遗留，同时存在时 `max-age` 赢。
4. `Age` 头是**估计值**，链式缓存里每一跳都会累加自己的驻留时间。
5. 响应没有 `Date` 时，规范允许用「收到响应的时刻」代替——这时时钟偏移无处消解。
6. `Vary: *` 等价于「永不复用」，常被当作禁用缓存的粗暴手段。
7. 启发式只适用于**「heuristically cacheable」**的状态码（200/203/204/206/300/301/404/405/410/414/501）或被显式标记为可缓存的响应。
8. 时钟：规范明确要求「MUST NOT allow local time zones to influence the calculation」，且带非 GMT 时区缩写的日期应视为无效。

## 十二、参考资料

- RFC 9111 — HTTP Caching — <https://www.rfc-editor.org/rfc/rfc9111.txt>（§4.1 Vary 缓存键、§4.2 新鲜度、§4.2.1-4.2.4 各算法、§4.3 校验、§4.3.4 freshening、§5.2 各指令）
- RFC 9110 — HTTP Semantics — <https://www.rfc-editor.org/rfc/rfc9110.txt>（§13 条件请求、§13.2.2 前置条件优先级、§12.5.5 Vary、§8.8.3 ETag）
- RFC 5861 — HTTP Cache-Control Extensions for Stale Content（stale-while-revalidate / stale-if-error）
