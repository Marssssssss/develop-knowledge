# 计算机视觉

聚焦图像分类、目标检测、分割等领域的**原理级**实现(纯标准库,不依赖 OpenCV/PyTorch;
Python 为主,近批 demo 同时提供 Go 实现)。

## 已完成 demo

| 编号 | 目录 | 知识点 |
| --- | --- | --- |
| 042 | [Sobel边缘检测/](./Sobel边缘检测/) | Sobel 算子 · Gx/Gy 核 · 梯度幅值与方向 · CV_8U 截断陷阱 |
| 043 | [Hough直线检测/](./Hough直线检测/) | 极坐标 ρ-θ 参数化 · 累加器投票 · 峰值 NMS · 断线鲁棒性 |
| 044 | [IoU与NMS/](./IoU与NMS/) | 交并比计算 · 贪心 NMS · 逐类 NMS 与 offset 技巧 |
| 342 | [Canny边缘检测/](./Canny边缘检测/) | 五步流水线 · 方向量化 4 扇区 · 双阈值+滞后 · 1/159 整数核 ≈ σ1.4(偏差 7.16%) |
| 343 | [Harris角点检测/](./Harris角点检测/) | 结构张量 M · `R = det − k·tr²`(k∈[0.04,0.06])· Shi-Tomasi λmin · 亚像素峰值 |
| 344 | [SIFT尺度空间/](./SIFT尺度空间/) | DoG≈尺度归一化 LoG(相关 0.984)· 26 邻居极值 · `Tr²/Det < 12.1` · 36bin+80% 方向 · 128 维描述子与 clamp 语义 |
| 345 | [YOLO检测头/](./YOLO检测头/) | σ 中心 + `pw·e^tw` 尺寸 · 网格量化误差 · 负责/忽略/负样本三态 · 独立 logistic vs softmax · k-means 锚框(d=1−IoU) |
| 346 | [ViT图像分块/](./ViT图像分块/) | `N=H·W/P²` · patch embedding ≡ Conv2d(stride=P)· [class] token · 1D 位置编码与置换对称性 · Pre-LN 残差 |

## 待研究

- [x] Canny 边缘检测(双阈值 + 滞后 + 非极大值抑制)—— → [Canny边缘检测/](./Canny边缘检测/),2026-09-18,ID 342
- [x] Harris 角点检测(结构张量 + 响应函数)—— → [Harris角点检测/](./Harris角点检测/),2026-09-18,ID 343
- [x] 图像金字塔与 SIFT 尺度空间 —— → [SIFT尺度空间/](./SIFT尺度空间/),2026-09-18,ID 344
- [x] YOLO 检测头(anchor 分配 / 置信度 / 解码)—— → [YOLO检测头/](./YOLO检测头/),2026-09-18,ID 345
- [x] ResNet / ViT(残差连接 / patch embedding)—— → [ViT图像分块/](./ViT图像分块/),2026-09-18,ID 346;残差连接见 [01-深度学习/残差连接](../01-深度学习/) (234)
- [ ] ResNet 结构细节(Bottleneck 1×1 降维 / BN 位置 / 梯度流实测)
- [ ] SAM(Segment Anything)
- [ ] Diffusion 模型(Stable Diffusion)
