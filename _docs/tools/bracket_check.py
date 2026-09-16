import re
import sys

path = sys.argv[1]
src = open(path, encoding="utf-8").read()
lines = [l.split("//")[0] for l in src.splitlines()]  # 剔除行注释
code = "\n".join(lines)
code = re.sub(r'"(?:[^"\\]|\\.)*"', '""', code)      # 字符串字面量置空
code = re.sub(r"'(?:[^'\\]|\\.)'", "'x'", code)      # 字符字面量置空
pairs = {"(": ")", "[": "]", "{": "}"}
stack = []
for i, ch in enumerate(code):
    if ch in pairs:
        stack.append((ch, i))
    elif ch in (")", "]", "}"):
        assert stack and pairs[stack[-1][0]] == ch, "mismatch near offset %d" % i
        stack.pop()
print("brackets:", "BALANCED" if not stack else "UNCLOSED %r" % (stack[:5],))
print("lines:", len(src.splitlines()))
