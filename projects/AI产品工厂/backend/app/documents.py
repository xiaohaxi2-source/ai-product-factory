"""上传 PRD 解析：真实类型校验、大小限制、安全文件名、文字提取（不做 OCR）。"""
import re
import zipfile
from io import BytesIO
from pathlib import Path

from .core import Problem

MAX_BYTES = 20 * 1024 * 1024
MAX_CHARS = 60000
ALLOWED = {'.pdf', '.docx', '.md', '.txt'}


def safe_filename(name: str) -> str:
    base = Path(name).name.replace('\\', '_').replace('/', '_')
    base = re.sub(r'[^\w.\-\u4e00-\u9fff]', '_', base).strip('._') or 'upload'
    return base[:80]


def parse_upload(filename: str, content: bytes) -> tuple[str, str]:
    """返回 (安全文件名, 提取的文字)。校验失败抛 Problem(UPLOAD_INVALID)。"""
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED:
        raise Problem('UPLOAD_INVALID', f'只支持 PDF / DOCX / Markdown / TXT，收到 .{ext.lstrip(".") or "未知"}。', 422)
    if len(content) > MAX_BYTES:
        raise Problem('UPLOAD_INVALID', '文件超过 20 MB 限制。', 422)
    name = safe_filename(filename)
    try:
        if ext == '.pdf':
            if not content.startswith(b'%PDF'):
                raise Problem('UPLOAD_INVALID', '文件内容不是有效 PDF（扫描件图片版不支持）。', 422)
            from pypdf import PdfReader
            reader = PdfReader(BytesIO(content))
            if len(reader.pages) > 200:
                raise Problem('UPLOAD_INVALID', 'PDF 超过 200 页，请先拆分。', 422)
            text = '\n'.join(page.extract_text() or '' for page in reader.pages)
        elif ext == '.docx':
            if not zipfile.is_zipfile(BytesIO(content)):
                raise Problem('UPLOAD_INVALID', '文件内容不是有效 DOCX。', 422)
            with zipfile.ZipFile(BytesIO(content)) as archive:
                if sum(z.file_size for z in archive.infolist()) > 60 * 1024 * 1024:
                    raise Problem('UPLOAD_INVALID', 'DOCX 解压体积过大。', 422)
            from docx import Document
            doc = Document(BytesIO(content))
            text = '\n'.join(p.text for p in doc.paragraphs)
        else:
            text = content.decode('utf-8')
            if '\x00' in text:
                raise Problem('UPLOAD_INVALID', '文本包含二进制内容。', 422)
    except UnicodeDecodeError:
        raise Problem('UPLOAD_INVALID', '文本文件必须是 UTF-8 编码。', 422)
    except Problem:
        raise
    except Exception:
        raise Problem('UPLOAD_INVALID', '文件解析失败，可能已损坏或内容类型不符。', 422)
    text = text.strip()
    if len(text) < 20:
        raise Problem('UPLOAD_INVALID', '未能从文件中提取到足够文字（扫描件图片版不支持）。', 422)
    if len(text) > MAX_CHARS:
        raise Problem('UPLOAD_INVALID', '文字超过 60000 字符，请拆分后上传。', 422)
    return name, text
