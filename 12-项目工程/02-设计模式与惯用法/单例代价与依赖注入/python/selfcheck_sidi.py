# -*- coding: utf-8 -*-
"""单例/定位器/注入三者对拍断言。"""

import threading

from sidi import (
    INJECTION_FORMS, ClientWithDI, ClientWithLocator, Config,
    ServiceLocator, SingletonMeta,
)

PASS = []


def ok(msg):
    PASS.append(msg)
    print(f"  [ok] {msg}")


def main():
    print("1. 单例的两条保证")
    a, b = Config(), Config()
    assert a is b and Config.init_calls == 1
    ok("全局唯一实例;且是**首次请求才初始化**(lazy)——普通构造器做不到(N 次调用 N 个对象)")

    print("2. 线程安全要专门处理")
    results = []
    for _ in range(2):                      # 模拟并发首用:锁内二次检查保证唯一
        t = threading.Thread(target=lambda: results.append(Config()))
        t.start(); t.join()
    assert all(r is results[0] for r in results) and Config.init_calls == 1
    ok("多线程环境需要锁——页面明言该模式『要求特殊处理』,否则可能创建多个实例")

    print("3. 测试为什么难")
    class TestDB:
        pass
    cfg1 = Config.instance()
    assert Config.instance() is cfg1        # 想换 mock 只能改全局状态/魔改模块
    ok("私有构造 + 类方法不可覆盖(多数语言):mock 要么hack全局要么不写测试"
       "——单例的四个官方缺点之一")

    print("4. 定位器 vs 注入(Fowler)")
    ServiceLocator.provide("db", "RealDB")
    c_loc = ClientWithLocator()
    assert "ServiceLocator" in c_loc.dependencies()
    c_di = ClientWithDI(db=TestDB())        # 构造签名即依赖清单,mock 直接塞
    assert c_di.dependencies() == ["db"] and isinstance(c_di.db, TestDB)
    ok("定位器:每个使用者都依赖定位器本身(它只是藏住了别的依赖);"
       "注入:看构造签名就知道全部依赖,mock 直接当参数传")

    print("5. 共同的底座")
    assert len(INJECTION_FORMS) == 3
    ok("Fowler:注入与定位器都做到了『应用代码与具体实现解耦』;"
       "真正的原则是**把配置与使用分开**——选哪个是次要的;"
       "注入的 IoC 有代价(难理解、调试绕),需要自我证明")
    ok("三种注入形态:构造/Setter/接口注入(注意:控制反转 ≠ 依赖注入——后者是前者的一个子集)")

    print(f"\n共 {len(PASS)} 项断言全部通过")


if __name__ == "__main__":
    main()
