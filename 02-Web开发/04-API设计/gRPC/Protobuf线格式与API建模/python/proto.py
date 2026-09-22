"""proto3 的字段号治理与"拼接即合并"语义。

口径来源（本轮实读 protobuf.dev/programming-guides/proto3/ 与 encoding/）：
  - 字段号 MUST 在 1 .. 536,870,911 之间，且在同一 message 内唯一。
  - **19,000 ~ 19,999 保留给 Protocol Buffers 实现**（用了 protoc 会报错）。
  - 字段号只有 29 位可用，另 3 位给线类型。
  - 1~15 的 tag 占 1 字节，16~2047 占 2 字节 ⇒ 最常设置的字段应放在 1~15。
  - 字段号**绝不可复用**；删除字段后 MUST 写进 reserved。
  - reserved 的数字区间是**闭区间**（`9 to 11` = 9, 10, 11）。
  - **字段号与字段名不能混在同一条 reserved 语句里**。
  - 保留名只影响 protoc（运行时唯一的例外是 TextProto 可能丢弃保留名字段，
    且只有 C++ 与 Go 实现这么做）；**运行时 JSON 解析不受保留名影响**。
  - Last One Wins：标量/字符串取最后一次；**嵌入消息是合并**（标量子字段覆盖、
    单值子消息递归合并、repeated 拼接）；repeated 直接拼接。
  - 于是 `Parse(str1 + str2)` == `Parse(str1)` 再 `MergeFrom(Parse(str2))`。
  - map 等价于 `repeated Entry { key = 1; value = 2; }`，顺序不保证。

运行：python proto.py
"""

FIELD_MIN = 1
FIELD_MAX = 536870911
RESERVED_IMPL_LOW = 19000
RESERVED_IMPL_HIGH = 19999


def validate_field_number(number):
    """返回错误列表（空即合法）。"""
    out = []
    if not isinstance(number, int) or isinstance(number, bool):
        return ["字段号必须是整数"]
    if number < FIELD_MIN:
        out.append("字段号 %d 小于下限 %d" % (number, FIELD_MIN))
    if number > FIELD_MAX:
        out.append("字段号 %d 大于上限 %d" % (number, FIELD_MAX))
    if RESERVED_IMPL_LOW <= number <= RESERVED_IMPL_HIGH:
        out.append("字段号 %d 落在实现保留区间 %d~%d"
                   % (number, RESERVED_IMPL_LOW, RESERVED_IMPL_HIGH))
    return out


def tag_size_bytes(field_number):
    """tag 的 varint 字节数：1~15 占 1 字节，16~2047 占 2 字节。"""
    return _varint(field_number << 3)


def _varint(value):
    out = 0
    while True:
        out += 1
        value >>= 7
        if not value:
            return out


def expand_reserved_numbers(entries):
    """把 `2, 15, 9 to 11` 展开成集合；区间是**闭区间**。"""
    out = set()
    for entry in entries:
        if isinstance(entry, tuple):
            low, high = entry
            out.update(range(low, high + 1))
        else:
            out.add(entry)
    return out


def reserved_statement(values):
    """reserved 语句里**不能**混用字段名与字段号。

    返回 (数字集合, 名字集合, 错误列表)。
    """
    numbers, names, errors = set(), set(), []
    has_number = any(isinstance(v, (int, tuple)) and not isinstance(v, bool) for v in values)
    has_name = any(isinstance(v, str) for v in values)
    if has_number and has_name:
        errors.append("字段号与字段名不能写在同一条 reserved 语句里")
    for value in values:
        if isinstance(value, tuple):
            low, high = value
            if low > high:
                errors.append("区间 %s 的下界大于上界" % (value,))
            numbers.update(range(low, high + 1))
        elif isinstance(value, bool):
            errors.append("布尔不是合法字段号")
        elif isinstance(value, int):
            numbers.add(value)
        elif isinstance(value, str):
            names.add(value)
    return numbers, names, errors


def merge_messages(base, incoming):
    """Message::MergeFrom 的语义。

    - 标量 / 字符串：后者覆盖（last one wins）；
    - 嵌入消息：递归合并；
    - repeated：拼接。

    值形态约定：list 表示 repeated，dict 表示单值子消息，其余是标量。
    """
    out = dict(base)
    for key, new_value in incoming.items():
        old_value = out.get(key)
        if isinstance(new_value, list) or isinstance(old_value, list):
            merged = (old_value or []) + (new_value or [])
            out[key] = merged
        elif isinstance(new_value, dict) and isinstance(old_value, dict):
            out[key] = merge_messages(old_value, new_value)
        else:
            out[key] = new_value
    return out


def map_entries(mapping, key_field=1, value_field=2):
    """map<K,V> 等价于 repeated Entry{key=1; value=2}。"""
    return [{key_field: k, value_field: v} for k, v in mapping.items()]


def map_from_entries(entries, key_field=1, value_field=2):
    """从 Entry 列表还原成 dict；重复 key 时按 last one wins。"""
    out = {}
    for entry in entries:
        out[entry[key_field]] = entry[value_field]
    return out


def to_message(fields):
    """把 [(字段号, 值), ...] 折叠成 message 字典；重复字段号即 repeated。"""
    out = {}
    for number, value in fields:
        if number in out:
            if not isinstance(out[number], list):
                out[number] = [out[number]]
            out[number].append(value)
        else:
            out[number] = value
    return out
