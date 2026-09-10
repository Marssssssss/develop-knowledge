# DEMO_TEMPLATE.md — Demo 模板与规范

每个 demo 目录都应严格遵循本模板。

## 一、目录结构

```
<domain>/<subdomain>/<topic>/<point>/
├── README.md            # 必含：简介 + 环境 + 运行方式
├── c/                   # 可选
│   ├── main.c
│   └── README.md (可选)
├── python/              # 可选
│   ├── main.py
│   └── requirements.txt (可选)
├── go/                  # 可选
│   ├── main.go
│   └── go.mod (可选)
├── ts/                  # 可选
│   ├── index.ts
│   └── package.json
├── js/                  # 可选
│   └── index.js
├── java/                # 可选
│   └── Main.java
└── NOTES.md             # 可选：踩坑记录、设计取舍
```

每个 demo 至少 **2 种语言**，目标是**2-4 种**主流实现（C / Python / Go / TypeScript）。

## 二、README.md 模板

```markdown
# <知识点名称>

## 简介
- 一句话讲清原理与适用场景
- 关键概念清单（3-5 个）

## 对比 / 选型
（如适用：与其他实现的对比表）

## 环境准备
- 操作系统：
- 语言版本：
- 依赖：

## 运行方式
### C
\`\`\`bash
gcc -O2 -Wall -Wextra main.c -o demo
./demo
\`\`\`

### Python
\`\`\`bash
python3 main.py
\`\`\`

## 关键代码片段
（贴 20-50 行核心实现，加注释）

## 注意事项
- 平台限制
- 性能数据
- 已知坑

## 参考资料
- 链接 1
- 链接 2
```

## 三、代码风格

| 语言 | 风格 |
| --- | --- |
| C | `-Wall -Wextra -pedantic` 编译干净 |
| Python | PEP 8，`python3 -m py_compile` 通过 |
| Go | `gofmt -d` 无输出 |
| TypeScript | `tsc --noEmit` 通过 |
| Java | 编译干净 |

## 四、禁止项

- ❌ 引入复杂框架（如 Flask、Spring）来写一个最小 demo
- ❌ 注释里写"这里不解释"
- ❌ 引入网络资源（CDN 库、远程图片）
- ❌ 中文变量名（C/Go 等主流语言习惯）
- ✅ 注释充分，函数命名语义化
- ✅ 错误处理完整（C 检查返回值，Python 抛异常）
- ✅ 跨语言命名一致（如 `select_demo.c` / `select_demo.py` / `select_demo.go`）