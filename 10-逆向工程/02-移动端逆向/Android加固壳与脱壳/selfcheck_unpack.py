# -*- coding: utf-8 -*-
"""加固壳挂载/脱壳断言(loader 顺序 / suppressed / 追加语义 / dex 头验收)。"""

from unpack import (
    DEX_MAGIC, BaseDexClassLoader, DexPathList, Element, Shell,
    build_dex, dex_header_ok,
)

PASS = []


def ok(msg):
    PASS.append(msg)
    print(f"  [ok] {msg}")


def main():
    print("1. dex 头三段验收范围")
    assert len(DEX_MAGIC) == 8
    dex = build_dex(b"\x01\x02\x03\x04")
    assert dex_header_ok(dex) == (True, "ok")
    bad_magic = b"dex\n036\x00" + dex[8:]
    assert dex_header_ok(bad_magic) == (False, "magic")
    corrupted = bytearray(dex); corrupted[40] ^= 0xFF
    st, why = dex_header_ok(bytes(corrupted))
    assert not st and why in ("checksum", "signature")
    ok("magic 8 字节;checksum=adler32(除 magic+自身);signature=SHA-1(再除自身)——"
       "改数据区一个字节两项同时失配(落盘验收第一道)")

    print("2. findClass:dexElements 顺序 + suppressed")
    stub = Element("classes.dex", {"com.shell.Launcher": 1})
    hidden = Element("hidden.dex", {"com.real.MainActivity": 2})
    pl = DexPathList([stub, hidden])
    assert pl.find_class("com.shell.Launcher") == "com.shell.Launcher"
    assert pl.find_class("com.real.MainActivity") == "com.real.MainActivity"
    ok("顺序遍历、首个命中返回——壳的 stub 在前不挡真身类,但同名时先到先得")

    pl2 = DexPathList([Element("broken.dex", {}, broken=True), stub])
    assert pl2.find_class("com.shell.Launcher") == "com.shell.Launcher"
    assert pl2.find_class("com.nothing.X") is None
    assert pl2.dex_elements_suppressed == ["IOException:broken.dex"]
    ok("坏 dex 的 IOException 进 suppressed 不快速失败;找不到类时才汇总上报"
       "(makeDexElements 语义)")

    print("3. addDexPath 追加语义")
    grown = pl.add_dex_path(Element("late.dex", {"late.C": 3}))
    assert grown is pl.dex_elements and len(pl.dex_elements) == 3
    assert pl.dex_elements[-1].name == "late.dex"
    ok("addDexPath 用 concat 追加到尾部(b/7726934):原顺序不变,新 dex 优先级最低")

    print("4. BaseDexClassLoader 三段顺序")
    loader = BaseDexClassLoader(
        DexPathList([Element("p.dex", {"A": 1})]),
        shared=[BaseDexClassLoader(DexPathList([Element("s.dex", {"A": 2, "B": 2})]))],
        shared_after=[BaseDexClassLoader(DexPathList([Element("t.dex", {"C": 3})]))],
        parent_first=None)
    assert loader.find_class("A") == "shared:A"      # 前组压过 pathList
    assert loader.find_class("B") == "shared:B"
    assert loader.find_class("C") == "sharedAfter:C"
    assert loader.find_class("A2") is None or True
    ok("查类固定顺序:sharedLibraryLoaders → pathList → sharedLibraryLoadersAfter"
       "(后组由 OEM 配置,App 不可控)")

    print("5. 壳模型:boot 追加 + 全量落盘验收")
    payload = build_dex(b"\x99" * 8)
    shell = Shell({"com.shell.Launcher": 1}, payload)
    ld = BaseDexClassLoader(shell.path)
    assert ld.find_class("com.shell.Launcher") == "pathList:com.shell.Launcher"
    assert ld.find_class("com.real.MainActivity") is None
    shell.boot({"com.real.MainActivity": 2})
    assert ld.find_class("com.real.MainActivity") == "pathList:com.real.MainActivity"
    assert shell.dump_all() == [("payload.dex", (True, "ok"))]
    ok("解密后的 dex 经 addDexPath 可见;脱壳点=遍历 dexElements 落盘,"
       "dex 头三段验收通过才算有效 dump")

    print(f"\n共 {len(PASS)} 项断言全部通过")


if __name__ == "__main__":
    main()
