"""Loopback-only desktop sidecar. Run with --data and --models."""
import argparse
import json
import multiprocessing
import secrets
import socket
import sys
import threading
from datetime import date
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Literal

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from storage import CatalogError, checked_source, connect, initialize, preview_folder
from species import decorate_candidates, initialize_species, list_species, save_species


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


class SearchTarget(BaseModel):
    species_id: str
    clips: list[ClipRequest] = Field(min_length=1, max_length=20)


class SearchRunRequest(BaseModel):
    dataset_id: int
    start_date: date | None = None
    end_date: date | None = None
    targets: list[SearchTarget] = Field(min_length=1, max_length=20)


class SampleRequest(BaseModel):
    low: float = Field(ge=-1, le=1, allow_inf_nan=False)
    high: float = Field(ge=-1, le=1, allow_inf_nan=False)
    count: int = Field(default=20, ge=1, le=200)


class JudgementRequest(BaseModel):
    segment_id: int
    verdict: Literal['target', 'other', 'unknown']
    note: str = Field(default='', max_length=2000)


class XCKeyRequest(BaseModel):
    key: str = Field(max_length=500, pattern=r'^\S*$')


class DetectionReportRequest(BaseModel):
    dataset_id: int
    engine: Literal['birdnet', 'perch']
    model_sha256: str
    threshold: float = Field(ge=0, le=1, allow_inf_nan=False)
    start_date: date
    end_date: date
    species_id: str | None = None
    base_report_id: int | None = None


class SearchReportRequest(BaseModel):
    run_id: int
    start_date: date
    end_date: date
    threshold: float | None = Field(default=None, ge=-1, le=1, allow_inf_nan=False)


class ValidationRequest(BaseModel):
    dataset_id: int
    species_id: str
    start_date: date
    end_date: date
    run_id: int | None = None
    annotation_session_id: int | None = None


class CustomValidationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    model_id: int = Field(ge=1)


class GroupValidationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    dataset_id: int
    species_ids: list[str] = Field(min_length=2, max_length=100)
    start_date: date
    end_date: date
    annotation_session_id: int


class CustomModelRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    annotation_session_id: int = Field(ge=1)
    species_id: str


class AnnotationRange(BaseModel):
    species_id: str | None
    start_s: float = Field(ge=0, allow_inf_nan=False)
    end_s: float = Field(gt=0, allow_inf_nan=False)


class AnnotationRequest(BaseModel):
    dataset_id: int
    start_date: date
    end_date: date


class AnnotationAnswer(BaseModel):
    item_id: int
    version: int = Field(ge=0)
    ranges: list[AnnotationRange] = Field(max_length=200)
    palette_ids: list[str] = Field(default_factory=list, max_length=1000)
    complete: bool = True


class ValidationReview(BaseModel):
    session_id: int
    item_id: int
    verdict: Literal['target', 'other', 'unknown']
    version: int = Field(ge=0)


class ValidationAnswer(BaseModel):
    item_id: int
    verdict: Literal['target', 'other', 'unknown']
    ranges: list[AnnotationRange] | None = Field(default=None, max_length=200)
    version: int | None = Field(default=None, ge=0)
    reviews: list[ValidationReview] | None = Field(default=None, max_length=100)


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
    initialize(root)
    initialize_species(root)
    cancel = threading.Event()
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
        yield
        cancel.set()
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

    app = FastAPI(lifespan=lifespan, dependencies=[Depends(authorize), Depends(request_lease)],
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(CORSMiddleware,
                       allow_origins=["http://localhost:1420", "http://127.0.0.1:1420",
                                      "tauri://localhost", "http://tauri.localhost", "https://tauri.localhost"],
                       allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type"])

    @app.exception_handler(CatalogError)
    async def catalog_error(request, error):
        return JSONResponse({"detail": str(error)}, status_code=400)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        return JSONResponse({"detail": "invalid_input"}, status_code=422)

    @app.get("/health")
    def health():
        return {"status": "ok", "data_folder": str(root)}

    @app.get('/datasets/{dataset_id}/overview')
    def dataset_overview(dataset_id: int, start_date: date | None = None, end_date: date | None = None):
        from results_workflow import overview
        return overview(root,dataset_id,start_date.isoformat() if start_date else None,end_date.isoformat() if end_date else None)

    @app.get('/datasets/{dataset_id}/species/{species_id}')
    def species_overview(dataset_id: int, species_id: str, start_date: date | None = None, end_date: date | None = None,
                         engine: Literal['birdnet','perch','custom','auto'] = 'auto', run_id: int | None = None,
                         model_id: int | None = Query(default=None,ge=1),
                         cell_date: date | None = None, hour: int | None = Query(default=None,ge=0,le=23)):
        from results_workflow import species_result
        return species_result(root,dataset_id,species_id,start_date.isoformat() if start_date else None,
            end_date.isoformat() if end_date else None,engine,run_id,cell_date.isoformat() if cell_date else None,hour,model_id)

    @app.get('/datasets/{dataset_id}/exports')
    def workflow_exports(dataset_id: int):
        with connect(root) as db:
            rows=db.execute('SELECT id,path,created_at,snapshot FROM workflow_exports WHERE dataset_id IN (SELECT id FROM datasets WHERE site_id=(SELECT site_id FROM datasets WHERE id=?)) ORDER BY id DESC',(dataset_id,))
            result=[]
            for row in rows:
                snap=json.loads(row['snapshot'])
                result.append({'id':row['id'],'path':row['path'],'created_at':row['created_at'],'results':snap['results'],
                    'format':snap.get('format'),'folder_path':snap.get('folder_path')})
            return result

    @app.post('/workflow-exports')
    def workflow_export(body: WorkflowExportRequest):
        from workflow_export import export
        with lock:
            try:return export(root,body)
            except OSError:raise CatalogError('report_write_failed') from None

    @app.post('/workflow-exports/{export_id}/open')
    def workflow_export_open(export_id: int,kind: Literal['document','folder']='document'):
        from workflow_export import open_export
        return open_export(root,export_id,kind)

    @app.post('/annotations')
    def annotation_start(body: AnnotationRequest):
        from annotations import start_session
        with lock:
            return start_session(root, body)

    @app.get('/annotations/{session_id}')
    def annotation_view(session_id: int, item_id: int | None = None):
        from annotations import view_session
        return view_session(root, session_id, item_id)

    @app.post('/annotations/{session_id}/answers')
    def annotation_answer(session_id: int, body: AnnotationAnswer):
        from annotations import save_answer
        with lock:
            return save_answer(root, session_id, body)

    @app.get('/annotations/{session_id}/custom-model-readiness')
    def custom_model_readiness(session_id: int, species_id: str):
        from custom_models import readiness
        return readiness(root,session_id,species_id)

    @app.post('/custom-models')
    def custom_model_create(body: CustomModelRequest):
        from custom_models import create
        with lock:
            if worker and worker.is_alive():
                raise HTTPException(409,'job_busy')
            return create(root,body.annotation_session_id,body.species_id)

    @app.get('/custom-models/{model_id}')
    def custom_model_detail(model_id: int):
        from custom_models import get_model
        return get_model(root,model_id)

    @app.post('/validations')
    def validation_start(body: ValidationRequest | CustomValidationRequest | GroupValidationRequest):
        from validation import start_session
        with lock:
            return start_session(root,body)

    @app.get('/validations/{session_id}')
    def validation_view(session_id: int, item_id: int | None = None):
        from validation import view_session
        return view_session(root,session_id,item_id=item_id)

    @app.post('/validations/{session_id}/evaluation')
    def validation_evaluation(session_id: int):
        from validation import finish_calibration
        with lock:
            return finish_calibration(root,session_id)

    @app.post('/validations/{session_id}/recall')
    def validation_recall(session_id: int):
        from validation import start_recall
        with lock:
            return start_recall(root,session_id)

    @app.post('/validations/{session_id}/answers')
    def validation_answer(session_id: int,body: ValidationAnswer):
        from validation import answer
        with lock:
            return answer(root,session_id,body.item_id,body.verdict,
                          [r.model_dump() for r in body.ranges] if body.ranges is not None else None, body.version,
                          [r.model_dump() for r in body.reviews] if body.reviews is not None else None)

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
            return names_for(row)

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

    @app.get('/quiz/clips/{clip_id}/image')
    def quiz_image(clip_id: int):
        from quiz import clip_image
        return FileResponse(clip_image(root, clip_id), media_type='image/jpeg')

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
                         end_s: float = Query(gt=0, allow_inf_nan=False)):
        from quiz import question_source
        from audio import spectrogram
        return spectrogram(question_source(root, session_id, position), start_s, end_s)

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
                           end_s: float = Query(gt=0, allow_inf_nan=False), detail: bool = False):
        from audio import source, spectrogram
        return spectrogram(source(root, kind, source_id), start_s, end_s, max_columns=8000 if detail else 1000)

    @app.post("/search")
    def similarity_search(body: SearchRequest):
        with lock:
            if worker and worker.is_alive():
                raise HTTPException(409, "job_busy")
            from search import search
            return search(root, models, body)

    @app.get('/search-runs')
    def saved_searches(dataset_id: int):
        from search_runs import list_runs
        return list_runs(root, dataset_id)

    @app.post('/search-runs')
    def saved_search(body: SearchRunRequest):
        from search_runs import create_runs
        with lock:
            if worker and worker.is_alive():
                raise HTTPException(409, 'job_busy')
            return create_runs(root, models, body)

    @app.get('/search-runs/{run_id}')
    def saved_search_detail(run_id: int, sample_id: int | None = None):
        from search_runs import get_run
        return get_run(root, run_id, sample_id)

    @app.post('/search-runs/{run_id}/samples')
    def sample_search(run_id: int, body: SampleRequest):
        from search_runs import draw_sample
        return draw_sample(root, run_id, body.low, body.high, body.count)

    @app.post('/search-runs/{run_id}/judgements')
    def judge_search(run_id: int, body: JudgementRequest):
        from search_runs import judge
        return judge(root, run_id, body.segment_id, body.verdict, body.note)

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

    @app.post('/reports/search')
    def similarity_report(body: SearchReportRequest):
        from reports import search_report
        return search_report(root, body)

    @app.get('/reports/{report_id}')
    def report(report_id: int, cell_date: date | None = None, hour: int | None = Query(default=None, ge=0, le=23)):
        from reports import get_report
        return get_report(root, report_id, cell_date.isoformat() if cell_date else None, hour)

    @app.post('/reports/{report_id}/export')
    def report_export(report_id: int, body: ReportExportRequest):
        from reports import export_report
        return export_report(root, report_id, body.folder, body.svg, body.png, body.audio)

    def inspect(body):
        zone = body.timezone
        if body.site_id is not None:
            with connect(root) as db:
                row = db.execute("SELECT timezone FROM sites WHERE id=?", (body.site_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "not_found")
            zone = row[0]
        return preview_folder(root, body.folder, zone)

    @app.post("/imports/preview")
    def preview(body: ImportRequest):
        records, skipped = inspect(body)
        return {"count": len(records), "seconds": sum(r[4] for r in records),
                "first": records[0][3] if records else None, "skipped": skipped}

    @app.post("/imports")
    def import_folder(body: ImportRequest):
        records, skipped = inspect(body)
        if not records:
            raise CatalogError("no_audio")
        with connect(root) as db:
            site_id = body.site_id
            if site_id is None:
                site_id = db.execute("INSERT INTO sites(name,timezone,latitude,longitude) VALUES(?,?,?,?)",
                                     (body.site_name.strip(), body.timezone, body.latitude, body.longitude)).lastrowid
            dataset = db.execute("INSERT INTO datasets(site_id,name,folder) VALUES(?,?,?)",
                                 (site_id, body.dataset_name.strip(), str(Path(body.folder).resolve()))).lastrowid
            db.executemany("""INSERT INTO recordings(dataset_id,path,original_time,timezone,recorded_at,
                seconds,sample_rate,size,mtime_ns) VALUES(?,?,?,?,?,?,?,?,?)""",
                           ((dataset, *r) for r in records))
        return {"dataset_id": dataset, "count": len(records), "skipped": skipped}

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
    def results(recording: int, perch_geo: bool = True):
        with connect(root) as db:
            analyses = db.execute("""SELECT * FROM analyses WHERE id IN
                (SELECT MAX(id) FROM analyses WHERE recording_id=? GROUP BY engine)""", (recording,)).fetchall()
            output = []
            for analysis in analyses:
                meta = json.loads(analysis["metadata"])
                if analysis["engine"] == "perch":
                    from predictions import perch_candidates
                    geo_filter = meta.get("geo_filter")
                    if geo_filter is None:
                        # Old runs saved the region only with BirdNET. Use the SAME run and recording.
                        bird = db.execute("SELECT metadata FROM analyses WHERE job_id=? AND recording_id=? AND engine='birdnet'",
                                          (analysis["job_id"], recording)).fetchone()
                        bird_meta = json.loads(bird[0]) if bird else {}
                        if "species" in bird_meta:
                            geo_filter = {key: bird_meta[key] for key in
                                          ("geo_sha256", "latitude", "longitude", "week", "geo_threshold", "species") if key in bird_meta}
                    seconds = db.execute("SELECT seconds FROM recordings WHERE id=?", (recording,)).fetchone()[0]
                    candidates, display = perch_candidates(analysis["scores_path"], seconds, analysis["id"], geo_filter, perch_geo)
                    meta = {**meta, **display, "geo_filter": geo_filter}
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
                if body.recording_id is None and records:
                    versions=db.execute("""SELECT a.engine,COUNT(DISTINCT json_extract(a.metadata,'$.model_sha256')) AS versions
                        FROM analyses a JOIN site_recordings r ON r.id=a.recording_id WHERE r.scope_id=?
                        AND a.id IN (SELECT MAX(id) FROM analyses GROUP BY recording_id,engine) GROUP BY a.engine""",(body.dataset_id,)).fetchall()
                    if not any(row['versions']>1 for row in versions):
                        from management import location_status
                        current_site = dict(site)
                        current_site['location_changed'] = bool(db.execute('SELECT 1 FROM changed_locations WHERE site_id=?',(site['id'],)).fetchone())
                        completed, _ = location_status(db, current_site)
                        records=[record for record in records if record['id'] not in completed]
                if not site or not records:
                    raise CatalogError("no_audio")
                job = db.execute("INSERT INTO jobs(dataset_id,status,total) VALUES(?,'queued',?)",
                                 (body.dataset_id, len(records) * 2)).lastrowid
            cancel.clear()
            def work():
                try:
                    from analysis import run
                    run(root, models, job, records, dict(site), cancel)
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

    return app


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
