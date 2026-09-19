# -*- coding: utf-8 -*-
"""web_components.py 自检：逐条对应 WHATWG custom elements 规范条文。"""
from web_components import Element, CustomElementRegistry

ok = 0
fails = []


def check(label, cond, detail=""):
    global ok
    if cond:
        ok += 1
    else:
        fails.append("%s %s" % (label, detail))


class Widget:
    observedAttributes = ["title"]

    def __init__(self, el):
        self.el = el
        # 构造期间把能看到的东西拍下来，用于验证「构造时读不到 attributes/children」
        self.attrs_at_construct = dict(el.attributes)
        self.children_at_construct = list(el.children)
        self.is_connected_at_connect = []
        self.attr_changes = []
        self.moves = 0

    def connectedCallback(self):
        self.is_connected_at_connect.append(self.el.is_connected)

    def attributeChangedCallback(self, name, old, new):
        self.attr_changes.append((name, old, new))
    # 注意：基类**故意不**定义 connectedMoveCallback，
    # 否则 H1「未定义时走默认两段」这条负向判据的前提就不成立了。


# --- 1. 定义时升级已在文档中的元素 ---
reg = CustomElementRegistry()
el = Element("my-widget", Widget)
el.is_connected = True
reg.track([el])
check("A1 定义前是 undefined", el.state == "undefined", el.state)
reg.define("my-widget", Widget)
check("A2 定义时尚未升级（要等队列）", el.state == "undefined")
reg.process()
check("A3 升级完成", el.state == "custom", el.state)
check("A4 先构造再 connected", el.log == ["constructor", "connectedCallback"], el.log)

# --- 2. 不在文档中的元素不会被自动升级（规范：upgrades only apply to elements in the document） ---
reg2 = CustomElementRegistry()
detached = Element("my-widget", Widget)
reg2.track([detached])
reg2.define("my-widget", Widget)
reg2.process()
check("B1 游离元素不被自动升级", detached.state == "undefined", detached.state)
check("B2 也没有构造日志", detached.log == [], detached.log)
reg2.connect(detached)
reg2.process()
check("B3 插入文档后升级", detached.state == "custom" and detached.log == ["constructor", "connectedCallback"])

# --- 3. registry.upgrade(root) 手动升级（不要求在文档中） ---
reg3 = CustomElementRegistry()
forced = Element("my-widget", Widget)
reg3.define("my-widget", Widget)
n = reg3.upgrade(forced)
reg3.process()
check("C1 手动升级生效", forced.state == "custom", forced.state)
check("C2 返回值是被排入升级的元素数", n == 1, str(n))
check("C3 手动升级不触发 connectedCallback", forced.log == ["constructor"], forced.log)

# --- 4. connectedCallback 可以被调用多次 ---
reg4 = CustomElementRegistry()
e4 = Element("my-widget", Widget)
reg4.define("my-widget", Widget)
reg4.connect(e4)
reg4.process()
reg4.disconnect(e4)
reg4.process()
reg4.connect(e4)
reg4.process()
check("D1 disconnected 后再 connected 触发第二次", e4.log.count("connectedCallback") == 2, e4.log)
check("D2 disconnected 回调 1 次", e4.log.count("disconnectedCallback") == 1, e4.log)
check("D3 构造函数只跑一次", e4.log.count("constructor") == 1, e4.log)

# --- 5. 已入队的 connectedCallback 在元素断开后仍执行，且 isConnected 为 false ---
reg5 = CustomElementRegistry()
e5 = Element("my-widget", Widget)
reg5.define("my-widget", Widget)
reg5.connect(e5)
reg5.process()               # 先完成升级（元素此时是已连接的 custom element）
e5.log.clear()
e5.impl.is_connected_at_connect.clear()
reg5.connect(e5)             # 入队 connectedCallback
reg5.disconnect(e5)          # 队列还没处理就断开
reg5.process()
check("E1 仍调用了 connectedCallback", "connectedCallback" in e5.log, e5.log)
check("E2 回调里 isConnected 为 false", e5.impl.is_connected_at_connect == [False],
      e5.impl.is_connected_at_connect)

# --- 6. attributeChangedCallback 只覆盖 observedAttributes ---
reg6 = CustomElementRegistry()
e6 = Element("my-widget", Widget)
reg6.define("my-widget", Widget)
reg6.connect(e6)
reg6.process()
reg6.set_attribute(e6, "title", "hello")
reg6.process()
reg6.set_attribute(e6, "hidden", "1")     # 不在 observedAttributes
reg6.process()
check("F1 观察中的属性触发回调", e6.impl.attr_changes == [("title", None, "hello")], e6.impl.attr_changes)
check("F2 未观察的属性不触发", len(e6.impl.attr_changes) == 1)

# --- 7. 构造期间看不到 attributes/children（规范强制） ---
reg7 = CustomElementRegistry()
e7 = Element("my-widget", Widget)
e7.attributes = {"title": "set-by-parser"}
e7.children = ["<span>"]
reg7.define("my-widget", Widget)
reg7.connect(e7)
reg7.process()
check("G1 构造时 attributes 为空", e7.impl.attrs_at_construct == {}, e7.impl.attrs_at_construct)
check("G2 构造时 children 为空", e7.impl.children_at_construct == [])
check("G3 构造后属性回来了", e7.attributes == {"title": "set-by-parser"})

# --- 8. 移动：默认 disconnected+connected，有 connectedMoveCallback 则取代 ---
reg8 = CustomElementRegistry()
e8 = Element("my-widget", Widget)
reg8.define("my-widget", Widget)
reg8.connect(e8)
reg8.process()

class NoMove(Widget):
    def connectedMoveCallback(self):
        self.moves += 1

reg8b = CustomElementRegistry()
e8b = Element("my-widget", Widget)
reg8b.define("my-widget", Widget)
reg8b.connect(e8b)
reg8b.process()
e8b.log.clear()
reg8b.move(e8b)
reg8b.process()
check("H1 未定义 connectedMoveCallback 时走默认两段", e8b.log == ["disconnectedCallback", "connectedCallback"], e8b.log)

reg9 = CustomElementRegistry()
e9 = Element("my-widget", NoMove)
reg9.define("my-widget", NoMove)
reg9.connect(e9)
reg9.process()
e9.log.clear()
reg9.move(e9)
reg9.process()
check("H2 定义了 connectedMoveCallback 则只调它", e9.log == ["connectedMoveCallback"], e9.log)
check("H3 默认两段被取代（无 disconnected）", "disconnectedCallback" not in e9.log)

# --- 9. 升级幂等 ---
reg10 = CustomElementRegistry()
e10 = Element("my-widget", Widget)
reg10.define("my-widget", Widget)
reg10.connect(e10)
reg10.process()
again = reg10.upgrade_element(e10)
check("I1 已 custom 的元素再升级返回 False", again is False)
check("I2 构造函数仍只跑一次", e10.log.count("constructor") == 1, e10.log)

# --- 10. 未知元素名不会升级 ---
reg11 = CustomElementRegistry()
plain = Element("div")
reg11.define("my-widget", Widget)
reg11.connect(plain)
reg11.process()
check("J1 未定义名保持 undefined", plain.state == "undefined", plain.state)
check("J2 也未构造", plain.log == [], plain.log)

print("web_components: %d/%d assertions passed" % (ok, ok + len(fails)))
for f in fails:
    print("  FAIL", f)
raise SystemExit(1 if fails else 0)
