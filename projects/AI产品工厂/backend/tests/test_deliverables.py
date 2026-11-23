import io
from pathlib import Path
import zipfile

from app.store import Interview
from conftest import create
from test_execution import executing, start, current
from test_planning import planning, closed, generate


def test_overview_and_documents_preview_download_are_project_scoped(planning):
    c, app, root = planning
    iv = closed(c)
    generate(c, iv)
    rows = c.get('/api/v1/workspace').json()
    assert rows[0]['id'] == iv and rows[0]['stage'] == 'plan' and not rows[0]['running']
    files = c.get(f'/api/v1/interviews/{iv}/artifacts').json()
    assert {'prd', 'checklist', 'decisions', 'technical'} <= {f['id'] for f in files}
    prd = c.get(f'/api/v1/interviews/{iv}/artifacts/prd').json()
    assert prd['name'] == 'PRD.md' and prd['content'].startswith('# PRD')
    download = c.get(prd['download_url'])
    assert download.status_code == 200 and download.text == prd['content']
    assert 'attachment' in download.headers['content-disposition']
    assert c.get(f'/api/v1/interviews/{iv}/artifacts/secret.env').status_code == 404
    assert c.get('/api/v1/interviews/9999/artifacts').status_code == 404
    with app.state.store.Session.begin() as session:
        session.get(Interview, iv).owner_id = 'another-owner'
    assert c.get(f'/api/v1/interviews/{iv}/artifacts/prd').status_code == 404
    assert c.get('/api/v1/workspace').json() == []


def test_code_zip_contains_only_immutable_source_manifest(executing):
    c, app, root, iv, body, gateway = executing
    assert start(executing)['status'] == 'succeeded'
    run = current(executing)
    response = c.get(f'/api/v1/executions/{run["id"]}/download')
    assert response.status_code == 200 and response.headers['content-type'] == 'application/zip'
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert set(archive.namelist()) == set(app.state.store.execution(run['id']).data['manifest'])
        assert 'backend/app/t1.py' in archive.namelist()
        assert not any('.env' in name or 'data/' in name or name.startswith('/') for name in archive.namelist())
    path = app.state.jobs.executions.revision_path(run['id'], run['revision'])/'backend/app/t1.py'
    path.write_text('INDEPENDENT_CHANGE = True\n')
    assert c.get(f'/api/v1/executions/{run["id"]}/download').status_code == 409
    assert c.get('/api/v1/executions/00000000-0000-0000-0000-000000000000/download').status_code == 404


def test_local_documents_are_real_stable_files_and_independent_changes_are_preserved(planning):
    c, app, root = planning
    iv = closed(c)
    generate(c, iv)
    response = c.get(f'/api/v1/interviews/{iv}/files')
    assert response.status_code == 200
    listing = response.json()
    assert Path(listing['document_directory']).is_relative_to(Path(app.state.store.settings.data_dir))
    prd = next(f for f in listing['files'] if f['artifact_id'] == 'prd')
    path = Path(prd['local_path'])
    assert path.is_file() and path.read_text().startswith('# PRD')
    assert c.get(prd['download_url']).content == path.read_bytes()
    again = c.get(f'/api/v1/interviews/{iv}/files').json()
    assert next(f for f in again['files'] if f['artifact_id'] == 'prd') == prd
    path.write_text('My independent edits')
    assert c.get(f'/api/v1/interviews/{iv}/files').json()['error']['code'] == 'LOCAL_FILE_CHANGED'
    assert path.read_text() == 'My independent edits'
    with app.state.store.Session.begin() as session:
        session.get(Interview, iv).owner_id = 'another-owner'
    assert c.get(f'/api/v1/interviews/{iv}/files').status_code == 404


def test_local_code_listing_matches_committed_manifest_and_detects_tampering(executing):
    c, app, root, iv, body, gateway = executing
    assert c.get(f'/api/v1/interviews/{iv}/files').json()['code_directory'] == ''
    assert start(executing)['status'] == 'succeeded'
    run = current(executing)
    listing = c.get(f'/api/v1/interviews/{iv}/files').json()
    code = [f for f in listing['files'] if f['kind'] == 'code']
    assert {f['name'] for f in code} == set(app.state.store.execution(run['id']).data['manifest'])
    assert listing['code_directory'] == run['workspace']
    for item in code:
        assert Path(item['local_path']).is_file()
        assert Path(item['local_path']).stat().st_size == item['bytes']
    Path(code[0]['local_path']).write_text('CHANGED = True')
    assert c.get(f'/api/v1/interviews/{iv}/files').status_code == 409


def test_document_snapshot_rejects_symlink_destination(planning):
    c, app, root = planning
    iv = closed(c)
    folder = Path(app.state.store.settings.data_dir) / 'deliverables'
    elsewhere = root / 'outside'
    elsewhere.mkdir()
    folder.symlink_to(elsewhere, target_is_directory=True)
    response = c.get(f'/api/v1/interviews/{iv}/files')
    assert response.status_code == 409 and response.json()['error']['code'] == 'LOCAL_FILE_UNSAFE'
    assert list(elsewhere.iterdir()) == []
