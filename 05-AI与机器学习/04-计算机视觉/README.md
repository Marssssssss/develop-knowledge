# 计算机视觉

聚焦图像分类、目标检测、分割等领域的**原理级**实现(纯 Python 标准库,不依赖 OpenCV/PyTorch)。

## 已完成 demo

| 编号 | 目录 | 知识点 |
| --- | --- | --- |
| 042 | [Sobel边缘检测/](./Sobel边缘检测/) | Sobel 算子 · Gx/Gy 核 · 梯度幅值与方向 · CV_8U 截断陷阱 |
| 043 | [Hough直线检测/](./Hough直线检测/) | 极坐标 ρ-θ 参数化 · 累加器投票 · 峰值 NMS · 断线鲁棒性 |
| 044 | [IoU与NMS/](./IoU与NMS/) | 交并比计算 · 贪心 NMS · 逐类 NMS 与 offset 技巧 |

## 待研究

- [ ] Canny 边缘检测(双阈值 + 滞后 + 非极大值抑制)—— Sobel 042 的下游
- [ ] Harris 角点检测(结构张量 + 响应函数)
- [ ] 图像金字塔与 SIFT 尺度空间
- [ ] YOLO 检测头(anchor 分配 / 置信度 / 解码)—— 044 的下游
- [ ] ResNet / ViT(残差连接 / patch embedding)
- [ ] SAM(Segment Anything)
- [ ] Diffusion 模型(Stable Diffusion)
