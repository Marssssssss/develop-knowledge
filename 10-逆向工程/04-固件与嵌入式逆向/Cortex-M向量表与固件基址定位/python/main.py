"""demo594 演示入口：从裸镜像里把加载基址和入口点找回来。"""

import vecbase as vb


def main():
    base, ram_lo, ram_hi = 0x08000000, 0x20000000, 0x20010000
    img = vb.build_sample_image(base, n_ext=8, ram_lo=ram_lo, ram_hi=ram_hi,
                                n_code=16)
    vecs = vb.candidate_vectors(img, 0, 24)
    print("== 候选向量表（文件偏移 0，小端）==")
    for i, name in enumerate(vb.vector_names(18)):
        if i >= len(vecs):
            break
        print("  [%2d] %-26s %#010x" % (i, name, vecs[i]))

    got, score, off = vb.infer_base(img, ram_lo, ram_hi)
    print("== 推断结果 ==")
    print("  base=%#010x (真实 %#010x) score=%d table_off=%d"
          % (got, base, score, off))
    print("  initial SP=%#010x  落在 RAM 窗口内=%s"
          % (vecs[0], ram_lo <= vecs[0] <= ram_hi))
    print("  reset vector=%#010x -> entry=%#010x"
          % (vecs[1], vb.entry_point(vecs)))
    print("  VTOR 可指向该基址=%s（TBLOFF 从位 %d 起，下限 %d 字节对齐）"
          % (vb.vtor_relocatable(got), vb.SCB_VTOR_TBLOFF_POS, vb.MIN_VECTOR_ALIGN))


if __name__ == "__main__":
    main()
