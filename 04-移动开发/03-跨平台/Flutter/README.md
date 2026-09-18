# Flutter

## 子领域

| 子目录 | 说明 |
| --- | --- |
| [三棵树/](./三棵树/) | Widget / Element / RenderObject 三棵树 + Box Constraint 模型 |
| [平台通道/](./平台通道/) | MethodChannel / EventChannel 与 StandardMessageCodec 二进制格式 |
| [dart-ffi/](./dart-ffi/) | dart:ffi 的 ABI 相关整数类型与 C 结构体布局 |

## 已完成 demo

| ID | 路径 | 知识点 | 语言 |
| --- | --- | --- | --- |
| 031 | `三棵树/` | Flutter 三棵树(Widget 不可变配置 + Element 持久化 + RenderObject 布局绘制) + Box Constraint 模型 + 渲染管线 build→layout→paint→composite | Dart |
| 337 | `平台通道/` | StandardMessageCodec 类型字节 + expanding 长度格式 + 8 字节对齐;MethodCodec 应答信封首字节 0 = 成功、非 0 = 错误 | Dart / Python |
| 338 | `dart-ffi/` | `Long` / `Size` / `UintPtr` 宽度随 ABI 变化;LLP64 上 `long` 仍是 4 字节;结构体 stride 按 C ABI | Dart / C / Python |

## 待研究

- [x] Flutter 平台通道与编解码 → demo 337
- [ ] Flutter Engine(C++ Skia/Impeller + Dart VM)
- [ ] Isolate 并发模型(无共享内存 + 端口消息)
- [ ] 自定义 RenderObject(performLayout + paint 实现)
- [ ] RepaintBoundary 性能调优
- [ ] Flutter 混合栈(Add-to-App 平台视图嵌入)