// ImageIO 解码:与 python/imageio_decode.py 同题的 C / Objective-C 侧实现(人工审查用)。
// CGImageSource 是纯 C API,所有选项通过 CFDictionary 传,返回值要手动 release。

#import <ImageIO/ImageIO.h>
#import <Foundation/Foundation.h>

// MARK: - 1. 全尺寸解码 vs 下采样

static CGImageRef DecodeFull(NSURL *url) {
    CGImageSourceRef src = CGImageSourceCreateWithURL((__bridge CFURLRef)url, NULL);
    if (!src) return NULL;
    // 这一行就是把整张图按原尺寸解出来:4032x3024 的图在内存里按 4 字节/像素算约 46 MiB
    CGImageRef img = CGImageSourceCreateImageAtIndex(src, 0, NULL);
    CFRelease(src);
    return img;                                  // 调用方负责 CGImageRelease
}

static CGImageRef Downsample(NSURL *url, int maxPixelSize) {
    CGImageSourceRef src = CGImageSourceCreateWithURL((__bridge CFURLRef)url, NULL);
    if (!src) return NULL;

    NSDictionary *opts = @{
        // 一定从原图生成缩略图(文件里内嵌的缩略图会被绕过)
        (id)kCGImageSourceCreateThumbnailFromImageAlways : @YES,
        // 宽和高的最大值,单位是像素
        (id)kCGImageSourceThumbnailMaxPixelSize         : @(maxPixelSize),
        // 按 EXIF orientation 旋转/缩放到正确朝向
        (id)kCGImageSourceCreateThumbnailWithTransform  : @YES,
    };
    CGImageRef thumb = CGImageSourceCreateThumbnailAtIndex(src, 0, (__bridge CFDictionaryRef)opts);
    CFRelease(src);
    return thumb;                                // 调用方负责 CGImageRelease
}

// MARK: - 2. 解码时机:ShouldCacheImmediately

static CGImageRef DecodeNow(NSURL *url) {
    CGImageSourceRef src = CGImageSourceCreateWithURL((__bridge CFURLRef)url, NULL);
    NSDictionary *opts = @{ (id)kCGImageSourceShouldCacheImmediately : @YES };
    // 加了 ShouldCacheImmediately:解码和缓存就发生在这一行
    CGImageRef img = CGImageSourceCreateImageAtIndex(src, 0, (__bridge CFDictionaryRef)opts);
    CFRelease(src);
    return img;
}

// MARK: - 3. 渐进式加载

typedef struct {
    CGImageSourceRef source;
    NSMutableData   *accumulated;
} IncrementalDecoder;

static IncrementalDecoder IncrementalCreate(void) {
    IncrementalDecoder d;
    d.source = CGImageSourceCreateIncremental(NULL);
    d.accumulated = [NSMutableData data];
    return d;
}

// 关键:每次都要把「截至当前的全部」数据交进去,不是只交新到的那一段
static CGImageSourceStatus IncrementalAppend(IncrementalDecoder *d,
                                             NSData *newChunk,
                                             BOOL isFinal) {
    [d->accumulated appendData:newChunk];
    CGImageSourceUpdateData(d->source, (__bridge CFDataRef)d->accumulated, isFinal);
    return CGImageSourceGetStatus(d->source);
}

static CGImageRef IncrementalImageIfReady(IncrementalDecoder *d) {
    CGImageSourceStatus s = CGImageSourceGetStatus(d->source);
    if (s != kCGImageSourceStatusComplete) {
        return NULL;                             // 还没齐,取不到完整图像
    }
    return CGImageSourceCreateImageAtIndex(d->source, 0, NULL);
}

// MARK: - 4. 容器信息

static void PrintContainerInfo(NSURL *url) {
    CGImageSourceRef src = CGImageSourceCreateWithURL((__bridge CFURLRef)url, NULL);
    size_t count = CGImageSourceGetCount(src);   // 图片数,不含缩略图
    NSLog(@"images = %zu", count);
    CFStringRef type = CGImageSourceGetType(src);
    NSLog(@"UTI = %@", type);
    CFRelease(src);
}

// MARK: - 5. PDF:必须显式开 FromImage* 才是 72 dpi 缩略图

static CGImageRef PdfThumbnail(NSURL *url) {
    CGImageSourceRef src = CGImageSourceCreateWithURL((__bridge CFURLRef)url, NULL);
    // 少了 kCGImageSourceCreateThumbnailFromImageAlways / IfAbsent,PDF 会返回 NULL
    NSDictionary *opts = @{ (id)kCGImageSourceCreateThumbnailFromImageAlways : @YES };
    CGImageRef img = CGImageSourceCreateThumbnailAtIndex(src, 0, (__bridge CFDictionaryRef)opts);
    CFRelease(src);
    return img;                                  // 72 dpi 的那一页
}
