import json
from uuid import uuid4

from fastapi.testclient import TestClient
import app.main as main

from app.main import create_app
from test_api import pdf_bytes


def test_json_files_and_downloads_survive_restart(tmp_path):
    client = TestClient(create_app(tmp_path))
    upload = client.post('/api/documents', files={
        'file': ('nda.pdf', pdf_bytes(), 'application/pdf'),
    })
    assert upload.status_code == 201
    identifier = upload.json()['document_id']
    folder = tmp_path / identifier
    raw = json.loads((folder / 'extracted.json').read_text(encoding='utf-8'))
    assert 'Confidential information' in raw['segments'][0]['text']

    result = client.get(f'/api/documents/{identifier}/text?method=manual')
    assert result.status_code == 200
    assert json.loads((folder / 'clauses_manual.json').read_text(encoding='utf-8')) == result.json()
    assert json.loads((folder / 'extracted.json').read_text(encoding='utf-8')) == raw
    assert not list(folder.glob('.clauses-*'))

    restarted = TestClient(create_app(tmp_path))
    for kind, expected in [('raw', raw), ('manual', result.json())]:
        download = restarted.get(f'/api/documents/{identifier}/text/download?kind={kind}')
        assert download.status_code == 200
        assert download.json() == expected
        assert download.headers['content-type'] == 'application/json'
        assert 'attachment;' in download.headers['content-disposition']
    assert restarted.get(f'/api/documents/{identifier}/text/download?kind=llm').status_code == 404


def test_download_validation(tmp_path):
    client = TestClient(create_app(tmp_path))
    assert client.get(f'/api/documents/{uuid4()}/text/download').status_code == 404
    assert client.get('/api/documents/invalid/text/download').status_code == 422
    assert client.get(f'/api/documents/{uuid4()}/text/download?kind=invalid').status_code == 422


def test_llm_result_is_saved_separately(tmp_path, monkeypatch):
    client = TestClient(create_app(tmp_path))
    upload = client.post('/api/documents', files={
        'file': ('nda.pdf', pdf_bytes(), 'application/pdf'),
    })
    identifier = upload.json()['document_id']
    manual = client.get(f'/api/documents/{identifier}/text').json()
    monkeypatch.setattr(main, 'process_clauses', lambda **kwargs: {
        'document_id': kwargs['document_id'], 'method': kwargs['method'],
        'clauses': [], 'clause_count': 0, 'warnings': [],
    })
    result = client.get(f'/api/documents/{identifier}/text?method=llm')
    assert result.status_code == 200
    folder = tmp_path / identifier
    assert json.loads((folder / 'clauses_llm.json').read_text(encoding='utf-8')) == result.json()
    assert json.loads((folder / 'clauses_manual.json').read_text(encoding='utf-8')) == manual
