# CDN 边缘缓存的一致性哈希：libketama 与 nginx chash

把两款**真实在用**的一致性哈希实现做成可断言的最小模型，并测出「扩容时要搬多少 key」：

- **libketama**（memcached 生态的经典 Ketama 环，md5 + 4 段取点）
- **nginx `hash ... consistent`**（`ngx_http_upstream_hash_module`，crc32 链式取点）

两者的环结构、哈希方式、查找边界处理完全不同，但**目标一致**：节点数变化时只搬动约 `1/(n+1)` 的 key。

## 事实来源

所有常量、公式与分支都来自下列**实际读过的**源码，未在官方没写定量处编造数值：

| 内容 | 位置 |
| --- | --- |
| `ketama_md5_digest()` | `RJ/ketama` `libketama/ketama.c:194` |
| `ketama_hashi()`：md5 前 4 字节小端 | `ketama.c:335` |
| `ketama_get_server()` 二分与「回滚到 0」 | `ketama.c:348` |
| 环构建：`ks = floorf(pct*40*numservers)`、一次 md5 取 4 点、`qsort` | `ketama.c:421-465` |
| `ketama_compare()`：按 point 升序 | `ketama.c:657` |
| crc32 表（标准反射 CRC-32，poly `0xEDB88320`） | `nginx` `src/core/ngx_crc32.c:34-35` |
| `ngx_crc32_init/update/final` | `nginx` `src/core/ngx_crc32.h:54-74` |
| `npoints = total_weight * 160` | `ngx_http_upstream_hash_module.c:362` |
| host/port 切分（`unix:` 前缀、从尾部扫 `:`） | 同上 `:389-418` |
| base hash = `crc32(HOST \0 PORT)`，其中 `\0` 是 `("", 1)` | 同上 `:421-424` |
| 每个点 `crc32(base ‖ PREV_HASH)`，链条从 0 开始 | 同上 `:426-443` |
| 排序 + 相邻去重 | 同上 `:450-461` |
| `find_chash_point()`：找第一个 `point >= hash` | 同上 `:490-511` |
| `tries > 20` 回退 round-robin | 同上 `:585`、`:686` |

## 目录结构

```
python/ketama.py              libketama 环转写
python/ngx_chash.py           nginx chash 环转写
python/selfcheck_ketama.py    44 条断言
python/selfcheck_chash.py     51 条断言
python/main.py                环规模 / 分布 / 重迁移实测
go/{ketama.go, chash.go, main.go, go.mod}   Go 转写
```

## 运行

```bash
cd python && python main.py
cd python && python selfcheck_ketama.py && python selfcheck_chash.py
cd go && go run ketama.go chash.go main.go
```

## 机制

### 1. 两种环的构造差异

| | libketama | nginx chash |
| --- | --- | --- |
| 哈希 | md5 | crc32（表驱动，init `0xffffffff`，final 异或） |
| 每节点点数 | `floor(pct * 40 * n) * 4`，等权时 160 | `weight * 160` |
| 点的来源 | 一次 md5 的 16 字节切成 4 个 uint32 | `crc32(HOST \0 PORT PREV_HASH)`，`PREV_HASH` 链式 |
| 权重表达 | 靠 memory 占比改 `ks` | 直接乘在 160 上 |
| 排序后 | 不去重 | 相邻同 hash 去重 |
| 查找 | 「落在 `(前一点, 本点]`」 | 「第一个 `>= hash`」 |
| 越界 | `midp == numpoints` 或 `lowp > highp` → 返回第 0 个点 | 返回 `len(points)`，由调用方回绕 |

nginx 那句 `ngx_crc32_update(&base_hash, (u_char *) "", 1)` 是个容易读错的地方：
`""` 字面量是 1 字节的 `\0`，长度参数也是 1 —— **它真的往 base hash 里喂了一个 NUL 字节**，
不是空操作。自检里用「少了这个字节就与 `zlib.crc32(HOST\0PORT)` 对不上」做了成对反例。

### 2. 一个单精度陷阱

`ketama.c` 里 `pct` 是 `float`，`floorf()` 也是单精度：

```c
float pct = (float)slist[i].memory / (float)memory;
unsigned int ks = floorf( pct * 40.0 * (float)numservers );
```

等权节点时数学上 `ks` 应该恒为 40，但**浮点路径说了算**：

| 等权节点数 | C 的单精度路径 | 全程 double |
| --- | --- | --- |
| 3 / 6 / 11 | 40 | 40 |
| **7** | **40** | **39** |

也就是说，用 Python/Go 默认的 double 直接照抄公式，7 台等权节点会少 4 个点
（156 而非 160）。本 demo 用 `float32` 显式降级复刻 C 的行为，并把两种口径都做成了断言。

### 3. 扩容时要搬多少 key（本 demo 实测口径）

`python main.py` 用 10000 个 `/video/{i}.mp4` 键实测，从 n 台加到 n+1 台：

| 变更 | libketama | nginx chash | 理想值 `1/(n+1)` |
| --- | --- | --- | --- |
| 3 → 4 | 24.6% | 27.8% | 25.0% |
| 4 → 5 | 18.4% | 20.9% | 20.0% |
| 9 → 10 | 8.9% | 10.5% | 10.0% |

两点值得注意：
- 实测值在理想值上下浮动几个百分点，这是**有限样本 + 有限虚拟节点**的正常偏差，不是 100% 精确。
- 一致性哈希的价值是「搬 `1/(n+1)`」而不是「搬全部」；对比朴素取模 `hash % n` 的
  `n/(n+1)`（3→4 台要搬 75%），差距才是重点。

## 断言设计

- 95 条断言全部实跑通过（ketama 44 + chash 51），每条都**成对构造**。
- `ketama_hashi` 与 `struct.unpack("<I", md5[:4])` 对拍，验证移位方向；
  整个环的点值与独立重算的 `sorted(...)` 逐项比对。
- 二分查找用手算的小环 `[10:'a', 20:'b', 30:'c']` 覆盖 9 个 h 值，含「命中自身」
  「落在区间内」「超出最大点后回滚」三类边界。
- `find_chash_point` 同样手算 8 个 h 值，含精确命中与返回 `len` 的越界情形。
- `tries > 20` 回退：构造「全部节点不可用」，断言 `tries == 21`、`hash` 从 5 递增到 26、
  最终走 round-robin；成对用例断言「首选可用时 `tries == 0`」。
- 分布类断言用「每台至少 N 个 key」而不是精确比例，避免把随机性钉死成假断言。

## 注意事项 / 口径

- 本 demo 覆盖的是**环的构造与查找**。真实的 nginx `get_chash_peer` 还要处理
  `down` / `max_fails` / `current_weight` 与 `rrp.tried` 位图，这里只保留
  「找点 → 不可用则 `hash++` → `tries > 20` 回退 RR」的主干，未建模健康检查和重试位图。
- libketama 原版用共享内存 + `mcs continuum[numservers*160]` 定长数组；这里用动态数组，
  语义等价但不涉及 shm。
- ketama 的 `while (1)` 二分加了 4096 次迭代上限，仅为避免断言失败时脚本挂死；
  正常输入下二分是 `O(log n)`，远达不到。
- 第 3 节的重迁移比例是**本 demo 用 10000 个键实跑**得出的，不是官方文档数值；
  换一批 key 会有几个百分点的浮动。
- 本机无 Go 工具链，Go 侧以 `bracket_check` / `go_sanity` / `go_crossref` / `syntax_sanity` 静态通过为准。

## 参考资料

- https://github.com/RJ/ketama/blob/master/libketama/ketama.c
- https://github.com/nginx/nginx/blob/master/src/core/ngx_crc32.c
- https://github.com/nginx/nginx/blob/master/src/core/ngx_crc32.h
- https://github.com/nginx/nginx/blob/master/src/http/modules/ngx_http_upstream_hash_module.c
