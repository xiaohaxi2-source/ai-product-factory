import io
import zipfile

import pytest
from app.core import Problem
from app.documents import MAX_BYTES, parse_upload, safe_filename
from conftest import create


@pytest.mark.parametrize('filename,content',[('bad.pdf',b'not-a-pdf'),('bad.docx',b'not-a-docx'),('bad.md',b'\x00'*100),('bad.md',b'\xff'*100),('bad.exe',b'text'*100),('bad.md',b'x'*(MAX_BYTES+1)),('bad.md',b'x'*60001)])
def test_upload_limits_and_true_types(filename,content):
    with pytest.raises(Problem):parse_upload(filename,content)


def test_valid_upload_retains_original_and_metadata(env):
    c,a,p=env
    content='# 产品需求\n\n这是一个可以整理读书摘录的本机工具，数据长期保存。'.encode()
    r=c.post('/api/v1/interviews',data={'product_name':'上传测试'},files={'prd_file':('../../PRD.md',content,'text/markdown')})
    assert r.status_code==201,r.text
    iv=r.json()['id'];asset=a.state.store.asset(iv,'upload')
    from pathlib import Path
    assert Path(asset.data['path']).read_bytes()==content
    assert asset.data['filename']=='PRD.md'


def test_docx_archive_requires_word_document():
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w') as z:z.writestr('test.txt','not a Word document')
    with pytest.raises(Problem):parse_upload('test.docx',buffer.getvalue())
