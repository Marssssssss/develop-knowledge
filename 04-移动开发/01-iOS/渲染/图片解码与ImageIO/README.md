# 图片解码与 ImageIO:一张 12MP 的照片到底吃了多少内存

`UIImage(contentsOfFile:)` 看起来只是一次读文件,实际上它会在**你把它画到屏幕上之前**
就把整张图解成位图。一张 4032×3024 的照片按 8 位/通道 RGBA 算是
**48,771,072 字节 ≈ 46.5 MiB** —— 而显示它的那个 view 可能只有 200×150,
只需要 **120,000 字节**。差 **406 倍**。

ImageIO 的 `CGImageSource` 提供了绕开这次全尺寸解码的官方路径。
`python/imageio_decode.py` 把它做成可执行模型(30 条断言),
`objc/ImageIODecode.m` 是同题的 C/Objective-C 实现。

## 1. 两笔账:文件字节数 vs 解码后字节数

| 量 | 4032×3024 的 HEIC | 200px 缩略图 |
| --- | --- | --- |
| 解码后位图(4 字节/像素) | **48,771,072 ≈ 46.5 MiB** | **120,000 ≈ 0.11 MiB** |
| 比值 | 406× | 1× |

文件多大和占用多少内存**没有关系**:压缩格式只影响磁盘,解码后的位图永远按
`宽 × 高 × 每像素字节数` 算。所以「图片才 2 MB,不可能 OOM」是最常见的误判。

> 口径说明:4 字节/像素(RGBA,8 位/通道)是**本 demo 的显式假设**,用于把量级算清楚;
> 官方文档没有给出每像素字节数。

## 2. 三个 key,三种完全不同的行为

`CGImageSourceCreateThumbnailAtIndex` 的行为完全由选项字典决定:

| 选项 | 语义(文档原文) | 模型断言 |
| --- | --- | --- |
| `…CreateThumbnailFromImageAlways` | 总是从原图生成缩略图 | 13–16、18 |
| `…CreateThumbnailFromImageIfAbsent` | 源文件里没有缩略图时才生成 | 10–11 |
| `…ThumbnailMaxPixelSize` | 缩略图**宽和高的最大值**,单位是**像素** | 05–07、17 |
| `…CreateThumbnailWithTransform` | 按 orientation 旋转并缩放到正确朝向 | 19–21 |
| `…ShouldCache` / `…ShouldCacheImmediately` | 是否缓存 / 是否在**创建时**就解码缓存 | 22–23 |

最容易被忽略的组合:

* **两个 FromImage key 都不给、文件里又没有内嵌缩略图 → 返回 `NULL`**(10)。
  这不是出错,是「你没说要生成,我也没有现成的」。
* 有内嵌缩略图时,不带任何 key 会**直接用内嵌的那张**,一次全尺寸解码都不做(12)——
  这是最省的路径,但内嵌缩略图通常只有 160×120,尺寸任由文件决定。
* 一旦给了 `Always`,**内嵌缩略图会被绕过**,重新解码整张再缩(16)。

## 3. `MaxPixelSize` 约束的是长边,不是面积

4000×1000 的图配 `MaxPixelSize = 400` → 得到 **400×100**(17):长边被压到 400,
短边按同比例缩。而 `MaxPixelSize` 大于原图尺寸时**原样返回**(18),不会放大。

## 4. `WithTransform`:先换轴,再谈长边

EXIF orientation 为 6(顺时针 90°)的 4032×3024 图,**存储方向是横的,显示方向是竖的**:

| 选项 | 结果 | 断言 |
| --- | --- | --- |
| 只给 `MaxPixelSize = 300` | 300×**225**(按存储方向缩) | 19 |
| 再加 `WithTransform` | **225×300**(先换轴再缩) | 20 |
| orientation = 1 时加不加 | 结果一致 | 21 |

少了 `WithTransform`,你会得到一张**朝向不对**的缩略图 —— 而它在本地相册里看着是对的。

## 5. `ShouldCacheImmediately`:把解码从「用时」提前到「创建时」

| 写法 | 创建时是否解码 | 断言 |
| --- | --- | --- |
| 不给该 key | 否(推迟到真正绘制时) | 23 |
| `kCGImageSourceShouldCacheImmediately = true` | 是 | 22 |

副作用是**峰值内存提前且确定**:后台线程批量建图时,如果同时开了
`ShouldCacheImmediately`,所有图的位图会在同一时刻全部驻留。

## 6. 渐进式加载:每次必须交「全部累计数据」

`CGImageSourceCreateIncremental` 建的是空容器,靠 `CGImageSourceUpdateData` 喂数据。
文档的要求很明确:**「each time you call the method, you must specify all of the
accumulated image data, not just the new data you received」** —— 只交新到的那一段是错的。

模型用一个「新块不以累计数据为前缀 → 判为非法」的检查把这件事当场抓出来(23):

| 累计数据 | 状态 |
| --- | --- |
| 0 字节 | `statusUnknownType` |
| < 头部 | `statusReadingHeader` |
| 头部之后但不完整 | `statusIncomplete` |
| 完整 | `statusComplete` |
| `final = true` 但数据不齐 | `statusUnexpectedEOF` |
| 只交了新块 | `statusInvalidData` |

只有 `statusComplete` 才取得到完整图像(26);`statusIncomplete` 时取图得到 `NULL`(22)。
另外还有 `statusUnknownType` 与 `statusInvalidData` 两个失败态。

## 7. 容器层面的两个计数

* `CGImageSourceGetCount` 数的是**图片数,不含缩略图**(27);
* `CGImageSourceGetPrimaryImageIndex` 返回 **HEIF 的主图下标**,它**可以不是 0**(28)——
  连拍、实况照片的主图常常在中间。

## 8. PDF 是特例:不给 FromImage* 就没有缩略图

文档写明:对 PDF,必须给 `…FromImageIfAbsent` 或 `…FromImageAlways` 之一(值为 true),
否则拿不到图,而且生成的是**该页 72 dpi** 的图(29–30)。

## 9. 运行方式

```bash
cd python && python selfcheck_imageio_decode.py    # 30 条断言
```

`objc/ImageIODecode.m` 只作人工对照(本机无 Xcode 工具链)。

## 10. 关键代码

```python
def _fit(self, size, max_px, transform):
    w, h = size
    if transform and self.file.exif_orientation in ORIENTATION_SWAPS_AXES:
        w, h = h, w                      # WithTransform:先按 orientation 换轴
    if max_px is None or max(w, h) <= max_px:
        return DecodedImage(w, h)
    scale = max_px / float(max(w, h))
    return DecodedImage(int(w * scale), int(h * scale))
```

## 11. 性能与适用边界

* 下采样的收益是**内存峰值**,不是 CPU:它仍然要读一遍压缩数据,只是不保留全尺寸位图。
* `MaxPixelSize` 只能**缩小**不能放大;`Always` 会强制走一次全解码,对超大图反而是负担。
* 模型是确定性的:真实 ImageIO 的解码是惰性 + 可缓存的,同一 source 重复取图可能命中缓存,
  不一定每次都付一遍解码代价。

## 12. 注意事项与常见坑

1. **`CGImageSourceCreateThumbnailAtIndex` 返回 `NULL` 不一定是失败** —— 很可能只是没给
   FromImage key。
2. **内嵌缩略图是捷径也是陷阱**:它省了解码,但尺寸由文件决定,清晰度不够时只能换 `Always`。
3. **忘了 `WithTransform`** 会得到朝向错误的图,而且只在「用户拍的照片」上复现。
4. **渐进式 `UpdateData` 传新块**是最经典的 bug:小图能过、大图必挂。
5. `final = true` 之后状态会变成 `UnexpectedEOF` 而不是 `Complete`,别把「下载结束」
   当成「数据收全了」。
6. 所有 `Create` 出来的引用都要自己 release(`CGImageRelease` / `CFRelease`)。

## 参考资料

* Apple《CGImageSource》
  <https://developer.apple.com/tutorials/data/documentation/imageio/cgimagesource.json>
* Apple《CGImageSourceCreateThumbnailAtIndex》
  <https://developer.apple.com/tutorials/data/documentation/imageio/cgimagesourcecreatethumbnailatindex(_:_:_:).json>
* Apple《CGImageSourceCreateIncremental》《CGImageSourceUpdateData》
  <https://developer.apple.com/tutorials/data/documentation/imageio/cgimagesourcecreateincremental(_:).json>
  <https://developer.apple.com/tutorials/data/documentation/imageio/cgimagesourceupdatedata(_:_:_:).json>
* Apple《CGImageSourceStatus》
  <https://developer.apple.com/tutorials/data/documentation/imageio/cgimagesourcestatus.json>
