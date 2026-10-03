import pytest
from chronos.native_pdf_import import import_native_pdf
from chronos.pdf_store import PdfStore

def test_native_import_preserves_source_and_deduplicates(tmp_path):
    root=tmp_path / 'downloads'
    root.mkdir()
    name='a'*32+'.pdf'
    data=b'%PDF-1.7\n'+b'x'*40+b'\n%%EOF'
    (root/name).write_bytes(data)
    store=PdfStore(tmp_path/'store')
    catalog=[dict(course_id='2',source_id='1',activity_id='3',filename='lecture.pdf',uploaded_at=None)]
    payload=dict(course_id='2',source_id='1',basename=name,byte_count=len(data))
    first=import_native_pdf(payload,root,store,catalog)
    assert import_native_pdf(payload,root,store,catalog)==first
    assert store.read('2','1',expected_sha256=first['sha256'])==data
    assert (root/name).exists()
    for change in [dict(basename='../outside.pdf'),dict(source_id='9'),dict(byte_count=True),dict(byte_count=100)]:
        with pytest.raises(ValueError):
            import_native_pdf({**payload,**change},root,store,catalog)
    with pytest.raises(ValueError):
        import_native_pdf(payload,None,store,catalog)
