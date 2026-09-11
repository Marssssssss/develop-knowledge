# Flutter

## 子领域

| 子目录 | 说明 |
| --- | --- |
| [三棵树/](./三棵树/) | Widget / Element / RenderObject 三棵树 + Box Constraint 模型 |

## 已完成 demo

| ID | 路径 | 知识点 | 语言 |
| --- | --- | --- | --- |
| 031 | `三棵树/` | Flutter 三棵树(Widget 不可变配置 + Element 持久化 + RenderObject 布局绘制) + Box Constraint 模型 + 渲染管线 build→layout→paint→composite | Dart |

## 待研究

- [ ] Flutter Engine(C++ Skia/Impeller + Dart VM)
- [ ] Isolate 并发模型(无共享内存 + 端口消息)
- [ ] 自定义 RenderObject(performLayout + paint 实现)
- [ ] RepaintBoundary 性能调优
- [ ] Compose Multiplatform(共享 UI 的 Flutter 替代方案)