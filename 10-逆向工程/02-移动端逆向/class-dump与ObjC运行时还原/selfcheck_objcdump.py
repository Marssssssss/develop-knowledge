# -*- coding: utf-8 -*-
"""构造假 __DATA_CONST 镜像,按 class-dump 的路径还原并断言。"""

import struct

from objcdump import ObjCImage, RO_META, WORD_SHIFT

PASS = []


def ok(msg):
    PASS.append(msg)
    print(f"  [ok] {msg}")


def build():
    img = ObjCImage()
    s = lambda t: img.add_section(t, b"\x00")   # 先占位,字符串直接 alloc 进 buf

    def cstr(x):
        return img.alloc(x.encode() + b"\x00", 1)

    # 字符串
    n_person, n_dog, n_meta = cstr("Person"), cstr("Dog"), cstr("Person")
    n_desc, n_setage, n_name, n_init = (cstr("description"), cstr("setAge:"),
                                        cstr("name"), cstr("init"))
    t_sig = cstr("v16@0:8")
    n_iv_age, t_iv_i = cstr("_age"), cstr("i")
    n_prop, a_prop = cstr("age"), cstr("Ti,V_age")
    n_cat, n_proto = cstr("Extra"), cstr("Pprintable")

    # Person 元类:ro 带 RO_META
    meta_methods = img.put_method_list([(n_desc, t_sig, 0x4000)], marker=1)
    meta_ro = img.put_ro(RO_META, n_meta, base_methods=meta_methods)
    # Person 本类:方法 + ivar(alignment_raw=~0 触发特例) + 属性
    inst_methods = img.put_method_list([(n_setage, t_sig, 0x5000),
                                        (n_name, t_sig, 0x5040)])
    ivars = img.put_ivar_list([(0, n_iv_age, t_iv_i, 0xFFFFFFFF, 4)])
    props = img.put_property_list([(n_prop, a_prop)])
    ro = img.put_ro(0, n_person, base_methods=inst_methods, ivars=ivars, props=props)

    # 元类先建(isa 指向它),data 指针低 3 位藏标志:bit0=isSwift,再置 bit1
    meta_cls = img.put_class(n_meta, 0, 0, meta_ro | 1, None)
    cls = img.put_class(n_person, 0, meta_cls, ro | 0b101, None)
    dog_ro = img.put_ro(0, n_dog, base_methods=img.put_method_list([(n_init, t_sig, 0x6000)]))
    dog = img.put_class(n_dog, cls, meta_cls, dog_ro, None)

    # classlist / catlist / protolist
    img.add_section("__objc_classlist", struct.pack("<2Q", cls, dog))
    cat_methods = img.put_method_list([(n_desc, t_sig, 0x7000)])
    cat = img.alloc(struct.pack("<8Q", n_cat, cls, cat_methods, 0, 0, 0, 0, 0))
    img.add_section("__objc_catlist", struct.pack("<Q", cat))
    proto = img.alloc(struct.pack("<5Q", 0, n_proto, 0, cat_methods, 0))
    img.add_section("__objc_protolist", struct.pack("<Q", proto))
    return img, cls


def main():
    img, cls_off = build()
    print("1. __objc_classlist 遍历")
    classes = img.load_classes()
    assert [c["name"] for c in classes] == ["Person", "Dog"]
    ok("按 classlist 顺序还原出 Person、Dog(class-dump loadClasses 的 readPtr 循环)")

    print("2. data 指针低位标志")
    person = classes[0]
    assert person["is_swift"] and person["data_flags"] == 0b101
    ok("data 指针 bit0=isSwiftClass、低 3 位被 &~7 掩掉后才是 ro 地址(实测藏了 0b101)")

    print("3. class_ro_t 字段序")
    assert person["instance_start"] == 8 and person["instance_size"] == 16
    ok("flags/instanceStart/instanceSize/reserved 后接 7 个指针,64 位 ro 共 72 字节")

    print("4. 元类识别与 isa 链")
    assert not person["is_meta"]
    meta = img.class_at(person["isa"])
    assert meta["is_meta"] and meta["name"] == "Person"
    ok("RO_META=(1<<0) 标在 ro.flags 上;类与元类同名,靠 flags 区分(class-dump 同款)")

    print("5. 方法表与 entsize 掩码")
    assert [m["name"] for m in person["methods"]] == ["setAge:", "name"]
    assert meta["methods"][0]["types"] == "v16@0:8"
    ok("经典 method_t={name,types,imp} 三指针;entsize 低 2 位是 fixup 标记,解析须 &~3")
    assert [c for c in img.load_classes()][1]["methods"][0]["name"] == "init"

    print("6. ivar 对齐特例")
    iv = person["ivars"][0]
    assert iv["name"] == "_age" and iv["type"] == "i" and iv["size"] == 4
    assert iv["alignment"] == (1 << WORD_SHIFT) and iv["alignment_raw"] == 0xFFFFFFFF
    ok("alignment_raw==~0(即 -1)时 alignment 取 1<<WORD_SHIFT=8,否则 1<<raw(objc-runtime-new.h)")

    print("7. 属性表")
    assert person["properties"] == [{"name": "age", "attributes": "Ti,V_age"}]
    ok("property_t={name,attributes} 两指针,属性编码 Ti,V_age 的含义在运行时同源")

    print("8. __objc_catlist / __objc_protolist")
    cat = img.load_categories()[0]
    assert cat["name"] == "Extra" and cat["cls"] == cls_off
    assert [m["name"] for m in cat["instance_methods"]] == ["description"]
    ok("category 八指针序:name→class→实例方法→类方法→协议→属性→v7→v8")
    proto = img.load_protocols()[0]
    assert proto["name"] == "Pprintable" and proto["instance_methods"][0]["name"] == "description"
    ok("protocol 五指针序:isa→name→protocols→实例方法→类方法(class-dump 读取序)")

    print(f"\n共 {len(PASS)} 项断言全部通过")


if __name__ == "__main__":
    main()
