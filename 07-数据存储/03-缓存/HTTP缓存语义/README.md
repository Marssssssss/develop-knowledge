# HTTP 缓存语义（RFC 9111 + RFC 5861）

## 一、简介

HTTP 缓存是**唯一一个标准协议层就规定死判定算法**的缓存系统。本 demo 把 RFC 9111（HTTP Caching）的**新鲜度计算**、**Age 计算**、**陈旧复用限制**、**Vary 二级键**，以及 RFC 5861 的 `stale-while-revalidate` / `stale-if-error`，逐条做成可验证的判定函数。

与 Redis/Memcached 那类「应用层 KV 缓存」不同，HTTP 缓存的难点不在数据结构，而在**语义边界**：`no-cache` 不等于 `no-store`、`age == max-age` 时已经算陈旧、`s-maxage` 只对共享缓存生效、`Vary: *` 永远不匹配。

## 二、原理详解

### 2.1 新鲜度判定是**严格大于**（§4.2）

```
response_is_fresh = (freshness_lifetime > current_age)
```

规范原文用的是 `>`，不是 `>=`。所以 `max-age=100` 的响应在 `current_age == 100` 的那一刻**已经陈旧**。这条边界在实现里极易写成 `>=`，后果是响应被多复用一个时钟滴答——对普通资源无害，对带 `must-revalidate` 的资源则是协议违规。

### 2.2 freshness_lifetime 的取值顺序（§4.2.1）

按序取**第一个**匹配，命中即停止：

1. 共享缓存且存在 `s-maxage` → 用它
2. `max-age` → 用它
3. `Expires` → `Expires - Date`
4. 都没有 → 走启发式

关键点：`s-maxage` 只对**共享缓存**（代理、CDN）生效，浏览器这类私有缓存必须忽略它。同一条响应发给浏览器和 CDN，寿命可能完全不同。

第 3 步用 `Expires - Date` 而不是 `Expires - now`，目的是**消除客户端与源站的时钟偏差**——两个值都出自源站，相减后偏差抵消。

### 2.3 启发式新鲜度：10% 规则（§4.2.2）

源站没给显式过期时间时，缓存**可以**猜，但规范限制了猜测的适用条件：

- 有显式过期时间时 **MUST NOT** 使用启发式
- 只能用于「状态码被定义为 heuristically cacheable」（200/203/204/206/300/301/308/404/405/410/414/501 等）或显式标了 `public` 的响应

若响应带 `Last-Modified`，规范建议取「距今间隔的某个比例」，**典型取值 10%**：

```
heuristic_lifetime = (now - Last-Modified) * 0.10
```

注意这只是「建议」（encouraged），不是强制——所以不同 CDN 的启发式寿命可以不一样，同一资源在不同厂商下缓存时长不同是**合规**的。

### 2.4 Age 计算：两种独立算法（§4.2.3）

```
apparent_age       = max(0, response_time - date_value)
response_delay     = response_time - request_time
corrected_age_value = age_value + response_delay

# 保守合成（路径上可能有不插 Age 的老旧缓存）
corrected_initial_age = max(apparent_age, corrected_age_value)

resident_time = now - response_time
current_age   = corrected_initial_age + resident_time
```

两个易错点：

- `apparent_age` 为负时**截断到 0**。客户端时钟落后于源站时 `response_time < date_value`，不截断会算出负 age，进而让「已过期」的响应被判定为新鲜。
- 保守合成取 `max`，非保守只用 `corrected_age_value`。当本地时钟与 `Date` 头分歧很大时，两者能差出**整条时钟偏移量**（本 demo 中实测差 900 秒）。合成方式的选择是一个安全/延迟的取舍：取 `max` 更保守（更容易判定为陈旧）。

### 2.5 陈旧复用限制（§4.2.4 + §5.2.2）

陈旧响应**默认不能**用来应答，除非「断网」或「客户端/源站显式允许」。显式禁止项：

| 指令 | 语义 |
| --- | --- |
| `no-cache`（无参） | 必须回源校验后复用；**不是「不缓存」** |
| `no-cache="Set-Cookie"`（带参） | 可以复用，但必须排除列出的头字段 |
| `must-revalidate` | 陈旧后**必须**校验成功才能复用；断网时 **MUST** 产生错误响应（SHOULD 504），**不能**退而复用陈旧响应 |
| `proxy-revalidate` | 同上，但只对共享缓存生效 |
| `s-maxage` | 对共享缓存隐含 must-revalidate 语义 |

`no-cache` vs `no-store` 是最经典的混淆：`no-store` 是**不许存储**（连磁盘都不能落），`no-cache` 是**可以存但每次都得问**。把两者写反，前者会导致敏感内容落盘，后者会让缓存完全失效。

`must-revalidate` 的 504 语义值得单独强调：规范写的是「if a cache is disconnected, the cache MUST generate an error response rather than reuse the stale response」，且 SHOULD 用 504。也就是说**断网不是复用陈旧响应的理由**——除非配了 `stale-if-error`。

### 2.6 RFC 5861：两个 stale 扩展

`stale-while-revalidate`（SWR）定义的是**过期之后的一个宽限窗口**：

```
Cache-Control: max-age=600, stale-while-revalidate=30
```

- `age < 600` → fresh，直接用
- `600 <= age < 630` → 立即返回陈旧响应，**同时后台异步校验**（不阻塞请求）
- `age >= 630` → 真陈旧，下一个请求阻塞等待校验

窗口边界同样是**左闭右开**：`age` 恰好等于 `600 + 30` 时已经出了窗口。

`stale-if-error` 只在**出错**时生效，且规范明确定义了「错误」的范围：**500 / 502 / 503 / 504**。注意 5xx 之外的状态码（如 404、429）不在此列。它同样有上限 `lifetime + sie`。

### 2.7 Vary 与二级缓存键（§4.1）

`Vary` 决定同一 URI 下能存几份表示。匹配规则：

- `Vary` 列出的每个头字段，新请求的值必须与原始请求**完全一致**
- **头字段缺失只能匹配缺失**（一个有 `Accept-Encoding`、一个没有 → 不匹配）
- 允许空白归一化、多行合并、语义等价归一化
- **`Vary: *` 永远不匹配**——等于禁用缓存

「缺失只能匹配缺失」这条在实现上很反直觉：很多手写实现用 `stored.get(f) != new.get(f)` 判断，当两边都缺失时 `None != None` 为假、被判成匹配，于是把无 `Accept-Encoding` 请求的结果错误地返回给带 `Accept-Encoding` 的请求。

## 三、对比

| 维度 | HTTP 缓存 | Redis/Memcached |
| --- | --- | --- |
| 失效依据 | 时间（max-age / Expires）为主 + 条件请求校验 | 显式删除 / TTL / 淘汰 |
| 一致性 | 天然最终一致，靠 `must-revalidate` 收紧 | 取决于应用层的双写策略 |
| 谁能读 | 私有缓存 / 共享缓存语义不同 | 无此区分 |
| 键 | URI + Vary 二级键 | 应用自定义字符串 |
| 「过期」能否继续用 | 有 SWR / SIE 协议级宽限 | 无，过期即不可见 |

## 四、环境

- Python 3.13（纯标准库）
- Go 1.22+（仅 `fmt` / `os` / `sort` / `strconv` / `strings` 标准库）

## 五、运行方式

```bash
cd 07-数据存储/03-缓存/HTTP缓存语义
python http_cache_selftest.py     # Python 实现 + 42 条断言
go run http_cache.go              # Go 实现 + 28 条断言
```

## 六、关键代码

`http_cache_core.py` 中三个最容易写错的函数：

```python
def response_is_fresh(lifetime, age):
    return lifetime > age            # 严格大于：== 时已陈旧

def current_age(age_value, date_value, request_time, response_time, now,
                conservative=True):
    apparent_age = max(0, response_time - date_value)   # 负值必须截断
    corrected_age_value = (age_value or 0) + (response_time - request_time)
    initial = max(apparent_age, corrected_age_value) if conservative \
              else corrected_age_value
    return initial + (now - response_time)

def vary_match(vary, stored_req_headers, new_req_headers):
    for field in (f.strip().lower() for f in vary.split(",")):
        if field == "*":
            return False                                 # 永远不匹配
        s, n = stored_req_headers.get(field), new_req_headers.get(field)
        if (s is None) != (n is None):                   # 缺失只能匹配缺失
            return False
        if s != n:
            return False
    return True
```

`split_commas` 是引号感知的逗号切分——`no-cache="Set-Cookie,ETag"` 是一个指令，朴素 `split(",")` 会把它拆成两个并把引号留在结果里。

## 七、性能边界

- 判定本身是 O(1)（指令解析 O(指令数)），可忽略
- 真正的成本在**存储的表示份数**：`Vary` 字段基数之积就是份数上限。`Vary: User-Agent, Accept-Encoding, Cookie` 会让缓存命中率断崖式下跌——`Cookie` 尤其致命，因为它几乎每人一个值
- 启发式新鲜度会让**没有任何缓存指令的响应也被缓存**，这是 CDN「明明没配缓存却拿到旧页面」的常见成因
- `stale-while-revalidate` 的窗口越大，后台回源请求越可能被合并；窗口为零则退化为普通过期

## 八、注意事项与常见坑

1. **`no-cache` ≠ `no-store`**。想「不缓存敏感内容」要用 `no-store`；`no-cache` 只是要求每次回源校验，内容照样可能落盘。
2. **`age == max-age` 已陈旧**。用 `>=` 判断「还新鲜」会多复用一个滴答。
3. **`s-maxage` 对私有缓存无效**。测试时用浏览器看不出来，必须用代理/CDN 验证。
4. **`Expires` 依赖 `Date`**。源站不返回 `Date` 时规范允许用「收到响应的时刻」代替，这时时钟偏差直接传导到寿命计算。
5. **时钟落后会让 `apparent_age` 变负**，必须 `max(0, ...)` 截断，否则陈旧响应会被判为新鲜。
6. **`Vary: *` 是禁用缓存**，不是「匹配一切」。
7. **`Vary` 头缺失只能匹配缺失**，用 `dict.get()` 直接比大小会在这条上出错。
8. **`must-revalidate` + 断网必须报错（SHOULD 504）**，不能「好心地」返回陈旧数据；要允许就得显式配 `stale-if-error`。
9. **`stale-if-error` 只认 500/502/503/504**。429、404 不算「错误」，不会触发陈旧复用。
10. **引号里有逗号时不能朴素 `split(",")`**，这是手写 Cache-Control 解析器的头号 bug。

## 九、参考资料

- RFC 9111《HTTP Caching》§3（Storing Responses）、§4.1（Vary）、§4.2（Freshness）、§4.2.1（Freshness Lifetime）、§4.2.2（Heuristic Freshness）、§4.2.3（Calculating Age）、§4.2.4（Serving Stale）、§5.2（Cache-Control 指令）、§5.2.2.1~5.2.2.4 — https://www.rfc-editor.org/rfc/rfc9111.txt
- RFC 5861《HTTP Cache-Control Extensions for Stale Content》§3（stale-while-revalidate）、§4（stale-if-error） — https://www.rfc-editor.org/rfc/rfc5861.txt
