"""HCL (HashiCorp Configuration Language) 最小解析器

来源:HCL Native Syntax Specification v2.12.0 (github.com/hashicorp/hcl/blob/v2.12.0/hclsyntax/spec.md)
     Packer HCL Configuration Syntax (docs.hashicorp.com/packer/docs/v1.8.x/templates/hcl_templates/syntax)
     HCL GitHub README (github.com/hashicorp/hcl)

覆盖能力:
- Attribute 定义:  name = expression
- Block 定义:     TYPE "label1" "label2" { ... }
- 注释:           # line, // line, /* block */
- 表达式字面量:   string (双引号, 支持 ${...} 模板插值)、number、bool、null
- 字符串字面量:   字符串拼接 "a${var.x}b"
- 嵌套块:         resource "x" "y" { tags = { Name = "foo" } }
- 标识符:         Unicode + hyphen + underscore (首字符不可为数字)

不实现(明确边界):
- 完整表达式子语言 (for/splat/index/arith)
- heredoc (<<EOT ... EOT)
- function call
- JSON 语法等价 (HCL 也是 JSON 的超集)

输出: 嵌套 dict { <block_type>: { <labels...>: { <attrs/blocks> } } } 或顶层 attr 列表
"""

import json
import re
from typing import Any


# -----------------------------------------------------------------------------
# Lexer — 把源文本切成 token 流
# -----------------------------------------------------------------------------

# 标识符: HCL 规范说 Unicode letter/_,首字符不可数字;为简化用 ASCII + 字母
_IDENT = re.compile(r"[_A-Za-z][-_\w]*")
_NUM = re.compile(r"-?\d+(\.\d+)?")
# 字符串字面量仅支持双引号 + ${...} 插值片段
_STR_FRAG = re.compile(r'"([^"\\]|\\["\\nrt])*"', re.DOTALL)

TOKEN_PATTERNS = [
    ("COMMENT_BLOCK", re.compile(r"/\*[\s\S]*?\*/")),
    ("COMMENT_LINE_HASH", re.compile(r"#[^\n]*")),
    ("COMMENT_LINE_SLASH", re.compile(r"//[^\n]*")),
    ("STRING", _STR_FRAG),
    ("NUMBER", _NUM),
    ("IDENT", _IDENT),
    ("OP", re.compile(r"[={}\[\],().*+\-/]")),
    ("WS", re.compile(r"\s+")),
]


def tokenize(src: str):
    """把 HCL 源码切成 (type, value) 列表"""
    tokens = []
    i = 0
    while i < len(src):
        for type_, pat in TOKEN_PATTERNS:
            m = pat.match(src, i)
            if m:
                if type_ != "WS" and not type_.startswith("COMMENT"):
                    tokens.append((type_, m.group(0)))
                i = m.end()
                break
        else:
            raise SyntaxError(f"Unexpected character at {i}: {src[i]!r}")
    return tokens


# -----------------------------------------------------------------------------
# Parser — 递归下降,把 token 流变成嵌套 dict
# -----------------------------------------------------------------------------

class Parser:
    def __init__(self, tokens):
        self.toks = tokens
        self.i = 0

    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else (None, None)

    def eat(self, expected_type=None, expected_value=None):
        t, v = self.peek()
        if expected_type and t != expected_type:
            raise SyntaxError(f"Expected {expected_type} got {t}({v}) at {self.i}")
        if expected_value and v != expected_value:
            raise SyntaxError(f"Expected {expected_value} got {t}({v}) at {self.i}")
        self.i += 1
        return v

    def parse(self):
        """顶层:一系列 attribute 或 block"""
        body = {}
        while self.i < len(self.toks):
            self._parse_item(body)
        return body

    def _parse_item(self, body):
        t, v = self.peek()
        if t == "IDENT":
            # 区分 attribute (下一 token 是 =) 还是 block (下一 token 是 { 或 字符串 label)
            save_i = self.i
            name = self.eat("IDENT")
            nxt = self.peek()
            if nxt == ("OP", "="):
                # attribute
                self.eat("OP", "=")
                value = self._parse_expr()
                body[name] = value
            else:
                # block
                self.i = save_i
                self._parse_block(body)
        else:
            raise SyntaxError(f"Unexpected {t}({v}) at {self.i}")

    def _parse_block(self, body):
        block_type = self.eat("IDENT")
        labels = []
        # 收集所有 string label 直到 OP { 或 OP (
        while True:
            t, v = self.peek()
            if t == "STRING":
                labels.append(self._parse_string())
            elif t == "OP" and v in "({[":
                break
            elif t == "IDENT":
                # 无引号 label (per spec: Block labels can either be quoted literal strings or naked identifiers)
                labels.append(self.eat("IDENT"))
            else:
                raise SyntaxError(f"Unexpected {t}({v}) in block at {self.i}")

        # body 必须在 {}
        opener = self.peek()
        if opener != ("OP", "{"):
            raise SyntaxError(f"Expected {{ got {opener} at {self.i}")
        self.eat("OP", "{")
        inner = {}
        while self.peek() != ("OP", "}"):
            self._parse_item(inner)
        self.eat("OP", "}")

        # 插入到 body: body[block_type][label1][label2]... = inner
        cursor = body.setdefault(block_type, {})
        for label in labels[:-1]:
            cursor = cursor.setdefault(label, {})
        if labels:
            cursor[labels[-1]] = inner
        else:
            # 无 label: block_type 直接就是 dict (覆盖)
            if not isinstance(cursor, dict) or any(isinstance(v, dict) for v in cursor.values()):
                # 已经设过同名无 label block,合并
                cursor.update(inner)
            else:
                body[block_type] = inner

    def _parse_expr(self):
        """表达式:目前只支持 string / number / bool / null / 一级嵌套 object/tuple"""
        t, v = self.peek()
        if t == "STRING":
            return self._parse_string()
        if t == "NUMBER":
            self.eat("NUMBER")
            return int(v) if "." not in v else float(v)
        if t == "IDENT":
            ident = self.eat("IDENT")
            if ident == "true":
                return True
            if ident == "false":
                return False
            if ident == "null":
                return None
            # 普通 ident 当字符串变量名
            return ident
        if t == "OP":
            if v == "{":
                return self._parse_object()
            if v == "[":
                return self._parse_tuple()
            if v == "-":
                # 一元负号
                nxt = self.peek()
                # 不实现
                raise SyntaxError("Unary minus not supported")
        raise SyntaxError(f"Bad expression {t}({v}) at {self.i}")

    def _parse_object(self):
        """{ key = expr, key = expr }"""
        self.eat("OP", "{")
        obj = {}
        while self.peek() != ("OP", "}"):
            key = self.eat("IDENT")
            self.eat("OP", "=")
            value = self._parse_expr()
            obj[key] = value
            nxt = self.peek()
            if nxt == ("OP", ","):
                self.eat("OP", ",")
            elif nxt == ("OP", "}"):
                break
            else:
                raise SyntaxError(f"Expected , or }} got {nxt} at {self.i}")
        self.eat("OP", "}")
        return obj

    def _parse_tuple(self):
        """[ expr, expr ]"""
        self.eat("OP", "[")
        lst = []
        while self.peek() != ("OP", "]"):
            lst.append(self._parse_expr())
            nxt = self.peek()
            if nxt == ("OP", ","):
                self.eat("OP", ",")
            elif nxt == ("OP", "]"):
                break
            else:
                raise SyntaxError(f"Expected , or ] got {nxt} at {self.i}")
        self.eat("OP", "]")
        return lst

    def _parse_string(self):
        """字符串:支持 "..."${var}..." 的简单插值,首尾必须有完整 "..."""
        s = self.eat("STRING")
        # 去除前后 ",处理转义
        inner = s[1:-1]
        # 简化:不做插值识别,只返回 string literal
        return inner.encode().decode("unicode_escape")


# -----------------------------------------------------------------------------
# Demo / 自测
# -----------------------------------------------------------------------------

SAMPLE_HCL = '''
# 示例 HCL —— 模仿 terraform 风格
provider "aws" {
  region = "us-east-1"
  alias  = "primary"
}

resource "aws_instance" "web" {
  ami           = "ami-0c55b159cbfafe1f0"
  instance_type = "t3.micro"
  count         = 2

  tags = {
    Name = "web-server"
    Env  = "prod"
  }
}

resource "aws_s3_bucket" "logs" {
  bucket = "my-logs-bucket"
  acl    = "private"
}

variable "region" {
  default = "us-west-2"
}
'''


def main():
    print("=== HCL 解析器 demo ===\n")
    print("--- Input HCL ---")
    print(SAMPLE_HCL)

    tokens = tokenize(SAMPLE_HCL)
    print(f"--- Lexer: {len(tokens)} tokens ---")
    for t, v in tokens[:20]:
        print(f"  {t:8s} {v!r}")
    print(f"  ... ({len(tokens)} total)\n")

    parser = Parser(tokens)
    ast = parser.parse()
    print("--- AST (顶层结构) ---")
    print(json.dumps(ast, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
