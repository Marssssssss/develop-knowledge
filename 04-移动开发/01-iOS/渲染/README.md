# iOS · 渲染

研究从「图层树」到「屏幕像素」这条链路上的每一笔成本。

## 子领域

| 子目录 | 知识点 |
| --- | --- |
| [CoreAnimation管线/](./CoreAnimation管线/) | Render Loop 五段:Commit(Layout / Display / Prepare)与 render server + GPU 的分工、脏标记合并、离屏渲染、`shouldRasterize` 盈亏平衡、混合与 overdraw |
| [图片解码与ImageIO/](./图片解码与ImageIO/) | `CGImageSource`:全尺寸解码 vs 下采样的内存差、`FromImageAlways/IfAbsent`、`MaxPixelSize`、`WithTransform`、渐进式加载与六个状态 |

## 待研究

- [x] Core Animation 渲染管线 → demo 231
- [x] 图片解码与 ImageIO(下采样 / 渐进式 / HEIF)→ demo 455
- [ ] 离屏渲染触发条件的完整清单与 `shouldRasterize` 的实测拐点
- [ ] 大图分块解码(`CATiledLayer`)与内存峰值
- [ ] 视频帧 / `CVPixelBuffer` 的 IOSurface 零拷贝路径
- [ ] Metal 与 Core Animation 的混合合成
