"""
标注工具测试数据生成与校验工具
核心功能：
- UTF-16 code unit 切片（与 JavaScript String.prototype.slice 行为一致）
- 换行符规范化（统一为 LF）
- SHA256 哈希计算
- 动态查找证据文本偏移量
"""
import hashlib


def normalize_newlines(text):
    """将所有换行符统一为 LF"""
    return text.replace('\r\n', '\n').replace('\r', '\n')


def read_text_file_lf(filepath):
    """读取文本文件，确保换行符为 LF"""
    with open(filepath, 'rb') as f:
        raw = f.read()
    text = raw.decode('utf-8')
    return normalize_newlines(text)


def compute_sha256(text):
    """计算文本的 SHA256 哈希"""
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def to_utf16_code_units(text):
    """转换为 UTF-16 code unit 列表，与 JS 字符串索引一致"""
    code_units = []
    for ch in text:
        cp = ord(ch)
        if cp <= 0xFFFF:
            code_units.append(cp)
        else:
            high = 0xD800 + ((cp - 0x10000) >> 10)
            low = 0xDC00 + ((cp - 0x10000) & 0x3FF)
            code_units.append(high)
            code_units.append(low)
    return code_units


def utf16_len(text):
    """UTF-16 code unit 数量，与 JS string.length 一致"""
    return len(to_utf16_code_units(text))


def utf16_slice(text, start, end):
    """模拟 JS String.prototype.slice，UTF-16 code unit 索引"""
    code_units = to_utf16_code_units(text)
    total = len(code_units)

    if start < 0:
        start = max(0, total + start)
    if end < 0:
        end = max(0, total + end)

    start = max(0, min(start, total))
    end = max(start, min(end, total))

    sliced = code_units[start:end]
    result = []
    i = 0
    while i < len(sliced):
        cu = sliced[i]
        if 0xD800 <= cu <= 0xDBFF and i + 1 < len(sliced):
            low = sliced[i + 1]
            if 0xDC00 <= low <= 0xDFFF:
                cp = 0x10000 + ((cu - 0xD800) << 10) + (low - 0xDC00)
                result.append(chr(cp))
                i += 2
                continue
        result.append(chr(cu))
        i += 1
    return ''.join(result)


def find_evidence_offset(text, evidence_text, search_from=0):
    """查找证据的 UTF-16 偏移，返回 (start, end)，找不到返回 None"""
    idx = text.find(evidence_text, search_from)
    if idx == -1:
        return None

    prefix = text[:idx]
    start_utf16 = utf16_len(prefix)
    end_utf16 = start_utf16 + utf16_len(evidence_text)

    verify = utf16_slice(text, start_utf16, end_utf16)
    if verify != evidence_text:
        total = utf16_len(text)
        ev_len = utf16_len(evidence_text)
        for i in range(search_from, total - ev_len + 1):
            if utf16_slice(text, i, i + ev_len) == evidence_text:
                return (i, i + ev_len)
        return None

    return (start_utf16, end_utf16)
