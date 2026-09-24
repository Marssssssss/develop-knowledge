"""Swift 参数包模型的自检(实跑)。

期望值逐条对照 SE-0393 / SE-0398 / SE-0399 / SE-0408 的原文手算过。
"""

from packs_model import (Each, Repeat, Node, PackError, captures, captured_type_packs,
                         ShapeSolver, infer_from_same_type_pack,
                         infer_from_pack_expansion, check_pack_expansion,
                         parse_generic_params, bind_generic_args, check_stored_property,
                         iterate_over_pack, expand_all, abstract_tuple_example,
                         infer_requirements)

_P = [0]
_F = []


def ok(c, m):
    if c:
        _P[0] += 1
    else:
        _F.append(m)
        print("FAIL:", m)


def eq(a, b, m):
    ok(a == b, "%s (got %r want %r)" % (m, a, b))


def raises(fn, m):
    try:
        fn()
    except PackError:
        _P[0] += 1
        return
    ok(False, "%s (no raise)" % m)


# ---------- 捕获(SE-0393) ----------
# repeat foo(each x, (each T).self, (repeat each y))
pat = Repeat(Node("foo", Each("x"), Node("self", Each("T")), Repeat(Each("y"))))
eq(captures(pat), {"x", "T"}, "外层 repeat 捕获 x 与 T,不捕获被内层 repeat 捕获的 y")
inner = pat.parts[0].parts[2]
eq(captures(inner), {"y"}, "内层 repeat 自己捕获 y")
eq(captures(Repeat(Each("v"))), {"v"}, "最简模式捕获一个包")
eq(captures(Repeat(Node("f", Each("a"), Each("b")))), {"a", "b"}, "多个包同时被捕获")
eq(captures(Repeat(Node("f", Repeat(Each("z"))))), set(),
   "模式里只有内层 repeat 时外层一个都不捕获")

# 值包的类型里顺带捕获的类型包:x: Foo<U, (repeat each V)> -> 捕获 U 不捕获 V
tp = Node("Foo", Each("U"), Repeat(Each("V")))
eq(captured_type_packs(tp), {"U"}, "类型里的 repeat 同样不穿透")

# ---------- 同形状推断(SE-0393) ----------
s = ShapeSolver(["First", "Second", "S"])
infer_from_same_type_pack(s, {"First", "Second"}, {"S"})
eq(s.same_shape("First", "S"), True, "same-type-pack 让 First 与 S 同形状")
eq(s.same_shape("First", "Second"), True, "同一要求也合并 First 与 Second")
eq(s.same_shape("Second", "S"), True, "等价类是传递的")
eq(s.class_of("First"), ["First", "S", "Second"], "三个包落进同一个等价类")

s2 = ShapeSolver(["T", "U", "V"])
infer_from_pack_expansion(s2, {"T", "U"})
eq(s2.same_shape("T", "U"), True, "参数/返回类型处的包扩展会推断同形状")
eq(s2.same_shape("T", "V"), False, "未参与推断的 V 不同形状")
raises(lambda: check_pack_expansion(s2, {"T", "V"}, "local var"),
       "其它位置的包扩展若尚未同形状就报错")

s3 = ShapeSolver(["T", "U"])
check_pack_expansion(s3, {"T"}, "local var")
ok(True, "只捕获一个包时不触发同形状检查")
infer_from_pack_expansion(s3, {"T", "U"})
check_pack_expansion(s3, {"T", "U"}, "local var")
ok(True, "推断过之后同样的位置就合法了")

# 具体形状是 conflict
s4 = ShapeSolver(["Q", "R", "S"])
s4.impose_abstract("Q")
eq(s4.concrete, {}, "只施加抽象形状时不会产生具体长度")
s4.impose_concrete("Q", 2)
raises(lambda: s4.impose_concrete("Q", 3), "同一类被钉成两个不同长度是 conflict")
raises(lambda: s4.impose_abstract("Q"), "已钉具体长度后再加抽象约束是 conflict")
s5 = ShapeSolver(["Q", "R"])
s5.impose_concrete("Q", 2)
raises(lambda: s5.union("Q", "R") if False else s5.impose_concrete("R", 3) or s5.union("Q", "R"),
       "合并两个长度不同的具体形状是 conflict")

# ---------- 变长泛型类型(SE-0398) ----------
spec_tuv = [("scalar", "T"), ("pack", "U"), ("scalar", "V")]
r = bind_generic_args(spec_tuv, ["Int", "Float"])
eq(r, {"T": "Int", "U": [], "V": "Float"}, "两个实参时包被替成空包")
r = bind_generic_args(spec_tuv, ["Int", "Bool", "Float"])
eq(r["U"], ["Bool"], "三个实参时包吃到中间一个")
r = bind_generic_args(spec_tuv, ["Int", "Bool", "String", "Float"])
eq(r, {"T": "Int", "U": ["Bool", "String"], "V": "Float"},
   "四个实参时包吃到中间两个,后缀仍绑定最后一个")
raises(lambda: bind_generic_args(spec_tuv, ["Int"]), "实参少于非包形参个数时报错")
eq(bind_generic_args([("pack", "T")], []), {"T": []}, "只有一个包时可以用空实参列表")
eq(bind_generic_args([("pack", "T")], ["Int", "String"])["T"], ["Int", "String"],
   "包可以吃到任意多个")
raises(lambda: parse_generic_params([("pack", "T"), ("pack", "U")]),
       "一个泛型类型只能声明一个类型参数包")
eq(parse_generic_params([("scalar", "T"), ("pack", "U")]),
   [("scalar", "T"), ("pack", "U")], "一个包加一个标量是允许的")
eq(bind_generic_args([("scalar", "T"), ("pack", "U")], ["Int", "Bool", "String"]),
   {"T": "Int", "U": ["Bool", "String"]}, "包在末尾时吃掉尾部所有实参")
eq(bind_generic_args([("pack", "T"), ("scalar", "V")], ["Int", "Bool", "Float"]),
   {"T": ["Int", "Bool"], "V": "Float"}, "包在开头时只吃前缀之后的部分")

# ---------- 实存属性(SE-0398) ----------
raises(lambda: check_stored_property(Repeat(Each("T"))),
       "实存属性的类型自身不能是包扩展类型")
eq(check_stored_property(Node("tuple", Repeat(Each("T")))), True,
   "包扩展嵌在元组里就可以")
eq(check_stored_property(Node("func", Repeat(Each("T")))), True,
   "嵌在函数类型里也可以")
eq(check_stored_property(Node("Other", Repeat(Each("T")))), True,
   "嵌在别的变长具名类型里也可以")

# ---------- 包遍历的惰性求值(SE-0408) ----------
values = [1, "hello", True]
events = iterate_over_pack(values, lambda i: "Evaluated %r" % values[i], break_at=1)
eq([e for k, e in events if k == "eval"],
   ["Evaluated 1", "Evaluated 'hello'"], "for-in 里 pattern 每次迭代才求值,break 后不再求值")
eq([i for k, i in events if k == "iter"], [0, 1], "只走了两次迭代")
eq([e for k, e in expand_all(values, lambda i: "Evaluated %r" % values[i])],
   ["Evaluated 1", "Evaluated 'hello'", "Evaluated True"],
   "repeat 表达式会把三份模式一次性全部求值")
eq(len(iterate_over_pack(values, lambda i: i)), 6, "不 break 时三次迭代各产生两个事件")
eq(len(iterate_over_pack([], lambda i: i)), 0, "空包一次都不迭代")

# ---------- 抽象元组(SE-0399) ----------
out = abstract_tuple_example([1, 2, 3], (4, 5, 6))
eq(out["each value"], (1, 2, 3), "repeat each value 展开值包")
eq(out["each tuple"], (4, 5, 6), "repeat each tuple 展开抽象元组里的包")
eq(out["value + tuple"], ((1, (4, 5, 6)), (2, (4, 5, 6)), (3, (4, 5, 6))),
   "不带 each 的元组是整体参与每一轮,不展开")
eq(out["value + each tuple"], ((1, 4), (2, 5), (3, 6)),
   "带 each 的抽象元组按位与值包配对")
eq(abstract_tuple_example([], ())["each value"], (), "空包与空元组都得到空结果")

# ---------- 要求推断(SE-0398) ----------
# demonstrate1: repeat ImposeRequirement<each U>   -> 推断 'repeat each U: P'
eq(infer_requirements("scalar", [("pack", "U")]), [("expansion", ("U",))],
   "规则 1:标量要求施加在包元素上,推断成要求扩展")
eq(infer_requirements("scalar", [("concrete", "Int")]), [("scalar", "Int")],
   "规则 1:施加在具体类型上仍是一条标量要求")
# demonstrate2: ImposeRepeatedRequirement<Int, V, repeat each U> -> Int: P, V: P, repeat each U: P
eq(infer_requirements("expansion", [("concrete", "Int"), ("concrete", "V"),
                                    ("pack", "U")]),
   [("scalar", "Int"), ("scalar", "V"), ("expansion", ("U",))],
   "规则 2:要求扩展对每个具体实参各展开一条")
# demonstrate3a: repeat ImposeRepeatedSameType<each U, repeat each V> -> 非法
raises(lambda: infer_requirements("expansion", [("pack", "U"), ("pack", "V")],
                                  pack_depths={"U": 1, "V": 2}),
       "规则 3a:多个包被不同深度的扩展捕获时非法")
# demonstrate3b: repeat (each U, ImposeRepeatedRequirement<Int, repeat each V>)
eq(infer_requirements("expansion", [("concrete", "Int"), ("pack", "V")],
                      pack_depths={"V": 2}),
   [("scalar", "Int"), ("expansion", ("V",))],
   "规则 3b:只有一个包时等价于最内层的那条要求扩展")
eq(infer_requirements("expansion", [("pack", "U"), ("pack", "V")],
                      pack_depths={"U": 2, "V": 2}),
   [("expansion", ("U",)), ("expansion", ("V",))],
   "规则 3b:同深度的两个包是允许的")

print("assertions passed: %d, failed: %d" % (_P[0], len(_F)))
if _F:
    raise SystemExit(1)
