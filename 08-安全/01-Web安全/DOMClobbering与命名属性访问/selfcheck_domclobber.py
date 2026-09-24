"""DOMClobbering 自检。

依据 https://html.spec.whatwg.org/multipage/window-object.html §7.2.2.3 实读：
  - supported property names = navigable property set ∪ 四种标签的 name ∪ 所有 ID
  - property set 的两段循环：先按名去重（留第一个），再按同源过滤
  - 规范原文 spices 例子（hosted on https://example.org/）：
        <iframe src=https://elsewhere.example.com/></iframe>   ← 子文档把 window.name 设为 "spices"
        <iframe name=spices></iframe>
    求值 window.spices 得到 undefined
  - 取值优先级：navigable > 单元素 > HTMLCollection
"""

from domclobber import (Element, Navigable, Window, determine_value,
                        named_objects, supported_property_names,
                        target_name_property_set, window_get)

OK = 0
FAIL = []


def ck(name, cond, detail=""):
    global OK
    if cond:
        OK += 1
    else:
        FAIL.append("%s %s" % (name, detail))


def eq(name, got, want):
    ck(name, got == want, "got=%r want=%r" % (got, want))


ORG = "example.org"
ELSE = "elsewhere.example.com"


def W(*objs):
    navs = [o for o in objs if isinstance(o, Navigable)]
    els = [o for o in objs if isinstance(o, Element)]
    return Window(ORG, navs, els)


# ---------- 哪些标签的 name 参与 ----------
eq("div 的 id 参与", supported_property_names(W(Element(0, "div", id="x"))), ["x"])
eq("div 的 name 不参与",
   supported_property_names(W(Element(0, "div", name="x"))), [])
eq("img 的 name 参与",
   supported_property_names(W(Element(0, "img", name="x"))), ["x"])
eq("form 的 name 参与",
   supported_property_names(W(Element(0, "form", name="x"))), ["x"])
eq("object 的 name 参与",
   supported_property_names(W(Element(0, "object", name="x"))), ["x"])
eq("embed 的 name 参与",
   supported_property_names(W(Element(0, "embed", name="x"))), ["x"])
eq("a 的 name 不参与",
   supported_property_names(W(Element(0, "a", name="x"))), [])
eq("a 的 id 参与", supported_property_names(W(Element(0, "a", id="x"))), ["x"])
eq("空 name 不参与",
   supported_property_names(W(Element(0, "img", name=""))), [])
eq("空 id 不参与", supported_property_names(W(Element(0, "div", id=""))), [])

# ---------- tree order 与去重 ----------
eq("tree order 而非字典序",
   supported_property_names(W(Element(0, "div", id="b"), Element(1, "div", id="a"))),
   ["b", "a"])
eq("重复 ID 只算一次",
   supported_property_names(W(Element(0, "div", id="x"), Element(1, "span", id="x"))),
   ["x"])
eq("id 与 name 同名只算一次",
   supported_property_names(W(Element(0, "img", id="x", name="x"))), ["x"])

# ---------- property set 的两段循环 ----------
w = W(Navigable(0, "spices", ELSE), Navigable(1, "spices", ORG))
eq("跨源 navigable 占掉重名后 property set 为空",
   target_name_property_set(w), [])
eq("于是 supported names 为空", supported_property_names(w), [])
eq("spices 求值为 undefined（规范原文例子）",
   window_get(w, "spices"), (None, None))

w = W(Navigable(0, "spices", ORG), Navigable(1, "spices", ELSE))
eq("同源的那个排在前 → property set 有值",
   target_name_property_set(w), ["spices"])
eq("named objects 不过滤同源（两个都在）",
   len(named_objects(w, "spices")), 2)
eq("取到 tree order 上第一个 navigable 的 WindowProxy",
   determine_value(w, "spices")[1].origin, ORG)

w = W(Navigable(0, "", ORG))
eq("空 target name 被跳过", target_name_property_set(w), [])
w = W(Navigable(0, "a", ORG), Navigable(1, "b", ELSE))
eq("只有同源的那个进入 property set", target_name_property_set(w), ["a"])

# ---------- 取值优先级 ----------
w = W(Element(0, "div", id="x"))
eq("单元素 → 元素", determine_value(w, "x")[0], "element")
w = W(Element(0, "div", id="x"), Element(1, "span", id="x"))
kind, payload = determine_value(w, "x")
eq("两元素 → HTMLCollection", kind, "collection")
eq("集合含两个对象", len(payload), 2)
w = W(Navigable(0, "x", ORG), Element(1, "div", id="x"))
eq("navigable 优先于元素", determine_value(w, "x")[0], "windowproxy")
w = W(Element(0, "div", id="x"), Navigable(1, "x", ORG))
kind, payload = determine_value(w, "x")
eq("元素在前 navigable 仍优先", kind, "windowproxy")
eq("取到的是那个 navigable", payload.origin, ORG)

# ---------- 经典 clobbering 场景 ----------
# 攻击者注入 <a id=config href=...> 覆盖应用读取的 window.config
w = W(Element(0, "a", id="config", attrs={"href": "javascript:alert(1)"}))
kind, payload = window_get(w, "config")
eq("注入的 a 元素成为 window.config", kind, "element")
eq("clobber 后的 href 可被读到",
   payload.attrs.get("href"), "javascript:alert(1)")
# 两个同名注入 → 集合，truthy 但 .href 取不到
w = W(Element(0, "a", id="config"), Element(1, "a", id="config"))
kind, payload = window_get(w, "config")
eq("两个同名注入得到集合", kind, "collection")
ck("集合不是单元素（故 x.href 之类属性取不到）",
   not hasattr(payload, "attrs"), type(payload).__name__)

# ---------- window_get 只认 supported names ----------
w = W(Element(0, "div", name="x"))
eq("未列入 supported names 即为 undefined", window_get(w, "x"), (None, None))
w = W(Element(0, "img", name="x"))
eq("列入 supported names 才有值", window_get(w, "x")[0], "element")

# ---------- 元素属性透传 ----------
el = Element(0, "form", id="f", attrs={"action": "/x"})
w = W(el)
kind, payload = window_get(w, "f")
eq("form 也可被 id 命中", kind, "element")
eq("form 的 action 可取", payload.attrs.get("action"), "/x")

print("OK =", OK)
if FAIL:
    print("FAILED =", len(FAIL))
    for f in FAIL:
        print("  -", f)
else:
    print("ALL OK")
