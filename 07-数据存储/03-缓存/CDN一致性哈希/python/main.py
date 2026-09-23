"""演示：两种一致性哈希实现的环结构、分布与扩缩容时的键重映射。"""

from ketama import ketama_create_continuum, ketama_get_server
from ngx_chash import build_chash_points, chash_lookup


def ketama_pick(ip, n):
    return ketama_create_continuum([(f"10.0.0.{i}", 1) for i in range(1, n + 1)])


def chash_ring(n):
    return build_chash_points([(f"10.0.0.{i}:80", 1) for i in range(1, n + 1)])


def distribution(picks):
    total = len(picks)
    return {s: round(100.0 * picks.count(s) / total, 1) for s in sorted(set(picks))}


def remap_ratio(n_old, n_new, keys=10000):
    """节点数从 n_old 变到 n_new 时，发生迁移的 key 比例（本 demo 实测口径）。"""
    ket_old = ketama_pick(None, n_old)
    ket_new = ketama_pick(None, n_new)
    ch_old = chash_ring(n_old)
    ch_new = chash_ring(n_new)

    k_move = c_move = 0
    for i in range(keys):
        key = f"/video/{i}.mp4"
        if ketama_get_server(key, ket_old).ip != ketama_get_server(key, ket_new).ip:
            k_move += 1
        if chash_lookup(ch_old, key) != chash_lookup(ch_new, key):
            c_move += 1
    return k_move / keys, c_move / keys


def main():
    n = 4
    print(f"== 1. 环规模（{n} 台等权节点） ==")
    ket = ketama_pick(None, n)
    ch = chash_ring(n)
    print(f"  libketama : {len(ket)} 个点（每节点 160，每 md5 取 4 段）")
    print(f"  nginx chash: {len(ch)} 个点（每节点 160，crc32 链式）")

    print("\n== 2. 1000 个 key 的分布（百分比） ==")
    keys = [f"/img/{i}.jpg" for i in range(1000)]
    print(f"  libketama : {distribution([ketama_get_server(k, ket).ip for k in keys])}")
    print(f"  nginx chash: {distribution([chash_lookup(ch, k) for k in keys])}")

    print("\n== 3. 扩容时的键重迁移比例（10000 个 key，本 demo 实测） ==")
    print("  理论上：从 n 台加到 n+1 台，只有约 1/(n+1) 的 key 需要换节点")
    for old, new in ((3, 4), (4, 5), (9, 10)):
        kr, cr = remap_ratio(old, new)
        print(f"  {old} → {new} 台：libketama {kr:.1%}   nginx chash {cr:.1%}"
              f"   （理想 {1.0 / new:.1%}）")

    print("\n== 4. 一致性检查：同一 key 在两种实现下的落点 ==")
    for k in ("/img/a.jpg", "/img/b.jpg", "/video/1.mp4"):
        print(f"  {k:16s} ketama → {ketama_get_server(k, ket).ip}    "
              f"chash → {chash_lookup(ch, k)}")


if __name__ == "__main__":
    main()
