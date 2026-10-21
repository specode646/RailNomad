"""Versioned compression for JSON records; no executable object serialization."""
import zlib


MAGIC=b'RN2\0'
MAX_JSON_BYTES=64*1024*1024


def pack(text):
    raw=text.encode('utf-8')
    if len(raw)>MAX_JSON_BYTES:
        raise ValueError('单条结果片段超过 64 MiB 存储保护限制')
    return MAGIC+zlib.compress(raw,level=1)


def unpack(value):
    if isinstance(value,str):
        return value
    if not isinstance(value,bytes) or not value.startswith(MAGIC):
        raise ValueError('未知结果存储格式')
    decoder=zlib.decompressobj()
    try:
        raw=decoder.decompress(value[len(MAGIC):],MAX_JSON_BYTES+1)
    except zlib.error as exc:
        raise ValueError('结果片段压缩内容损坏') from exc
    if len(raw)>MAX_JSON_BYTES or decoder.unconsumed_tail or not decoder.eof or decoder.unused_data:
        raise ValueError('结果片段压缩内容无效或超过限制')
    return raw.decode('utf-8')
