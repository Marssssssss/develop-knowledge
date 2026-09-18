"""StandardMethodCodec:方法调用与应答信封(建在 sc_codec 的 StandardMessageCodec 之上)。

格式依据(实读 flutter/packages/flutter/lib/src/services/message_codecs.dart):
  * 方法调用 = 方法名 String 的编码 ++ 参数值的编码(两段直接拼接,**没有信封**);
  * 应答信封 = 首字节 0 表示成功,后接结果值;首字节**非 0** 表示错误,
    后接 code / message / details 三个值 —— 判定是"非零"而不是"等于 1";
  * 空信封抛 'Expected envelope, got nothing';结构不合法抛 'Invalid envelope';
  * 方法名必须是 String 且缓冲区恰好读空,否则抛 'Invalid method call'。
"""

import struct  # noqa: F401  (与 sc_codec 保持同一份依赖集合,便于交叉阅读)

from sc_codec import (TYPE_STRING, FormatException, ReadBuffer, WriteBuffer,
                      read_value, write_value)


class PlatformException(Exception):
    """对应 Dart 的 PlatformException(错误信封被解码后抛出)。"""

    def __init__(self, code, message=None, details=None, stacktrace=None):
        super().__init__("PlatformException(%s, %s, %s)" % (code, message, details))
        self.code = code
        self.message = message
        self.details = details
        self.stacktrace = stacktrace


def encode_method_call(method, arguments=None):

    buf = WriteBuffer()
    write_value(buf, method)
    write_value(buf, arguments)
    return bytes(buf.data)


def decode_method_call(data):
    buf = ReadBuffer(data)
    method = read_value(buf)
    arguments = read_value(buf)
    if isinstance(method, str) and not buf.has_remaining:
        return method, arguments
    raise FormatException("Invalid method call")


def encode_success_envelope(result=None):
    buf = WriteBuffer()
    buf.put_uint8(0)
    write_value(buf, result)
    return bytes(buf.data)


def encode_error_envelope(code, message=None, details=None):
    buf = WriteBuffer()
    buf.put_uint8(1)
    write_value(buf, code)
    write_value(buf, message)
    write_value(buf, details)
    return bytes(buf.data)


def decode_envelope(envelope):
    if len(envelope) == 0:
        raise FormatException("Expected envelope, got nothing")
    buf = ReadBuffer(envelope)
    if buf.get_uint8() == 0:
        return read_value(buf)
    error_code = read_value(buf)
    error_message = read_value(buf)
    error_details = read_value(buf)
    # 源码额外支持第 4 个值:堆栈(编码侧不写,解码侧可选读)
    error_stacktrace = read_value(buf) if buf.has_remaining else None
    if isinstance(error_code, str) and (error_message is None or isinstance(error_message, str)) \
            and not buf.has_remaining:
        raise PlatformException(error_code, error_message, error_details, error_stacktrace)
    raise FormatException("Invalid envelope")
