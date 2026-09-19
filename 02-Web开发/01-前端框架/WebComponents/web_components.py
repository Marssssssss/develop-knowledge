# -*- coding: utf-8 -*-
"""
Custom Elements 最小实现：升级（upgrade）+ custom element reactions 队列。

权威依据：WHATWG HTML「4.13 Custom elements」
  https://html.spec.whatwg.org/multipage/custom-elements.html
  - §4.13.5 Upgrades：upgrade 只作用于 state 为 "undefined"/"uncustomized" 的元素；
    未插入文档的元素不会被自动升级（"upgrades only apply to elements in the document"）。
  - §4.13.2/§4.13.6：构造函数里**不得**检查 attributes/children —— non-upgrade 情形下它们
    根本不存在；也不得新增 attributes/children。工作应尽量推迟到 connectedCallback，
    "note that connectedCallback can be called more than once"。
  - reaction 队列：connectedCallback 可以在元素已 disconnected 之后仍被调用，
    此时回调里 `isConnected` 为 false（规范给了这段示例输出）。
  - §4.13.2.1 移动元素：默认会依次调用 disconnectedCallback 与 connectedCallback；
    定义了 connectedMoveCallback 则取代默认行为。
  - CustomElementRegistry.upgrade(root)：手动升级尚不在文档中的元素。
  - attributeChangedCallback 只对 observedAttributes 里的属性触发。
"""

UPGRADE_STATES = ("undefined", "uncustomized")


class Element:
    def __init__(self, local_name, impl_factory=None):
        self.local_name = local_name
        self.state = "undefined"          # undefined/uncustomized/custom/failed
        self.is_connected = False
        self.attributes = {}
        self.children = []
        self.impl = None
        self.log = []
        self.factory = impl_factory
        self.doc = None

    # 生命周期回调（由 impl 可选实现）
    def _fire(self, name, *args):
        self.log.append(name)
        fn = getattr(self.impl, name, None)
        if fn is not None:
            fn(*args)


class CustomElementRegistry:
    def __init__(self):
        self.definitions = {}             # localName -> 可调用（构造函数）
        self.queue = []                   # custom element reaction queue
        self.processing = False

    # ---------- 队列 ----------
    def enqueue(self, reaction):
        self.queue.append(reaction)

    def process(self):
        """处理 reaction 队列；处理期间新入队的 reaction 追加到同一轮尾部。"""
        if self.processing:
            return
        self.processing = True
        while self.queue:
            fn = self.queue.pop(0)
            fn()
        self.processing = False

    def define(self, name, ctor):
        self.definitions[name] = ctor
        # 已定义后，文档中处于 undefined 状态的元素会被升级
        for el in list(getattr(self, "_elements", [])):
            if el.local_name == name and el.state in UPGRADE_STATES and el.is_connected:
                self.enqueue(lambda e=el: self.upgrade_element(e))
        return self

    def track(self, elements):
        self._elements = list(elements)
        return self

    # ---------- 升级 ----------
    def upgrade_element(self, el):
        ctor = self.definitions.get(el.local_name)
        if ctor is None:
            return False
        if el.state not in UPGRADE_STATES:
            return False                  # 已经升级过 / 已失败：幂等
        saved_attrs, saved_children = el.attributes, el.children
        # 构造期间 attributes/children 对元素不可见（规范强制）
        el.attributes, el.children = {}, []
        try:
            el.impl = ctor(el)
            el.state = "custom"
        except Exception:
            el.state = "failed"
            el.attributes, el.children = saved_attrs, saved_children
            return False
        el.attributes, el.children = saved_attrs, saved_children
        el._fire("constructor")
        if el.is_connected:
            # 升级一个已连接的元素时，connectedCallback 紧跟在构造函数之后
            self.enqueue(lambda: el._fire("connectedCallback"))
        return True

    def upgrade(self, root=None):
        """registry.upgrade(root)：手动升级（不要求元素已在文档中）。"""
        targets = [root] if root is not None else list(getattr(self, "_elements", []))
        done = 0
        for el in targets:
            if el.local_name in self.definitions and el.state in UPGRADE_STATES:
                self.enqueue(lambda e=el: self.upgrade_element(e))
                done += 1
        return done

    # ---------- 树操作 ----------
    def connect(self, el):
        el.is_connected = True
        if el.local_name in self.definitions and el.state in UPGRADE_STATES:
            # 升级时由 upgrade_element 负责补一次 connectedCallback
            self.enqueue(lambda: self.upgrade_element(el))
        elif el.state == "custom":
            # 已经是自定义元素（例如断开后重连）→ 第二次 connectedCallback
            self.enqueue(lambda: el._fire("connectedCallback"))
        # 未定义的名字（普通 <div>）没有任何 custom element reaction

    def disconnect(self, el):
        el.is_connected = False
        if el.state == "custom":
            self.enqueue(lambda: el._fire("disconnectedCallback"))

    def move(self, el):
        """移动元素：默认 disconnected + connected，除非实现了 connectedMoveCallback。"""
        if hasattr(el.impl or object(), "connectedMoveCallback"):
            self.enqueue(lambda: el._fire("connectedMoveCallback"))
        else:
            self.enqueue(lambda: el._fire("disconnectedCallback"))
            self.enqueue(lambda: el._fire("connectedCallback"))

    def set_attribute(self, el, name, value):
        old = el.attributes.get(name)
        el.attributes[name] = value
        if el.state == "custom" and name in getattr(el.impl, "observedAttributes", []):
            self.enqueue(lambda: el._fire("attributeChangedCallback", name, old, value))
        return old

    def parse_children(self, el, children):
        """模拟解析器填充子节点（构造之后才发生）。"""
        el.children = list(children)
