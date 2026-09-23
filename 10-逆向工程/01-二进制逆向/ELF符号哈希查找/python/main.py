"""650 · ELF 符号哈希查找：DT_HASH 与 DT_GNU_HASH 对照演示。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import elf_hash as E  # noqa: E402


def banner(t):
    print("\n== %s ==" % t)


def main():
    syms = ["", "printf", "malloc", "free", "memcpy", "strlen", "my_export"]

    banner("1. 两套哈希函数")
    for n in ["printf", "malloc", "free"]:
        print("  %-8s sysv=0x%07x(%d)  gnu=0x%08x(%d)"
              % (n, E.dl_elf_hash(n), E.dl_elf_hash(n),
                 E.dl_new_hash(n), E.dl_new_hash(n)))

    banner("2. 建表（linker 口径：按 bucket 排序使 chain 连续）")
    t = E.GnuHashTable(syms, symbias=1, nbuckets=4, nwords=2, shift=6)
    print("  nbuckets=%d symbias=%d bitmask_nwords=%d shift=%d"
          % (t.nbuckets, t.symbias, t.nwords, t.shift))
    print("  buckets =", t.buckets)
    print("  符号表顺序 =", t.symbols)
    print("  chain_zero =", [hex(v) for v in t.chain_zero])
    print("  bitmask    =", [hex(v) for v in t.bitmask])

    banner("3. 查 printf（逐步）")
    idx, tr = t.lookup("printf", trace=True)
    for step in tr:
        print("   ", step)
    print("  => symidx =", idx, "->", t.symbols[idx] if idx else None)

    banner("4. bloom 是「放行/拦截」而非判定：存在假阳性")
    big = E.GnuHashTable([""] + ["sym%d" % i for i in range(60)],
                         symbias=1, nbuckets=8, nwords=2, shift=6)
    for probe in ["q0", "nope", "sym7"]:
        h = E.dl_new_hash(probe)
        hit, widx, b1, b2 = big.bloom_probe(h)
        print("  %-5s h=0x%08x word=%d bits=(%d,%d) bloom=%s lookup=%s"
              % (probe, h, widx, b1, b2, hit, big.lookup(probe)))
    print("  q0 的 bloom 放行但 chain 落空 ⇒ 必须靠 chain 的名字比较收尾")

    banner("5. SysV DT_HASH 的链表形态")
    s = E.SysvHashTable(syms, symbias=1, nbuckets=4)
    print("  buckets =", s.buckets)
    print("  chain   =", s.chain)
    print("  free -> symidx", s.lookup("free"))
    print("  (STN_UNDEF==0 同时是空桶与链表尾的哨兵，"
          "所以符号 0 永远查不到)")

    banner("6. _dl_setup_hash 的解析口径")
    p = E.parse_gnu_hash(t.words32())
    print("  idxbits = nwords-1 =", p["idxbits"])
    print("  chain_zero 长度 =", len(p["chain_zero"]),
          "（= 符号数，因为 l_gnu_chain_zero = hash32 - symbias）")
    print("  symidx = hasharr - chain_zero =", t.symidx_from_hasharr(t.buckets[0], 0))


if __name__ == "__main__":
    main()
