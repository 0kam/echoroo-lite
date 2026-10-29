"""Loopback-only desktop sidecar. Run with --data and --models."""
import argparse
import json
import multiprocessing
import secrets
import socket
import sys
import threading
import traceback
from datetime import date
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Annotated, Literal

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from storage import CatalogError, checked_source, connect, initialize, preview_folder
from species import decorate_candidates, initialize_species, list_species, save_species
import models as model_registry
from models import Engine, SpeciesEngine, ValidationEngine


class SiteEdit(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=200)
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)


class DatasetEdit(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=200)


class DeleteRequest(BaseModel):
    dataset_id: int | None = None
    revision: str = Field(min_length=64, max_length=64)


class PreviewRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    folder: str
    site_id: int | None = None
    timezone: str


class ImportRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    folder: str
    site_id: int | None = None
    site_name: str = Field(min_length=1, max_length=200)
    dataset_name: str = Field(min_length=1, max_length=200)
    timezone: str
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)


class JobRequest(BaseModel):
    dataset_id: int
    recording_id: int | None = None


class ModelSelection(BaseModel):
    main: str
    extra: list[str]


class ModelBenchmarkRequest(BaseModel):
    engine: str | None = None


class SpeciesRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    scientific_name: str = Field(min_length=1, max_length=200)
    ja: str = Field(default="", max_length=200)
    en: str = Field(default="", max_length=200)


class ReferenceRequest(BaseModel):
    path: str = Field(min_length=1)


class SearchRequest(BaseModel):
    dataset_id: int
    start_date: date | None = None
    end_date: date | None = None
    source_kind: Literal["recording", "reference"]
    source_id: int
    start_s: float = Field(ge=0, allow_inf_nan=False)
    end_s: float = Field(gt=0, allow_inf_nan=False)
    limit: int = Field(default=30, ge=1, le=100)


class ClipRequest(BaseModel):
    source_kind: Literal['recording', 'reference']
    source_id: int
    start_s: float = Field(ge=0, allow_inf_nan=False)
    end_s: float = Field(gt=0, allow_inf_nan=False)


class SearchRunRequest(BaseModel):
    dataset_id: int
    species_id: str
    start_date: date | None = None
    end_date: date | None = None
    clips: list[ClipRequest] = Field(min_length=1, max_length=20)


class ExampleRef(BaseModel):
    kind: Literal['named', 'confirmed']
    origin: Literal['quiz', 'xeno-canto', 'search-reference', 'presence', 'validation']
    id: int | Annotated[str, Field(max_length=100)]


# The examples the reviewer could listen to when answering; stored for reproducibility.
ExamplesSnapshot = Annotated[list[ExampleRef] | None, Field(max_length=50)]


class ExamplesRequest(BaseModel):
    dataset_id: int
    validation_id: int | None = None


class XCKeyRequest(BaseModel):
    key: str = Field(max_length=500, pattern=r'^\S*$')


class DetectionReportRequest(BaseModel):
    dataset_id: int
    engine: Engine
    model_sha256: str
    threshold: float = Field(ge=0, le=1, allow_inf_nan=False)
    start_date: date
    end_date: date
    species_id: str | None = None
    base_report_id: int | None = None


class ScopeRequest(BaseModel):
    dataset_id: int
    species_id: str
    start_date: date
    end_date: date


class ValidationRequest(ScopeRequest):
    model: ValidationEngine
    model_id: int | None = Field(default=None, ge=1)


class ValidationAnswer(BaseModel):
    item_id: int
    verdict: Literal['target', 'other', 'unknown']
    version: int | None = Field(default=None, ge=0)
    examples_snapshot: ExamplesSnapshot = None


class PresenceMark(BaseModel):
    dataset_id: int
    species_id: str
    recording_id: int
    start_s: float = Field(ge=0, allow_inf_nan=False)
    end_s: float = Field(gt=0, allow_inf_nan=False)
    run_id: int | None = None
    marked: bool


class PresenceMiss(ScopeRequest):
    source: Literal['ai', 'search']
    listened: int = Field(ge=1, le=1000)


class ModelLabel(BaseModel):
    segment_id: int
    label: Literal['target', 'other'] | None


class EvidenceSelection(BaseModel):
    recording_id: int
    start_s: float = Field(ge=0, allow_inf_nan=False)
    end_s: float = Field(gt=0, allow_inf_nan=False)


class ReportExportRequest(BaseModel):
    folder: str
    svg: str | None = Field(default=None, max_length=10_000_000)
    png: str | None = Field(default=None, max_length=24_000_000)
    audio: list[EvidenceSelection] = Field(default_factory=list, max_length=200)


class WorkflowExportRequest(BaseModel):
    dataset_id: int
    start_date: date
    end_date: date
    revision: str
    species_ids: list[str] = Field(min_length=1,max_length=1000)
    folder: str
    locale: Literal['ja','en'] = 'en'


class QuizSelection(BaseModel):
    clip_ids: list[int] = Field(min_length=1, max_length=2000)
    count: int = Field(default=10, ge=1, le=50)


class QuizSetRequest(QuizSelection):
    name: str = Field(min_length=1, max_length=200)
    model_config = ConfigDict(str_strip_whitespace=True)


class QuizCredit(BaseModel):
    recordist: str = Field(default='', max_length=200)
    source_url: str = Field(default='', max_length=1000)
    license: str = Field(default='', max_length=1000)


class QuizClipRequest(ClipRequest):
    species_id: str
    sound_type: Literal['song', 'call', 'other'] = 'other'
    note: str = Field(default='', max_length=2000)
    credit: QuizCredit = Field(default_factory=QuizCredit)


class QuizAnswerRequest(BaseModel):
    position: int = Field(ge=0)
    species_id: str | None = None


class QuizEnabledRequest(BaseModel):
    enabled: bool


def create_app(root, models, token):
    model_registry.configure_birdnet(models)
    compatible = initialize(root)
    if compatible:
        initialize_species(root)
        model_registry.configure(root, models)
    cancel = threading.Event()
    operation_cancel = threading.Event()
    lock = threading.Lock()
    worker = None
    gate = threading.Condition()
    requests = 0
    managing = False

    def request_lease():
        nonlocal requests
        with gate:
            gate.wait_for(lambda: not managing)
            requests += 1
        try:
            yield
        finally:
            with gate:
                requests -= 1

    @contextmanager
    def management_change():
        nonlocal managing
        with gate:
            if requests != 1 or (worker and worker.is_alive()):
                raise CatalogError('management_busy')
            managing = True
        try:
            yield
        finally:
            with gate:
                managing = False
                gate.notify_all()

    @asynccontextmanager
    async def lifespan(app):
        from analysis import start_warmup
        start_warmup()
        yield
        cancel.set()
        operation_cancel.set()
        if worker:
            # BirdNET closes its own workers before returning from predict.
            worker.join()

    def authorize(request: Request):
        supplied = request.headers.get("Authorization", "").removeprefix("Bearer ")
        # Audio uses the same short-lived session token; access logging is disabled.
        if request.url.path.endswith(("/audio", "/image")):
            supplied = request.query_params.get("token", supplied)
        if not secrets.compare_digest(supplied, token):
            raise HTTPException(401, "unauthorized")

    def require_compatible(request: Request):
        if not compatible and request.url.path != "/health":
            raise CatalogError("data_incompatible")

    app = FastAPI(lifespan=lifespan, dependencies=[Depends(authorize), Depends(require_compatible), Depends(request_lease)],
                  docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(CatalogError)
    async def catalog_error(request, error):
        return JSONResponse({"detail": str(error)}, status_code=400)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        return JSONResponse({"detail": "invalid_input"}, status_code=422)

    @app.exception_handler(Exception)
    async def unexpected_error(request, error):
        traceback.print_exception(error)
        return JSONResponse({"detail": "unknown_error"}, status_code=500)

    @app.get("/health")
    def health():
        from analysis import birdnet_available
        return {"status": "ok" if compatible else "data_incompatible", "data_folder": str(root),
                "birdnet": birdnet_available(models), "models": model_registry.enabled_engines(root, models),
                "detail": None if compatible else "data_incompatible"}

    @app.get('/models')
    def detection_models():
        return model_registry.list_models(root, models)

    @app.put('/models')
    def select_models(body: ModelSelection):
        with lock:
            return model_registry.set_selection(root, models, body.main, body.extra)

    @app.post('/models/benchmark')
    def benchmark_models(body: ModelBenchmarkRequest | None = None):
        if not lock.acquire(blocking=False):
            raise HTTPException(409, 'job_busy')
        try:
            if worker and worker.is_alive():
                raise HTTPException(409, 'job_busy')
            from model_benchmark import benchmark
            return benchmark(root, models, body.engine if body else None)
        finally:
            lock.release()

    @app.get('/datasets/{dataset_id}/overview')
    def dataset_overview(dataset_id: int, start_date: date | None = None, end_date: date | None = None):
        from results_workflow import overview
        return overview(root,dataset_id,start_date.isoformat() if start_date else None,end_date.isoformat() if end_date else None)

    @app.get('/datasets/{dataset_id}/species/{species_id}')
    def species_overview(dataset_id: int, species_id: str, start_date: date | None = None, end_date: date | None = None,
                         engine: SpeciesEngine = 'auto',
                         model_id: int | None = Query(default=None,ge=1),
                         cell_date: date | None = None, hour: int | None = Query(default=None,ge=0,le=23)):
        from results_workflow import species_result
        return species_result(root,dataset_id,species_id,start_date.isoformat() if start_date else None,
            end_date.isoformat() if end_date else None,engine,cell_date.isoformat() if cell_date else None,hour,model_id)

    @app.get('/datasets/{dataset_id}/exports')
    def workflow_exports(dataset_id: int):
        with connect(root) as db:
            rows=db.execute('SELECT id,path,created_at,snapshot FROM workflow_exports WHERE dataset_id IN (SELECT id FROM datasets WHERE site_id=(SELECT site_id FROM datasets WHERE id=?)) ORDER BY id DESC',(dataset_id,))
            result=[]
            for row in rows:
                snap=json.loads(row['snapshot'])
                if not all(model_registry.result_visible(root, r) for r in snap['results']):
                    continue
                result.append({'id':row['id'],'path':row['path'],'created_at':row['created_at'],'results':snap['results'],
                    'format':snap.get('format'),'folder_path':snap.get('folder_path')})
            return result

    @app.post('/workflow-exports')
    def workflow_export(body: WorkflowExportRequest):
        from workflow_export import export
        with lock:
            operation_cancel.clear()
            try:return export(root,body,operation_cancel)
            except OSError:raise CatalogError('report_write_failed') from None

    @app.post('/workflow-exports/{export_id}/open')
    def workflow_export_open(export_id: int,kind: Literal['document','folder']='document'):
        from workflow_export import open_export
        return open_export(root,export_id,kind)

    @app.get('/datasets/{dataset_id}/species/{species_id}/presence')
    def presence_list(dataset_id: int, species_id: str, source: Literal['ai', 'search'] = 'ai',
                      start_date: date | None = None, end_date: date | None = None):
        from presence import candidates
        return candidates(root, dataset_id, species_id, source, start_date.isoformat() if start_date else None,
                          end_date.isoformat() if end_date else None)

    @app.post('/presence/marks')
    def presence_mark(body: PresenceMark):
        from presence import set_mark
        return set_mark(root, body)

    @app.post('/presence/misses')
    def presence_miss(body: PresenceMiss):
        from presence import record_miss
        return record_miss(root, body)

    @app.get('/datasets/{dataset_id}/species/{species_id}/custom-model')
    def custom_model_readiness(dataset_id: int, species_id: str, start_date: date | None = None, end_date: date | None = None,
                               limit: int = Query(default=30, ge=0, le=200), offset: int = Query(default=0, ge=0)):
        from custom_models import readiness
        from results_workflow import context
        ctx = context(root, dataset_id, start_date.isoformat() if start_date else None, end_date.isoformat() if end_date else None)
        return readiness(root, ctx, species_id, limit, offset)

    @app.post('/search-runs/{run_id}/labels')
    def custom_model_label(run_id: int, body: ModelLabel):
        from custom_models import set_label
        return set_label(root, run_id, body.segment_id, body.label)

    @app.post('/custom-models')
    def custom_model_create(body: ScopeRequest):
        from custom_models import create
        from results_workflow import context
        with lock:
            if worker and worker.is_alive():
                raise HTTPException(409,'job_busy')
            operation_cancel.clear()
            return create(root, context(root, body.dataset_id, body.start_date.isoformat(), body.end_date.isoformat()),
                          body.species_id, operation_cancel)

    @app.get('/custom-models/{model_id}')
    def custom_model_detail(model_id: int):
        from custom_models import get_model
        return get_model(root,model_id)

    @app.post('/custom-models/{model_id}/delete')
    def custom_model_delete(model_id: int):
        from custom_models import delete_model
        with management_change():
            return delete_model(root,model_id)

    @app.post('/validations')
    def validation_start(body: ValidationRequest):
        from validation import start_session
        if (body.model == 'custom') != (body.model_id is not None):
            raise CatalogError('invalid_input')
        with lock:
            return start_session(root,body)

    @app.get('/validations/{session_id}')
    def validation_view(session_id: int, item_id: int | None = None):
        from validation import view_session
        return view_session(root,session_id,item_id=item_id)

    @app.post('/validations/{session_id}/extend')
    def validation_extend(session_id: int):
        from validation import extend
        with lock:
            return extend(root,session_id)

    @app.post('/validations/{session_id}/answers')
    def validation_answer(session_id: int,body: ValidationAnswer):
        from validation import answer
        with lock:
            return answer(root,session_id,body.item_id,body.verdict,body.version,
                          [r.model_dump() for r in body.examples_snapshot] if body.examples_snapshot is not None else None)

    @app.post('/validations/{session_id}/delete')
    def validation_delete(session_id: int):
        from validation import delete_check
        with lock:
            return delete_check(root,session_id)

    @app.get("/catalog")
    def catalog():
        with connect(root) as db:
            return {"sites": [dict(r) for r in db.execute("SELECT * FROM sites")],
                    "datasets": [dict(r) for r in db.execute("SELECT * FROM datasets ORDER BY id DESC")],
                    "jobs": [dict(r) for r in db.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT 20")]}

    @app.get('/sites/{site_id}/management')
    def site_management(site_id: int):
        from management import site_details
        return site_details(root, site_id)

    @app.post('/sites/{site_id}/edit')
    def site_edit(site_id: int, body: SiteEdit):
        from management import edit_site
        with management_change():
            return edit_site(root, site_id, body)

    @app.post('/sites/{site_id}/datasets/{dataset_id}/edit')
    def dataset_edit(site_id: int, dataset_id: int, body: DatasetEdit):
        from management import edit_dataset
        with management_change():
            return edit_dataset(root, site_id, dataset_id, body.name)

    @app.get('/sites/{site_id}/delete-preview')
    def delete_preview(site_id: int, dataset_id: int | None = None):
        from management import preview_delete
        return preview_delete(root, site_id, dataset_id)

    @app.post('/sites/{site_id}/delete')
    def delete_site_data(site_id: int, body: DeleteRequest):
        from management import delete
        with management_change():
            return delete(root, site_id, body.dataset_id, body.revision)

    @app.get("/species")
    def species(q: str = ""):
        return list_species(root, q)

    @app.get('/species/{species_id}')
    def get_species(species_id: str):
        from species import names_for
        with connect(root) as db:
            row=db.execute('SELECT * FROM species WHERE id=?',(species_id,)).fetchone()
            if row is None:
                raise CatalogError('not_found')
            from species import ebird_codes
            return {**names_for(row), 'ebird_code': ebird_codes().get(row['scientific_name'])}

    @app.get('/species/{species_id}/examples')
    def species_examples(species_id: str, dataset_id: int, validation_id: int | None = None):
        from examples import examples
        from quiz import initialize_quiz
        with lock:
            initialize_quiz(root)
        return examples(root, species_id, dataset_id, validation_id)

    @app.post('/species/{species_id}/examples/xeno-canto')
    def species_examples_xc(species_id: str, body: ExamplesRequest):
        from examples import examples, fetch_xeno_canto
        with lock:
            fetch_xeno_canto(root, species_id)
        return examples(root, species_id, body.dataset_id, body.validation_id)

    @app.post("/species")
    def update_species(body: SpeciesRequest):
        return save_species(root, body.scientific_name, {"ja": body.ja, "en": body.en})

    @app.get("/references")
    def references():
        from audio import source
        with connect(root) as db:
            ids = [r[0] for r in db.execute('SELECT id FROM audio_references WHERE id NOT IN (SELECT reference_id FROM quiz_clips) ORDER BY id DESC')]
        return [source(root, 'reference', rid) for rid in ids]

    @app.get('/quiz/library')
    def quiz_library():
        from quiz import initialize_quiz, library
        with lock:
            initialize_quiz(root)
        return library(root)

    @app.post('/quiz/clips')
    def quiz_clip(body: QuizClipRequest):
        from quiz import add_clip
        with lock:
            return add_clip(root, body)

    @app.post('/quiz/clips/{clip_id}/enabled')
    def quiz_enabled(clip_id: int, body: QuizEnabledRequest):
        with connect(root) as db:
            if not db.execute('UPDATE quiz_clips SET enabled=? WHERE id=?', (int(body.enabled), clip_id)).rowcount:
                raise CatalogError('not_found')
        return {'enabled': body.enabled}

    @app.post('/quiz/sets')
    def quiz_set(body: QuizSetRequest):
        from quiz import save_set
        return save_set(root, body)

    @app.post('/quiz/sessions')
    def quiz_start(body: QuizSelection):
        from quiz import start_session
        return start_session(root, body)

    @app.get('/quiz/sessions/{session_id}')
    def quiz_session(session_id: int):
        from quiz import session_view
        return session_view(root, session_id)

    @app.post('/quiz/sessions/{session_id}/answers')
    def quiz_answer(session_id: int, body: QuizAnswerRequest):
        from quiz import answer_question
        return answer_question(root, session_id, body)

    @app.get('/quiz/sessions/{session_id}/questions/{position}/audio')
    def quiz_audio(session_id: int, position: int):
        from quiz import question_audio
        path = question_audio(root, session_id, position)
        return FileResponse(path, media_type='audio/mpeg' if path.suffix == '.mp3' else 'audio/wav')

    @app.get('/quiz/sessions/{session_id}/questions/{position}/spectrogram')
    def quiz_spectrogram(session_id: int, position: int,
                         start_s: float = Query(ge=0, allow_inf_nan=False),
                         end_s: float = Query(gt=0, allow_inf_nan=False),
                         scale: Literal['mel', 'linear'] = 'mel'):
        from quiz import question_source
        from audio import spectrogram
        return spectrogram(question_source(root, session_id, position), start_s, end_s, scale=scale)

    @app.post("/references")
    def reference(body: ReferenceRequest):
        from audio import add_reference
        return add_reference(root, body.path)

    @app.get('/xeno-canto/key')
    def xc_key_status():
        from xeno_canto import key_status
        return key_status(root)

    @app.post('/xeno-canto/key')
    def xc_key_save(body: XCKeyRequest):
        from xeno_canto import save_key
        return save_key(root, body.key)

    @app.get('/xeno-canto/search')
    def xc_search(species_id: str, country: str = Query(default='', max_length=100),
                  quality: Literal['', 'A', 'B', 'C', 'D', 'E'] = '', page: int = Query(default=1, ge=1)):
        from xeno_canto import search
        with connect(root) as db:
            row = db.execute('SELECT scientific_name FROM species WHERE id=?', (species_id,)).fetchone()
        if row is None:
            raise CatalogError('not_found')
        return search(root, row[0], country.strip(), quality, page)

    @app.post('/xeno-canto/{xc_id}/import')
    def xc_import(xc_id: int):
        from xeno_canto import import_recording
        with lock:
            return import_recording(root, xc_id)

    @app.get("/sources/{kind}/{source_id}/audio")
    def source_audio(kind: Literal["recording", "reference"], source_id: int):
        from audio import source
        path = checked_source(source(root, kind, source_id))
        media = {".wav": "audio/wav", ".flac": "audio/flac", ".mp3": "audio/mpeg"}
        return FileResponse(path, media_type=media.get(path.suffix.lower(), "application/octet-stream"))

    @app.get("/sources/{kind}/{source_id}/spectrogram")
    def source_spectrogram(kind: Literal["recording", "reference"], source_id: int,
                           start_s: float = Query(ge=0, allow_inf_nan=False),
                           end_s: float = Query(gt=0, allow_inf_nan=False), detail: bool = False,
                           scale: Literal['mel', 'linear'] = 'mel'):
        from audio import source, spectrogram
        return spectrogram(source(root, kind, source_id), start_s, end_s,
                           max_columns=8000 if detail else 1000, scale=scale)

    @app.post("/search")
    def similarity_search(body: SearchRequest):
        with lock:
            if worker and worker.is_alive():
                raise HTTPException(409, "job_busy")
            from search import search
            operation_cancel.clear()
            return search(root, models, body, operation_cancel)

    @app.post('/search-runs')
    def saved_search(body: SearchRunRequest):
        from search_runs import create_run
        with lock:
            if worker and worker.is_alive():
                raise HTTPException(409, 'job_busy')
            operation_cancel.clear()
            return create_run(root, models, body, operation_cancel)

    @app.get('/search-runs/{run_id}/delete-preview')
    def search_delete_preview(run_id: int):
        from search_runs import delete_preview
        return delete_preview(root, run_id)

    @app.post('/search-runs/{run_id}/delete')
    def search_delete(run_id: int):
        from search_runs import delete_run
        with management_change():
            return delete_run(root, run_id)

    @app.get('/reports/options')
    def report_options(dataset_id: int):
        from reports import options
        return options(root, dataset_id)

    @app.get('/reports')
    def saved_reports(dataset_id: int):
        from reports import list_reports
        return list_reports(root, dataset_id)

    @app.post('/reports/detection')
    def detection_report(body: DetectionReportRequest):
        from reports import detection
        return detection(root, body)

    @app.get('/reports/{report_id}')
    def report(report_id: int, cell_date: date | None = None, hour: int | None = Query(default=None, ge=0, le=23)):
        from reports import get_report
        return get_report(root, report_id, cell_date.isoformat() if cell_date else None, hour)

    @app.post('/reports/{report_id}/export')
    def report_export(report_id: int, body: ReportExportRequest):
        from reports import export_report
        with lock:
            operation_cancel.clear()
            return export_report(root, report_id, body.folder, body.svg, body.png, body.audio, operation_cancel)

    def inspect(body, operation=None):
        zone = body.timezone
        if body.site_id is not None:
            with connect(root) as db:
                row = db.execute("SELECT timezone FROM sites WHERE id=?", (body.site_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "not_found")
            zone = row[0]
        return preview_folder(root, body.folder, zone, operation)

    @app.post("/imports/preview")
    def preview(body: PreviewRequest):
        # Only the folder and time zone matter here, so the folder can be checked before the site is named.
        with lock:
            operation_cancel.clear()
            records, skipped = inspect(body, operation_cancel)
            starts = [r[3] for r in records]
            return {"count": len(records), "seconds": sum(r[4] for r in records),
                    "first": min(starts, default=None), "last": max(starts, default=None), "skipped": skipped}

    @app.post("/imports")
    def import_folder(body: ImportRequest):
        with lock:
            operation_cancel.clear()
            records, skipped = inspect(body, operation_cancel)
            if not records:
                raise CatalogError("no_audio")
            with connect(root) as db:
                if operation_cancel.is_set():raise CatalogError('cancelled')
                site_id = body.site_id
                if site_id is None:
                    site_id = db.execute("INSERT INTO sites(name,timezone,latitude,longitude) VALUES(?,?,?,?)",
                                         (body.site_name.strip(), body.timezone, body.latitude, body.longitude)).lastrowid
                dataset = db.execute("INSERT INTO datasets(site_id,name,folder) VALUES(?,?,?)",
                                     (site_id, body.dataset_name.strip(), str(Path(body.folder).resolve()))).lastrowid
                def rows():
                    for record in records:
                        if operation_cancel.is_set():raise CatalogError('cancelled')
                        yield dataset,*record
                db.executemany("""INSERT INTO recordings(dataset_id,path,original_time,timezone,recorded_at,
                    seconds,sample_rate,size,mtime_ns) VALUES(?,?,?,?,?,?,?,?,?)""",rows())
            return {"dataset_id": dataset, "count": len(records), "skipped": skipped}

    @app.post('/operations/cancel')
    def cancel_operation():
        operation_cancel.set()
        return {'status':'cancelling'}

    @app.get("/datasets/{dataset}/recordings")
    def recordings(dataset: int):
        with connect(root) as db:
            return [dict(r) for r in db.execute("SELECT * FROM site_recordings WHERE scope_id=? ORDER BY recorded_at", (dataset,))]

    @app.get("/recordings/{recording}/audio")
    def audio(recording: int):
        with connect(root) as db:
            row = db.execute("SELECT * FROM recordings WHERE id=?", (recording,)).fetchone()
        if row is None:
            raise HTTPException(404, "not_found")
        return FileResponse(checked_source(row), media_type="audio/wav" if Path(row["path"]).suffix.lower() == ".wav" else "audio/flac")

    @app.get("/recordings/{recording}/results")
    def results(recording: int):
        with connect(root) as db:
            analyses = db.execute("""SELECT * FROM analyses WHERE id IN
                (SELECT MAX(id) FROM analyses WHERE recording_id=? GROUP BY engine)""", (recording,)).fetchall()
            output = []
            for analysis in analyses:
                if analysis['engine'] not in model_registry.selected_engines(root):
                    continue
                meta = json.loads(analysis["metadata"])
                if analysis["engine"] == "perch":
                    from predictions import perch_candidates
                    candidate_filter = meta.get("candidate_filter")
                    seconds = db.execute("SELECT seconds FROM recordings WHERE id=?", (recording,)).fetchone()[0]
                    candidates, display = perch_candidates(analysis["scores_path"], seconds, analysis["id"], candidate_filter)
                    meta = {**meta, **display}
                else:
                    candidates = [dict(r) for r in db.execute(
                        "SELECT * FROM candidates WHERE analysis_id=? ORDER BY score DESC LIMIT 100", (analysis["id"],))]
                output.append({**dict(analysis), "metadata": meta,
                               "candidates": decorate_candidates(db, analysis["engine"], candidates)})
            return output

    @app.post("/jobs")
    def start(body: JobRequest):
        nonlocal worker
        with lock:
            if worker and worker.is_alive():
                raise HTTPException(409, "job_busy")
            with connect(root) as db:
                site = db.execute("SELECT s.* FROM sites s JOIN datasets d ON s.id=d.site_id WHERE d.id=?",
                                  (body.dataset_id,)).fetchone()
                records = [dict(r) for r in db.execute(
                    "SELECT * FROM site_recordings WHERE scope_id=? AND (? IS NULL OR id=?) ORDER BY recorded_at",
                    (body.dataset_id, body.recording_id, body.recording_id))]
                if not site or not records:
                    raise CatalogError("no_audio")
            from analysis import plan
            pending = plan(root, models, records, dict(site))
            records = [record for record in records if record['id'] in pending]
            if not records:
                raise CatalogError('no_audio')
            with connect(root) as db:
                job = db.execute("INSERT INTO jobs(dataset_id,status,total) VALUES(?,'queued',?)",
                                 (body.dataset_id, sum(map(len, pending.values())))).lastrowid
            cancel.clear()
            def work():
                try:
                    from analysis import run
                    run(root, models, job, records, dict(site), cancel, pending)
                except Exception:
                    import traceback
                    traceback.print_exc()
                    with connect(root) as db:
                        db.execute("UPDATE jobs SET status='failed',error='analysis_failed' WHERE id=?", (job,))
            worker = threading.Thread(target=work)
            worker.start()
        return {"id": job}

    @app.post("/jobs/cancel")
    def stop():
        cancel.set()
        return {"status": "cancelling"}

    return CORSMiddleware(app, allow_origins=["http://localhost:1420", "http://127.0.0.1:1420",
                                              "tauri://localhost", "http://tauri.localhost", "https://tauri.localhost"],
                          allow_methods=["GET", "POST", "PUT"], allow_headers=["Authorization", "Content-Type"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--parent-stdin", action="store_true")
    parser.add_argument("--diagnostics", action="store_true", help="Dump Python stacks every minute for stalled-process diagnosis")
    args = parser.parse_args()
    if args.diagnostics:
        import faulthandler
        faulthandler.dump_traceback_later(60, repeat=True)
    from filelock import FileLock
    args.data.mkdir(parents=True, exist_ok=True)
    with FileLock(args.data / ".lock", timeout=0):
        token = secrets.token_urlsafe(32)
        app = create_app(args.data.resolve(), args.models.resolve(), token)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            sock.listen(128)
            server = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False))
            print(json.dumps({"url": f"http://127.0.0.1:{sock.getsockname()[1]}", "token": token}), flush=True)
            if args.parent_stdin:
                def watch_parent():
                    if sys.platform == "win32":
                        import _winapi
                        import msvcrt
                        import time
                        # A pending ReadFile on inherited stdin can stall spawned Python
                        # initialization (python/cpython#78961). Only peek for our stop byte.
                        handle = msvcrt.get_osfhandle(sys.stdin.fileno())
                        try:
                            while not _winapi.PeekNamedPipe(handle)[0]:
                                time.sleep(0.1)
                        except BrokenPipeError:
                            pass  # The desktop exited without sending its stop byte.
                    else:
                        sys.stdin.readline()
                    server.should_exit = True
                threading.Thread(target=watch_parent, daemon=True).start()
            server.run(sockets=[sock])


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
