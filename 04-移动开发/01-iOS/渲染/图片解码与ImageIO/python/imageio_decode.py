"""ImageIO / CGImageSource:解码、下采样、渐进式加载的可执行模型。

模型口径(全部对应 Apple 官方文档,见 README 参考资料):
* CGImageSourceCreateThumbnailAtIndex + 两个「从原图生成缩略图」的 key;
* kCGImageSourceThumbnailMaxPixelSize 是「宽和高的最大值」,按像素计;
* kCGImageSourceCreateThumbnailWithTransform 负责按 orientation 旋转/缩放;
* kCGImageSourceShouldCacheImmediately 决定解码与缓存发生在「创建时」还是用的时候;
* 渐进式源:每次 update 必须提交**截至当前的全部**数据(不是只提交新块),并用 final 收尾;
* 状态机:statusReadingHeader / statusIncomplete / statusComplete / statusUnexpectedEOF
         / statusInvalidData / statusUnknownType。

位图内存按「8 位/通道 RGBA,4 字节/像素」估算 —— 这是**模型的显式假设**,不是文档数值。
"""

BYTES_PER_PIXEL = 4

# EXIF orientation:1 无旋转;6 顺时针 90°(显示时宽高互换)
ORIENTATION_SWAPS_AXES = {5, 6, 7, 8}


class ImageFile:
    def __init__(self, width, height, total_bytes=None, has_embedded_thumb=False,
                 thumb_size=(160, 120), exif_orientation=1, kind="jpeg", images=1,
                 primary_index=0):
        self.width = width
        self.height = height
        self.total_bytes = total_bytes or width * height // 4
        self.has_embedded_thumb = has_embedded_thumb
        self.thumb_size = thumb_size
        self.exif_orientation = exif_orientation
        self.kind = kind
        self.images = images
        self.primary_index = primary_index


class DecodedImage:
    def __init__(self, width, height, source="full"):
        self.width = width
        self.height = height
        self.source = source                      # "full" | "embedded"

    @property
    def byte_count(self):
        return self.width * self.height * BYTES_PER_PIXEL

    def mib(self):
        return self.byte_count / 1024 / 1024


class ImageSource:
    decode_count = 0                              # 全尺寸解码次数(用来数内存峰值)

    def __init__(self, file):
        self.file = file
        self.index = 0

    # -- 读信息
    def count(self):
        """CGImageSourceGetCount:图片数量,**不含**缩略图。"""
        return self.file.images

    def primary_image_index(self):
        """CGImageSourceGetPrimaryImageIndex:HEIF 的主图下标。"""
        return self.file.primary_index

    # -- 全尺寸解码
    def create_image_at_index(self, index=0, cache_immediately=False):
        self._check_index(index)
        decoded = DecodedImage(self.file.width, self.file.height)
        if cache_immediately:
            type(self).decode_count += 1          # 创建时就解码并缓存
        self._lazy = decoded                      # 否则留到真正要用的时候
        return decoded

    def _check_index(self, index):
        if not 0 <= index < self.file.images:
            raise IndexError("invalid image index")

    # -- 缩略图
    def create_thumbnail_at_index(self, index=0, options=None):
        options = options or {}
        self._check_index(index)
        always = options.get("kCGImageSourceCreateThumbnailFromImageAlways", False)
        if_absent = options.get("kCGImageSourceCreateThumbnailFromImageIfAbsent", False)
        max_px = options.get("kCGImageSourceThumbnailMaxPixelSize")
        transform = options.get("kCGImageSourceCreateThumbnailWithTransform", False)

        # PDF 特殊规则:必须给 Always 或 IfAbsent 之一,而且是 72 dpi
        if self.file.kind == "pdf":
            if not (always or if_absent):
                return None
            base = (612, 792)                     # 8.5 x 11 inch @ 72 dpi
            return self._fit(base, max_px, transform)

        if always:
            img = self._decode_full()             # 一定要解码整张再缩
        elif self.file.has_embedded_thumb:
            img = DecodedImage(*self.file.thumb_size, source="embedded")
        elif if_absent:
            img = self._decode_full()
        else:
            return None                           # 文件里没缩略图,也没让生成 → NULL

        fitted = self._fit((img.width, img.height), max_px, transform)
        fitted.source = img.source
        return fitted

    def _decode_full(self):
        type(self).decode_count += 1
        return DecodedImage(self.file.width, self.file.height)

    def _fit(self, size, max_px, transform):
        w, h = size
        # WithTransform:按 orientation 把「显示尺寸」换成旋转后的,再谈长边
        if transform and self.file.exif_orientation in ORIENTATION_SWAPS_AXES:
            w, h = h, w
        if max_px is None or max(w, h) <= max_px:
            return DecodedImage(w, h)
        scale = max_px / float(max(w, h))
        return DecodedImage(int(w * scale), int(h * scale))


class IncrementalSource:
    """CGImageSourceCreateIncremental + CGImageSourceUpdateData。"""

    def __init__(self, file, header_bytes=100):
        self.file = file
        self.header_bytes = header_bytes
        self.accumulated = b""
        self.status = "statusUnknownType"

    def update_data(self, data, final=False):
        """每次提交**截至当前的全部**数据;只提交新块会被判为非法。"""
        if not data.startswith(self.accumulated):
            self.status = "statusInvalidData"     # 把「只传新块」当场抓出来
            return self.status
        self.accumulated = data
        n = len(data)
        if n == 0:
            self.status = "statusUnknownType"
        elif n < self.header_bytes:
            self.status = "statusReadingHeader"
        elif n >= len(self):
            self.status = "statusComplete"
        else:
            self.status = "statusIncomplete"
            if final:
                self.status = "statusUnexpectedEOF"
        return self.status

    def __len__(self):
        return self.file.total_bytes

    def image_at(self, index=0):
        if self.status != "statusComplete":
            return None
        return DecodedImage(self.file.width, self.file.height)
