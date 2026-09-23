"""nginx upstream consistent hash 的可执行转写。

对应源码（nginx/nginx@master）：
  - src/core/ngx_crc32.c:34   crc32 表（标准反射 CRC-32，poly 0xEDB88320）
  - src/core/ngx_crc32.h:54-74 ngx_crc32_init / _update / _final
  - src/http/modules/ngx_http_upstream_hash_module.c:337  ngx_http_upstream_update_chash
  - 同上 :490  ngx_http_upstream_find_chash_point
  - 同上 :585,686  tries > 20 回退到 round-robin

与 C 的差异：
  - `ngx_crc32_update(&base_hash, (u_char *) "", 1)` 传的是 "" 字面量、长度 1，
    即**一个 NUL 字节**（不是空串）。这里显式写成 b"\\x00"。
  - 真实的 get_chash_peer 还要过滤 down / max_fails / weight 等；本 demo 只保留
    「按 hash 找点 → 不可用则 hash++ 重试 → tries > 20 回退 RR」这条主干。
"""

CRC32_INIT = 0xFFFFFFFF


def _build_crc32_table():
    table = []
    for n in range(256):
        c = n
        for _ in range(8):
            c = (0xEDB88320 ^ (c >> 1)) if (c & 1) else (c >> 1)
        table.append(c & 0xFFFFFFFF)
    return table


CRC32_TABLE = _build_crc32_table()


def ngx_crc32_init():
    return CRC32_INIT


def ngx_crc32_update(crc, data):
    c = crc & 0xFFFFFFFF
    for b in data:
        c = (CRC32_TABLE[(c ^ b) & 0xFF] ^ (c >> 8)) & 0xFFFFFFFF
    return c


def ngx_crc32_final(crc):
    return (crc ^ 0xFFFFFFFF) & 0xFFFFFFFF


def ngx_crc32(data):
    return ngx_crc32_final(ngx_crc32_update(ngx_crc32_init(), data))


def split_host_port(server):
    """复刻 ngx_http_upstream_update_chash 里的 host/port 切分。

    规则：`unix:` 前缀剥掉且无 port；否则**从字符串尾部**往前扫，
    遇到 ':' 就切开，遇到非数字字符就放弃（整串当 host）。
    """
    if len(server) >= 5 and server[:5].lower() == "unix:":
        return server[5:], ""
    j = 0
    while j < len(server):
        c = server[len(server) - j - 1]
        if c == ":":
            return server[: len(server) - j - 1], server[len(server) - j:]
        if c < "0" or c > "9":
            break
        j += 1
    return server, ""


def build_chash_points(servers, dedup=True):
    """servers: [(server_name, weight), ...]；返回去重并排序后的 [(hash, server)]。

    `dedup=False` 时保留全部 `total_weight * 160` 个点，供断言核对原始点数。

    每个 peer 生成 `weight * 160` 个点，hash = crc32(HOST \\0 PORT PREV_HASH)，
    PREV_HASH 初始为 0、之后链式取上一个点的 hash。
    """
    points = []
    for name, weight in servers:
        host, port = split_host_port(name)
        base = ngx_crc32_init()
        base = ngx_crc32_update(base, host.encode())
        base = ngx_crc32_update(base, b"\x00")
        base = ngx_crc32_update(base, port.encode())

        prev = 0
        for _ in range(weight * 160):
            h = ngx_crc32_update(base, prev.to_bytes(4, "little"))
            h = ngx_crc32_final(h)
            points.append((h, name))
            prev = h

    points.sort(key=lambda p: p[0])

    if not dedup:
        return points

    # 去重：保留每个 hash 的第一次出现（C 里是 i/j 双指针原地压缩）
    out = []
    for p in points:
        if not out or out[-1][0] != p[0]:
            out.append(p)
    return out


def find_chash_point(points, hash_):
    """ngx_http_upstream_find_chash_point：找第一个 point >= hash 的下标。

    返回值可能等于 len(points)，调用方需自行回绕到 0。
    """
    i, j = 0, len(points)
    while i < j:
        k = (i + j) // 2
        if hash_ > points[k][0]:
            i = k + 1
        elif hash_ < points[k][0]:
            j = k
        else:
            return k
    return i


class ChashPeer:
    """模拟一次 get_chash_peer 的迭代状态。"""

    def __init__(self, points, hash_, unavailable=None, rr_index=0):
        self.points = points
        self.hash = hash_ & 0xFFFFFFFF
        self.tries = 0
        self.unavailable = unavailable or set()
        self.rr_index = rr_index
        self.fell_back_to_rr = False
        self.chash_index = None


def get_chash_peer(state):
    """返回选中的 server 名；tries > 20 时回退到 round-robin。"""
    peers = state.points
    if not peers:
        return None
    while True:
        i = find_chash_point(peers, state.hash)
        if i == len(peers):
            i = 0
        state.chash_index = i
        server = peers[i][1]

        if server not in state.unavailable:
            return server

        state.hash = (state.hash + 1) & 0xFFFFFFFF
        state.tries += 1
        if state.tries > 20:
            state.fell_back_to_rr = True
            order = sorted({p[1] for p in peers})
            picked = order[state.rr_index % len(order)]
            state.rr_index += 1
            return picked


def chash_lookup(points, key):
    """按 key 的 crc32 值选节点（key 用 ngx_crc32 哈希，与 nginx 的 $hash 变量一致口径）。"""
    if not points:
        return None
    h = ngx_crc32(key.encode())
    i = find_chash_point(points, h)
    return points[0][1] if i == len(points) else points[i][1]
