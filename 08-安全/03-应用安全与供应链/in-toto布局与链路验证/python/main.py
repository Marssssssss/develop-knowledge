"""in-toto 演示：一条完整的供应链布局，以及三种典型的"规则写错"后果。"""

from intoto import Inspection, Layout, Link, Step, link_filename, verify
from rules import verify_expected

H = lambda c: c * 64  # noqa: E731  简化的哈希值


def build_layout():
    """经典的 fetch -> build -> package 三步。"""
    steps = [
        Step("fetch", pubkeys=["alice"],
             expected_products=["CREATE src/*", "DISALLOW *"]),
        Step("build", pubkeys=["bob"],
             expected_materials=["MATCH src/* WITH PRODUCTS FROM fetch", "DISALLOW *"],
             expected_products=["CREATE obj/*", "DISALLOW *"]),
        Step("package", pubkeys=["carol", "dave"], threshold=2,
             expected_materials=["MATCH obj/* WITH PRODUCTS FROM build", "DISALLOW *"],
             expected_products=["CREATE dist/app.tar.gz", "DISALLOW *"]),
    ]
    return Layout(steps, inspections=[
        Inspection("untar", expected_materials=["MATCH dist/app.tar.gz WITH PRODUCTS FROM package",
                                                "DISALLOW *"])])


def good_links():
    return {
        "fetch": [Link("fetch", {}, {"src/main.c": H("a")}, signer="alice")],
        "build": [Link("build", {"src/main.c": H("a")}, {"obj/main.o": H("b")}, signer="bob")],
        "package": [
            Link("package", {"obj/main.o": H("b")}, {"dist/app.tar.gz": H("c")}, signer="carol"),
            Link("package", {"obj/main.o": H("b")}, {"dist/app.tar.gz": H("c")}, signer="dave"),
        ],
        "untar": [Link("untar", {"dist/app.tar.gz": H("c")}, {}, signer="alice")],
    }


def demo():
    print("in-toto 布局验证：规则写对与写错的差别")
    print()
    print("1) 一条完整供应链（package 步骤阈值 2，需要两人结果一致）")
    res = verify(build_layout(), good_links())
    print("   通过: %s  告警: %d" % (res.ok(), len(res.warnings)))
    for e in res.errors:
        print("   ERROR %s" % e)

    print()
    print("2) 中间产物被偷偷换掉（build 拿着 a，package 声称拿到的是 z）")
    links = good_links()
    links["package"] = [
        Link("package", {"obj/main.o": H("z")}, {"dist/app.tar.gz": H("c")}, signer="carol"),
        Link("package", {"obj/main.o": H("z")}, {"dist/app.tar.gz": H("c")}, signer="dave"),
    ]
    res = verify(build_layout(), links)
    print("   通过: %s" % res.ok())
    for e in res.errors:
        print("   ERROR %s" % e)

    print()
    print("3) 阈值 2 但两人报告不一致")
    links = good_links()
    links["package"] = [
        Link("package", {"obj/main.o": H("b")}, {"dist/app.tar.gz": H("c")}, signer="carol"),
        Link("package", {"obj/main.o": H("b")}, {"dist/app.tar.gz": H("d")}, signer="dave"),
    ]
    res = verify(build_layout(), links)
    print("   通过: %s" % res.ok())
    for e in res.errors:
        print("   ERROR %s" % e)

    print()
    print("4) 忘记在规则列表末尾写 DISALLOW * 的后果")
    link = Link("s", {"src/main.c": H("a"), "src/backdoor.c": H("x")}, {})
    for rules in (["MATCH src/main.c WITH PRODUCTS FROM fetch"],
                  ["MATCH src/main.c WITH PRODUCTS FROM fetch", "DISALLOW *"]):
        passed, err, left = verify_expected(rules, link.materials, "materials", link,
                                            {"fetch": Link("fetch", {}, {"src/main.c": H("a")})})
        print("   规则 %-52s -> 通过=%s 剩余=%s"
              % (" + ".join(rules), passed, sorted(left)))

    print()
    print("5) 规则顺序：ALLOW * 写前面会把后面的 DISALLOW 架空")
    link = Link("s", {"bad.c": H("x")}, {})
    for rules in (["ALLOW *", "DISALLOW *.c"], ["DISALLOW *.c", "ALLOW *"]):
        passed, err, left = verify_expected(rules, link.materials, "materials", link, {})
        print("   %-24s -> 通过=%s %s" % (" + ".join(rules), passed, err or ""))

    print()
    print("6) link 文件名（规范 4.4：keyid 前六字节）")
    print("   %s" % link_filename("package", "0123456789abcdef0123"))


if __name__ == "__main__":
    demo()
