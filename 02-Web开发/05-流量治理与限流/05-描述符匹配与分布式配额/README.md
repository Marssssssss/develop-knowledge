# 描述符匹配与分布式配额

`envoyproxy/ratelimit` 服务的工作方式：Envoy 把请求的 **domain + 一组 descriptor 键值对**（`database: users`、`remote_address: 1.2.3.4`…）交给限流服务，服务在配置树里做深度匹配，算出限额，再在共享缓存（Redis / memcached）上按「域 + 描述符 + 窗口桶」组成的键做原子自增。多副本共享同一组键，这就是「分布式配额」的全部秘密。

## 一、匹配顺序（`config_impl.go:GetLimit`）

逐个 entry 走，每一步三条候选：

```
finalKey = entry.Key + "_" + entry.Value   ← ① 先找 key_value
   ↓ 未命中
遍历上一层的 wildcardEntries，wildcardMatch(parts, finalKey)   ← ② 再找通配
   ↓ 未命中
finalKey = entry.Key                        ← ③ 最后回落到「只按 key」的默认条目
```

三条容易踩的规则：

1. **`GET /` 默认条目就是「无 value」的那条**。配置里 `- key: database`（无 value）等价于默认值；请求 `database=ops` 会在 ① 落空后回落到它。
2. **深度必须完全匹配**才采用 limit。源码里只有 `i == len(descriptor.Entries)-1` 时才赋值，否则打日志 `request depth does not match config depth` 就跳过。请求比配置深、或比配置浅，都拿不到限额（返回 nil ≈ 不限流）。
3. **Envoy 侧携带的 `limit_override` 完全绕过配置树**：直接 `NewRateLimit(...)` 返回，且 `shadow_mode` / `quota_mode` / `detailed_metric` **全部写死 false**（源码注释：*When limit override is provided by envoy config, we don't want to enable shadow_mode*）。连 descriptor 是否在配置里都不检查。

### 通配匹配 `wildcardMatch`

配置键里的 `*` 把键切成若干 literal 段，匹配规则是：段数 1 → 全等；否则要求 value 以首段开头、以末段结尾、总固定字符数不超过 value 长度，中间段按顺序在剩余区间里依次出现（每段消耗一次 `strings.Index`）。

注意匹配对象是**请求侧拼出来的 `key_value` 串**，所以通配键要写成 `path_/api/*` 这种「键名 + 值模式」的形式，切分后的两段是 `["path_/api/", ""]`。

### 配置期校验

- 描述符 `key` 为空 → panic。
- 同一层 `finalKey` 重复 → panic `duplicate descriptor composite key`。
- `unlimited: true` **不能**同时给 `unit` → panic `should not specify rate limit unit when unlimited`。
- 非 unlimited 但 `unit` 缺失或不在枚举里 → panic `invalid rate limit unit`。

## 二、判定阈值（`base_limiter.go`）

`GetResponseDescriptorStatus` 的判据链，顺序即优先级、互斥：

| 判据 | 含义 |
| --- | --- |
| `cacheKey == ""` | 该 descriptor 没配限额 → 直接 `OK`，`CurrentLimit = nil` |
| local cache 命中 | 直接 `OVER_LIMIT`，`LimitRemaining = 0`，同时计 `OverLimit` + `OverLimitWithLocalCache` |
| `limitAfterIncrease > overLimitThreshold` | **严格大于**才过限；`overLimitThreshold = requests_per_unit` |
| `limitAfterIncrease > nearLimitThreshold` | `nearLimitThreshold = floor(threshold × nearLimitRatio)` |

统计记账（`checkOverLimitThreshold` / `checkNearLimitThreshold`，都用 `>=`）：

- 自增前 `before >= threshold` → **整份** `hits_addend` 计入 `OverLimit`；否则只计 `after - threshold`，并把 `threshold - max(near, before)` 计进 `NearLimit`。
- 自增前未过限但已近限 → 整份 addend 计入 `NearLimit`；否则只计 `after - near`。
- `shadow_mode` 下即使过限也**返回 OK**（只记账不分流），用于灰度上线新限额。
- `hits_addend` 可以 >1（一次请求扣多份配额），也可以为负（走 `GetResponseDescriptorStatusForNegativeHits`，直接 OK 并计 `TotalNegativeHits`）。

## 三、分布式缓存键（`cache_key.go`）

键的拼法是 `prefix + domain + "_"`，然后每个 entry 写 `key + "_" + value + "_"`，最后接**窗口桶起点**：

```
bucketStart = (now / divider) * divider        // divider 由 Unit 决定
```

- `month` 单位在启用 `use_calendar_month` 时改走 `utils.MonthStartUnix(now)`（**UTC** 当月 1 日 00:00:00），因为自然月长度不等，用固定 30 天除数会漂。
- `GenerateCacheKey` 返回的 `PerSecond` 标志只对 `SECOND` 单位为 true——限流服务据此决定是否额外上报秒级指标。
- `limit == nil` 时返回**空键**，与服务端 `GetResponseDescriptorStatus` 对空键的短路逻辑首尾呼应：没配限额的 descriptor 连缓存都不用查。
- `share_threshold` 开启时用**通配模式**替换实际 value 写入键，从而把一批同前缀的键合并成同一个计数器（共享阈值）。

多副本部署下，键的确定性就是配额的正确性：同域、同描述符、同窗口必须落到同一个键上。

## 四、代码结构

| 文件 | 内容 |
| --- | --- |
| `python/ratelimit_service.py` | `RateLimit` / `DescriptorNode` / `wildcard_match` / `RateLimitConfig.get_limit` / `LimitInfo` / `get_response_descriptor_status` / `generate_cache_key` / `month_start_unix` / `month_expiration_seconds` |
| `python/selfcheck_ratelimit_service.py` | 58 条断言（实跑全绿） |
| `python/main.py` | 五组请求的匹配结果、阈值判定表、三种单位的缓存键对照 |
| `go/ratelimit_service.go` | Go 侧同构实现（`WildcardMatch` / `Config.GetLimit` / `Status` / `GenerateCacheKey` / `MonthStartUnix`） |

本 demo 未覆盖的部分（作用域外）：真正的缓存后端实现（Redis / memcached 的原子自增与 TTL 抖动）、过期时间抖动、quota_mode 的额度扣减语义。

## 参考资料

- envoyproxy/ratelimit@main `src/config/config_impl.go` — <https://raw.githubusercontent.com/envoyproxy/ratelimit/main/src/config/config_impl.go>（`GetLimit` 三条候选与深度判据、`wildcardMatch`、`loadDescriptors` 的 unlimited/unit 与重复键校验、`NewRateLimit`）
- envoyproxy/ratelimit@main `src/limiter/base_limiter.go` — <https://raw.githubusercontent.com/envoyproxy/ratelimit/main/src/limiter/base_limiter.go>（`GetResponseDescriptorStatus` 分支顺序、`checkOverLimitThreshold` / `checkNearLimitThreshold` / `increaseShadowModeStats` 的 `>=` 记账、`IsOverLimitWithLocalCache`）
- envoyproxy/ratelimit@main `src/limiter/cache_key.go` — <https://raw.githubusercontent.com/envoyproxy/ratelimit/main/src/limiter/cache_key.go>（`GenerateCacheKey` 的拼接顺序、`PerSecond`、`MonthStartUnix` 分支、`share_threshold`）
- envoyproxy/ratelimit@main `src/utils/time.go` — <https://raw.githubusercontent.com/envoyproxy/ratelimit/main/src/utils/time.go>（`MonthStartUnix` / `MonthExpirationSeconds`，均为 UTC）
