"""DOM Clobbering 与 window 命名属性访问最小模型。

依据 HTML 规范 §7.2.2.3「Named access on the Window object」
（https://html.spec.whatwg.org/multipage/window-object.html ，817681 B 实读）：

**supported property names**（按贡献元素的 tree order，忽略后出现的重复）由三部分并集：
  1. window 的 *document-tree child navigable target name property set*
  2. 所有 embed / form / img / object 元素**非空** name 内容属性的值
     （注意：只有这四种标签的 name 参与，div/span/a 的 name 不参与）
  3. 所有带 ID 的元素的 ID（不限标签）

**document-tree child navigable target name property set** 是两段循环：
  第一段：按 tree order 取每个 navigable 的 target name；空串跳过；
          若集合里已有同名的则跳过（**只保留第一个**）。
  第二段：再筛一遍——只有 active document 与 window 同源的才把 name 记入结果。
  两段合起来的效果：跨源 iframe 会「占掉」重名，然后被第二段过滤掉，
  把同源的那个一起带走（规范原文 spices 例子）。

**named objects**（注意与 supported property names **不是**同一件事，不做同源过滤）：
  1. target name 等于 name 的 document-tree child navigables
  2. name 内容属性等于 name 的 embed/form/img/object 元素
  3. ID 等于 name 的元素

**取值算法**：
  a. 若 objects 含 navigable → 返回 tree order 上第一个「其 content navigable 在
     objects 里」的 navigable container 的 active WindowProxy
  b. 否则若 objects 只有一个元素 → 返回该元素
  c. 否则 → 返回 HTMLCollection

另注：规范写明 Window 带 [Global] 扩展属性，因此其命名属性遵循
*named properties object* 规则而非 legacy platform object 规则。
"""

NAME_ATTR_TAGS = {"embed", "form", "img", "object"}


class Navigable:
    """一个 document-tree child navigable（iframe 的内容）。"""

    def __init__(self, tree_index, target_name="", origin=""):
        self.tree_index = tree_index
        self.target_name = target_name   # 会被子文档的 window.name 改写
        self.origin = origin

    def __repr__(self):
        return "Navigable(name=%r, origin=%r)" % (self.target_name, self.origin)


class Element:
    """一个元素。tree_index 决定 document order。"""

    def __init__(self, tree_index, tag, id="", name="", attrs=None):
        self.tree_index = tree_index
        self.tag = tag.lower()
        self.id = id
        self.name = name
        self.attrs = dict(attrs or {})

    def contributes_name(self):
        return self.tag in NAME_ATTR_TAGS and self.name != ""

    def __repr__(self):
        return "<%s id=%r name=%r>" % (self.tag, self.id, self.name)


class Window:
    def __init__(self, origin, navigables=None, elements=None):
        self.origin = origin
        self.navigables = list(navigables or [])
        self.elements = list(elements or [])

    def _ordered(self):
        c = [(n.tree_index, n) for n in self.navigables]
        c += [(e.tree_index, e) for e in self.elements]
        c.sort(key=lambda t: t[0])
        return [obj for (_, obj) in c]


def target_name_property_set(win):
    """两段循环得到的 property set（同源过滤后）。"""
    first_named = []
    for nav in win.navigables:
        if nav.target_name == "":
            continue
        if any(n.target_name == nav.target_name for n in first_named):
            continue
        first_named.append(nav)
    names = []
    for nav in first_named:
        if nav.origin == win.origin:
            names.append(nav.target_name)
    return names


def supported_property_names(win):
    """tree order 并集，忽略后出现的重复。"""
    nav_names = target_name_property_set(win)
    out = []
    for obj in win._ordered():
        if isinstance(obj, Navigable):
            if obj.target_name in nav_names and obj.target_name not in out:
                out.append(obj.target_name)
        else:
            for candidate in _element_names(obj):
                if candidate not in out:
                    out.append(candidate)
    return out


def _element_names(el):
    names = []
    if el.contributes_name():
        names.append(el.name)
    if el.id:
        names.append(el.id)
    return names


def named_objects(win, name):
    """不做同源过滤的 named objects 列表。"""
    out = []
    for obj in win._ordered():
        if isinstance(obj, Navigable):
            if obj.target_name == name:
                _append_unique(out, obj)
        else:
            if el_matches(obj, name):
                _append_unique(out, obj)
    return out


def el_matches(el, name):
    return (el.tag in NAME_ATTR_TAGS and el.name == name) or el.id == name


def _append_unique(seq, obj):
    """同一对象只记一次。

    规范用三条列举给出 named objects，未明说 `<img id=x name=x>` 是否计两次；
    本 demo 按对象同一性去重，并在 README 标注该口径。
    """
    if not any(o is obj for o in seq):
        seq.append(obj)


def determine_value(win, name):
    """§7.2.2.3 的取值算法。返回 (种类, 载体)。"""
    objects = named_objects(win, name)
    if not objects:
        return (None, None)
    if any(isinstance(o, Navigable) for o in objects):
        for obj in win._ordered():
            if isinstance(obj, Navigable) and any(o is obj for o in objects):
                return ("windowproxy", obj)
    if len(objects) == 1:
        return ("element", objects[0])
    return ("collection", objects)


def window_get(win, name):
    """window[name] 的结果：未列入 supported property names 时是 undefined。"""
    if name not in supported_property_names(win):
        return (None, None)
    return determine_value(win, name)
