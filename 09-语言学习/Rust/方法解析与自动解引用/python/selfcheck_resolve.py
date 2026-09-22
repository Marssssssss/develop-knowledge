"""方法解析与自动解引用 —— 自检（全部断言对应 Reference method-call-expr 原文）。"""

from resolve import (
    Ambiguity, Method, MethodTable, Resolution, apply_call, candidate_receivers,
    candidate_types, disambiguate, receiver_type, resolve, std_env,
)

COUNT = [0]


def check(label, cond, detail=""):
    COUNT[0] += 1
    if not cond:
        raise AssertionError("%s FAILED %s" % (label, detail))


# ---------------------------------------------------------------- 1. 候选列表
env = std_env()
box_arr = candidate_receivers("Box<[i32;2]>", env)
official = ["Box<[i32;2]>", "&Box<[i32;2]>", "&mut Box<[i32;2]>",
            "[i32;2]", "&[i32;2]", "&mut [i32;2]",
            "[i32]", "&[i32]", "&mut [i32]"]
check("1.1 官方 Box<[i32;2]> 候选共 9 个", len(box_arr) == 9, str(len(box_arr)))
check("1.2 官方候选顺序逐项一致", box_arr == official, str(box_arr))
check("1.3 未定长强制转换只发生在最后一步",
      candidate_types("Box<[i32;2]>", env) == ["Box<[i32;2]>", "[i32;2]", "[i32]"],
      str(candidate_types("Box<[i32;2]>", env)))
check("1.4 &T 紧跟 T、&mut T 紧跟 &T", box_arr[1] == "&" + box_arr[0]
      and box_arr[2] == "&mut " + box_arr[0])
check("1.5 无 Deref/Unsize 时只有 3 个候选",
      candidate_receivers("i32", env) == ["i32", "&i32", "&mut i32"],
      str(candidate_receivers("i32", env)))
check("1.6 二级解引用链 Box<String>→String→str",
      candidate_types("Box<String>", env) == ["Box<String>", "String", "str"],
      str(candidate_types("Box<String>", env)))

# ------------------------------------------------- 2. 接收者形态 → 候选类型映射
check("2.1 self 的接收者类型是 T 本身", receiver_type("Foo", "self") == "Foo")
check("2.2 &self 的接收者类型是 &T", receiver_type("Foo", "&self") == "&Foo")
check("2.3 &mut self 的接收者类型是 &mut T",
      receiver_type("Foo", "&mut self") == "&mut Foo")

# ------------------------------------------- 3. 官方「出人意料」例子：trait 先赢
# Reference NOTE：因为 `&self` 方法先被查到，trait 方法排在 struct 的 &mut self 之前
def foo_bar_table(struct_kind):
    t = MethodTable()
    t.add("Foo", Method("bar", "inherent", struct_kind))
    t.add("Foo", Method("bar", "Bar", "&self"))
    return t


t1 = foo_bar_table("&mut self")
r1 = resolve("bar", "Foo", env, t1)
check("3.1 struct 是 &mut self 时命中 trait 版本", r1.method.owner == "Bar", str(r1))
check("3.2 命中位置是候选 #1(&Foo)，早于 #2(&mut Foo)", r1.index == 1, str(r1.index))

t2 = foo_bar_table("&self")
r2 = resolve("bar", "Foo", env, t2)
check("3.3 struct 改成 &self 后命中固有版本（成对对照）",
      r2.method.is_inherent, str(r2))
check("3.4 命中位置回到候选 #1，说明只差一个接收者形态", r2.index == 1, str(r2.index))

# ------------------------------------------------------ 4. 固有方法优先于 trait
t3 = MethodTable()
t3.add("Foo", Method("baz", "inherent", "&self"))
t3.add("Foo", Method("baz", "Baz", "&self"))
r3 = resolve("baz", "Foo", env, t3)
check("4.1 同一候选上固有方法压过 trait 方法", r3.method.is_inherent, str(r3))
check("4.2 固有/trait 同名不算歧义", not isinstance(r3, Ambiguity))

# ------------------------------------------------------------ 5. 自动解引用查找
t4 = MethodTable()
t4.add("str", Method("len", "inherent", "&self"))
r4 = resolve("len", "String", env, t4)
check("5.1 String 上没有 len，解引用后在 str 上找到", r4.candidate == "&str", str(r4))
check("5.2 跨了 3 个候选才命中（#4：String,&String,&mut String,str,&str）",
      r4.index == 4, str(r4.index))
r4b = resolve("len", "Box<String>", env, t4)
check("5.3 Box<String> 两级解引用后同样命中 &str", r4b.candidate == "&str", str(r4b))
check("5.4 两级解引用命中位置是 #7", r4b.index == 7, str(r4b.index))

# -------------------------------------------------------------- 6. 歧义 E0034
t5 = MethodTable()
t5.add("Foo", Method("dup", "Alpha", "&self"))
t5.add("Foo", Method("dup", "Beta", "&self"))
try:
    resolve("dup", "Foo", env, t5)
    raise AssertionError("6.1 两个 trait 同名方法应当歧义")
except Ambiguity as e:
    check("6.1 两个 trait 提供同名方法 → E0034", e.code == "E0034", e.code)
check("6.2 只保留一个 trait 后恢复可解析（成对对照）",
      resolve("dup", "Foo", env, MethodTable()) is None)

# ------------------------------------------- 7. 可变性不参与查找（官方原文）
t6 = MethodTable()
t6.add("Foo", Method("bump", "inherent", "&mut self"))
r7 = resolve("bump", "Foo", env, t6)
check("7.1 不可变绑定下查找照样命中 &mut self 方法", r7.candidate == "&mut Foo", str(r7))
check("7.2 但调用期报 E0596（不可变借用）",
      apply_call(r7, place_is_mut=False) == "E0596",
      str(apply_call(r7, place_is_mut=False)))
check("7.3 换成 mut 绑定即可调用（成对对照）",
      apply_call(r7, place_is_mut=True) is None)
check("7.4 unsafe 同理：查找成功、调用报 E0133",
      apply_call(r7, place_is_mut=True, receiver_is_unsafe=True) == "E0133")
r7b = resolve("nope", "Foo", env, t6)
check("7.5 找不到方法才是 E0599", apply_call(r7b, True) == "E0599")

# ------------------------------------------------- 8. 类型参数：bound 先查
t8 = MethodTable()
t8.add("T", Method("clone", "Clone", "&self"))
t8.add("T", Method("clone", "MyClone", "&self"))
try:
    resolve("clone", "T", env, t8)
    raise AssertionError("8.1 两个 trait 都非 bound 时应歧义")
except Ambiguity as e:
    check("8.1 非 bound 的两个 trait → E0034", e.code == "E0034", e.code)
r8 = resolve("clone", "T", env, t8, bound_traits=("Clone",))
check("8.2 写成 bound 后 Clone 胜出", r8.method.owner == "Clone", str(r8))
r8b = resolve("clone", "T", env, t8, bound_traits=("MyClone",))
check("8.3 换一个 bound 就换成另一个 trait（成对对照）",
      r8b.method.owner == "MyClone", str(r8b))

# --------------------------------------------------- 9. trait object 同名冲突
t9 = MethodTable()
t9.add("dyn Show", Method("show", "inherent", "&self"))
t9.add("dyn Show", Method("show", "Show", "&self"))
try:
    resolve("show", "dyn Show", env, t9, trait_object=True)
    raise AssertionError("9.1 trait object 上固有与 trait 同名应报错")
except Ambiguity as e:
    check("9.1 trait object 同名 → 编译错误", e.code == "E0034", e.code)
r9 = disambiguate("show", "Show", "dyn Show", env, t9)
check("9.2 完全限定语法命中 trait 方法", r9.method.owner == "Show", str(r9))
check("9.3 官方警告：固有方法没有任何调用手段",
      disambiguate("show", "inherent", "dyn Show", env, t9) is None)

# ------------------------------------------ 10. 数组 IntoIterator 的 edition 差异
t10 = MethodTable()
t10.add("[i32;3]", Method("into_iter", "IntoIterator", "self", item="i32"))
t10.add("&[i32;3]", Method("into_iter", "IntoIterator", "self", item="&i32"))
t10.add("&[i32]", Method("into_iter", "IntoIterator", "self", item="&i32"))
r21 = resolve("into_iter", "[i32;3]", env, t10, edition=2021)
check("10.1 2021 版数组直接按值迭代", r21.method.item == "i32", str(r21))
check("10.2 2021 版命中候选 #0（数组本身）", r21.index == 0, str(r21.index))
r18 = resolve("into_iter", "[i32;3]", env, t10, edition=2018)
check("10.3 2018 版跳过数组上的 IntoIterator，得到引用", r18.method.item == "&i32",
      str(r18))
check("10.4 2018 版命中的是 &[i32;3] 而非切片的 &[i32]",
      r18.candidate == "&[i32;3]", str(r18.candidate))

# -------------------------------------------------- 11. 解析结果与 Resolution 型
check("11.1 resolve 返回 Resolution", isinstance(r1, Resolution))
check("11.2 候选索引与候选类型可互查",
      candidate_receivers("Foo", env)[r1.index] == r1.candidate)
check("11.3 解引用链终止于无 Deref 的类型",
      candidate_types("str", env) == ["str"], str(candidate_types("str", env)))

print("resolve: %d assertions passed" % COUNT[0])


if __name__ == "__main__":
    pass
