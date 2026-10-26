#!/usr/bin/env python3
"""Real, disposable-catalog analysis benchmarks; no downloads or user-catalog writes.

Example: .venv/bin/python scripts/bench_analysis.py --work-dir /tmp/analysis-bench
Each trial runs in a fresh process. Imports are warmed; model loading is timed.
Fixtures and numeric reference snapshots persist in work-dir; catalogs are deleted.
"""
import argparse
import cProfile
from collections import defaultdict
from contextlib import contextmanager, ExitStack
from datetime import datetime, timedelta
import hashlib
import inspect
import json
import os
from pathlib import Path
import platform
import sqlite3
import subprocess
import sys
import tempfile
import threading
from time import perf_counter
from unittest.mock import patch

os.environ['ORT_DISABLE_TELEMETRY'] = '1'
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO/'apps/sidecar'))
import numpy as np
import soundfile as sf

SHAPES = {'long':(1,3600,48000,'WAV'), 'short':(120,30,48000,'WAV'), 'flac':(20,300,44100,'FLAC')}
ENGINE_SETS = {'perch':['perch'], 'kinglet-s':['kinglet-s'], 'kinglet-xs':['kinglet-xs'], 'dual':['perch','birdnet'],
               'all':['perch','birdnet','birdnet-v3','kinglet-s','kinglet-xs'],
               'onnx':['perch','birdnet-v3','kinglet-s','kinglet-xs']}
VARIANTS = ['current', *[f'threads-{n}' for n in (1,2,4,6,8,12,0)], 'inter-2', 'parallel',
            'graph-disabled','graph-basic','graph-extended','arena-off','no-spin', 'coreml',
            *[f'batch-{n}' for n in (1,2,4,8,16,32,64)],'batch16-no-arena','batch32-no-arena', 'no-copies', 'fft', 'poly-fast',
            'blocks', 'prefetch', 'npz-plain', 'npz-level1', 'sqlite-normal', 'sqlite-delete',
            'sqlite-bulk', 'sqlite-reuse', 'cached-hashes', 'reuse-scores', 'birdnet-batch8', 'birdnet-workers4']


def fixtures(folder, shape, scale):
    """Nonrepeating ambient noise, insects, wind and a locally bundled bird call."""
    from scipy.signal import lfilter, resample_poly
    count, seconds, rate, fmt = SHAPES[shape]
    count = max(1,round(count*scale)) if count>1 else count
    seconds = max(5,round(seconds*scale)) if shape=='long' else seconds
    folder.mkdir(parents=True,exist_ok=True)
    signature = dict(version=1,count=count,seconds=seconds,rate=rate,format=fmt,seed=20261026)
    manifest = folder/'fixture.json'
    if manifest.exists() and json.loads(manifest.read_text())==signature:
        return sorted(folder.glob('*.wav' if fmt=='WAV' else '*.flac'))
    call, call_rate = sf.read(REPO/'scripts/fixtures/horornis-diphone-xc1177906.mp3',dtype='float32')
    if call.ndim==2: call=call.mean(axis=1)
    import math
    divisor=math.gcd(rate,call_rate)
    call=resample_poly(call,rate//divisor,call_rate//divisor)
    call=call/max(float(np.max(np.abs(call))),1e-6)
    rng=np.random.default_rng(20261026)
    state=np.zeros(1); paths=[]; position=0
    for index in range(count):
        stamp=(datetime(2024,8,8,6)+timedelta(seconds=index*seconds)).strftime('%Y%m%d_%H%M%S')
        path=folder/f'BENCH_{stamp}.{fmt.lower()}'
        with sf.SoundFile(path,'w',samplerate=rate,channels=1,format=fmt,subtype='PCM_16') as output:
            for start in range(0,seconds,30):
                size=min(30,seconds-start)*rate
                white=rng.normal(0,.008,size).astype(np.float32)
                wind,state=lfilter([.02],[1,-.98],white,zi=state)
                t=(np.arange(size,dtype=np.float64)+position)/rate
                samples=white+wind*4+.002*np.sin(2*np.pi*6100*t)*(1+np.sin(2*np.pi*.7*t))
                # Vary call amplitude, including weak calls near detection thresholds.
                offset=rate*3
                length=min(len(call),size-offset)
                if length>0: samples[offset:offset+length]+=call[:length]*float(rng.choice([.002,.01,.05,.2]))
                output.write(np.clip(samples,-.98,.98).astype(np.float32))
                position+=size
        paths.append(path)
    manifest.write_text(json.dumps(signature,sort_keys=True))
    return paths


class Stages:
    def __init__(self):
        self.seconds=defaultdict(float); self.calls=defaultdict(int); self.local=threading.local(); self.lock=threading.Lock()

    @contextmanager
    def time(self,name):
        stack=getattr(self.local,'stack',None)
        if stack is None: stack=self.local.stack=[]
        entry=[perf_counter(),0.]; stack.append(entry)
        try: yield
        finally:
            elapsed=perf_counter()-entry[0]; stack.pop()
            if stack: stack[-1][1]+=elapsed
            with self.lock:
                self.seconds[name]+=elapsed-entry[1]; self.calls[name]+=1

    def wrap(self,name,function):
        def timed(*args,**kwargs):
            with self.time(name): return function(*args,**kwargs)
        return timed


class Database:
    """Script-local SQLite instrumentation; retains transactions and extension loading."""
    def __init__(self,raw,stages,variant):
        object.__setattr__(self,'raw',raw); object.__setattr__(self,'stages',stages); object.__setattr__(self,'variant',variant)
    def __getattr__(self,key): return getattr(self.raw,key)
    def __setattr__(self,key,value): setattr(self.raw,key,value)
    def __enter__(self): self.raw.__enter__(); return self
    def __exit__(self,*args):
        with self.stages.time('db_commit'): return self.raw.__exit__(*args)
    def close(self):
        with self.stages.time('db_close'): return self.raw.close()
    def execute(self,sql,*args):
        name='embeddings_vec' if sql.startswith('INSERT INTO vectors') or sql.startswith('INSERT INTO segments') else 'db_writes' if sql.lstrip().split()[0].upper() in ('INSERT','UPDATE','DELETE','CREATE') else 'db_reads'
        with self.stages.time(name): return self.raw.execute(sql,*args)
    def executemany(self,sql,rows):
        name='embeddings_vec' if sql.startswith('INSERT INTO vectors') or sql.startswith('INSERT INTO segments') else 'db_writes'
        with self.stages.time(name):
            if self.variant=='sqlite-bulk' and sql.startswith('INSERT INTO vectors'):
                rows=list(rows)
                for start in range(0,len(rows),256):
                    chunk=rows[start:start+256]
                    self.raw.execute(sql.replace('VALUES(?,?)','VALUES'+','.join(['(?,?)']*len(chunk))),
                                     [value for row in chunk for value in row])
                return
            return self.raw.executemany(sql,rows)


def replace_function(function,old,new):
    source=inspect.getsource(function)
    if old not in source: raise ValueError('Experiment no longer matches source: '+old)
    namespace=dict(function.__globals__)
    exec(compile(source.replace(old,new),'<benchmark experiment>','exec'),namespace)
    return namespace[function.__name__]


@contextmanager
def instruments(stages,variant,models,scratch):
    import analysis, reports, results_workflow, storage
    import onnxruntime as ort
    with ExitStack() as stack:
        if variant=='prefetch':
            with storage.connect(scratch) as db:
                recording_paths=[Path(r[0]) for r in db.execute('SELECT path FROM recordings ORDER BY recorded_at')]
        original_connect=sqlite3.connect
        def opening(*args,**kwargs):
            with stages.time('db_open'): raw=original_connect(*args,**kwargs)
            if variant=='sqlite-normal': raw.execute('PRAGMA synchronous=NORMAL')
            if variant=='sqlite-delete': raw.execute('PRAGMA journal_mode=DELETE')
            return Database(raw,stages,variant)
        stack.enter_context(patch('sqlite3.connect',opening))
        infer=analysis.infer_onnx
        if variant.startswith('batch-') or variant in ('batch16-no-arena','batch32-no-arena'):
            size=int(variant.split('-')[1]) if variant.startswith('batch-') else int(variant[5:7])
            infer=replace_function(infer,"batch_size = 1 if engine == 'perch' else 32",f'batch_size = {size}')
        if variant=='no-copies':
            infer=replace_function(infer,"np.pad(chunk, (0, -len(chunk) % 160000)).reshape(-1, 160000).astype(np.float32)",
                "(np.pad(chunk, (0, -len(chunk) % 160000)) if len(chunk)%160000 else chunk).reshape(-1,160000).astype(np.float32,copy=False)")
        stack.enter_context(patch('analysis.infer_onnx',lambda detector,engine,*a,**kw:
            stages.wrap('inference.'+engine,infer)(detector,engine,*a,**kw)))
        original_read=sf.read
        stack.enter_context(patch('analysis.sf.read',stages.wrap('decode',original_read)))
        resample=analysis.resample
        if variant=='fft':
            from scipy.signal import resample as fft
            resample=lambda audio,rate: audio if rate==32000 else fft(audio,round(len(audio)*32000/rate))
        if variant=='poly-fast':
            from scipy.signal import resample_poly
            import math
            resample=lambda audio,rate: audio if rate==32000 else resample_poly(audio,32000//math.gcd(rate,32000),rate//math.gcd(rate,32000),window=('kaiser',3.))
        stack.enter_context(patch('analysis.resample',stages.wrap('resample',resample)))
        if variant=='prefetch':
            from concurrent.futures import ThreadPoolExecutor
            pool=stack.enter_context(ThreadPoolExecutor(max_workers=1,thread_name_prefix='audio-prefetch'))
            raw_read=original_read; cached={}; future=None; next_index=0
            def prepare(path):
                with stages.time('decode'): native,rate=raw_read(path,dtype='float32')
                with stages.time('resample'): converted=resample(native,rate)
                return native,rate,converted
            def prefetched(path,**kwargs):
                nonlocal future,next_index
                if future is None: future=pool.submit(prepare,recording_paths[next_index]); next_index+=1
                with stages.time('decode_wait'): native,rate,converted=future.result()
                future=None
                if next_index<len(recording_paths):
                    future=pool.submit(prepare,recording_paths[next_index]); next_index+=1
                cached[id(native)]=converted
                return native,rate
            stack.enter_context(patch('analysis.sf.read',prefetched))
            stack.enter_context(patch('analysis.resample',lambda audio,rate:cached.pop(id(audio))))
        if variant=='blocks':
            def blocked(path,**kwargs): return np.concatenate(list(sf.blocks(path,blocksize=48000*30,**kwargs))),sf.info(path).samplerate
            stack.enter_context(patch('analysis.sf.read',stages.wrap('decode',blocked)))
        original_npz=np.savez_compressed
        def npz(file,**arrays):
            name=str(file if isinstance(file,(str,os.PathLike)) else file.name)
            raw='analyses' in Path(name).parts and '.windows.npz' not in name
            with stages.time('saving_npz' if raw else 'derived_npz'):
                if raw and variant=='npz-plain': return np.savez(file,**arrays)
                if raw and variant=='npz-level1':
                    import zipfile
                    with zipfile.ZipFile(file,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=1) as archive:
                        for key,value in arrays.items():
                            with archive.open(key+'.npy','w',force_zip64=True) as stream:
                                np.lib.format.write_array(stream,np.asanyarray(value),allow_pickle=False)
                    return
                return original_npz(file,**arrays)
        stack.enter_context(patch('numpy.savez_compressed',npz))
        save=analysis.save
        if variant=='reuse-scores':
            original_load=np.load
            def reuse(root,job,record,engine,meta,scores,*args,**kwargs):
                from contextlib import nullcontext
                target=Path(root)/'analyses'/str(job)/f"{record['id']}-{engine}.npz"
                def loading(path,*a,**kw):
                    return nullcontext(scores) if Path(path)==target else original_load(path,*a,**kw)
                with patch('numpy.load',loading): return save(root,job,record,engine,meta,scores,*args,**kwargs)
            saving=reuse
        else: saving=save
        stack.enter_context(patch('analysis.save',stages.wrap('saving_other',saving)))
        stack.enter_context(patch('reports.classification_windows',stages.wrap('result_windows',reports.classification_windows)))
        stack.enter_context(patch('results_workflow.overview',stages.wrap('overview',results_workflow.overview)))
        original_prepare=analysis.prepare_models
        @contextmanager
        def preparing(*args,**kwargs):
            context=original_prepare(*args,**kwargs)
            with stages.time('model_loading'): loaded=context.__enter__()
            try:
                with ExitStack() as sessions:
                    if 'birdnet' in loaded:
                        session,names,meta=loaded['birdnet']
                        sessions.enter_context(patch.object(session,'run_arrays',stages.wrap('inference.birdnet',session.run_arrays)))
                    yield loaded
            finally:
                with stages.time('model_cleanup'): context.__exit__(*sys.exc_info())
        stack.enter_context(patch('analysis.prepare_models',preparing))
        if variant.startswith(('threads-','graph-')) or variant in ('inter-2','parallel','arena-off','no-spin','coreml','batch16-no-arena','batch32-no-arena'):
            def load(directory,engine):
                from models import REGISTRY
                options=ort.SessionOptions(); options.intra_op_num_threads=8; options.inter_op_num_threads=1
                if variant.startswith('threads-'): options.intra_op_num_threads=int(variant.split('-')[1])
                if variant=='inter-2': options.inter_op_num_threads=2
                if variant=='parallel': options.execution_mode=ort.ExecutionMode.ORT_PARALLEL
                if variant in ('arena-off','batch16-no-arena','batch32-no-arena'): options.enable_cpu_mem_arena=False
                if variant=='no-spin': options.add_session_config_entry('session.intra_op.allow_spinning','0')
                levels=dict(disabled=ort.GraphOptimizationLevel.ORT_DISABLE_ALL,basic=ort.GraphOptimizationLevel.ORT_ENABLE_BASIC,extended=ort.GraphOptimizationLevel.ORT_ENABLE_EXTENDED)
                if variant.startswith('graph-'): options.graph_optimization_level=levels[variant.split('-')[1]]
                providers=['CPUExecutionProvider']
                if variant=='coreml':
                    if 'CoreMLExecutionProvider' not in ort.get_available_providers(): raise RuntimeError('CoreML EP is not installed')
                    providers=[('CoreMLExecutionProvider',{'ModelCacheDirectory':str(scratch/'coreml'),'MLComputeUnits':'ALL'}),'CPUExecutionProvider']
                path=Path(directory)/REGISTRY[engine]['files'][0]
                return ort.InferenceSession(str(path),options,providers=providers),analysis.digest(path)
            stack.enter_context(patch('analysis.load_onnx',load))
        if variant=='cached-hashes':
            from models import file_sha256
            stack.enter_context(patch('analysis.digest',file_sha256))
        if variant.startswith('birdnet-'):
            from models import configure_birdnet
            configure_birdnet(models)
            import birdnet
            original_load=birdnet.load
            def load_birdnet(kind,*args,**kwargs):
                model=original_load(kind,*args,**kwargs)
                if kind=='acoustic':
                    original_session=model.predict_session
                    def session(**kwargs):
                        if variant=='birdnet-batch8': kwargs['batch_size']=8
                        else: kwargs['n_workers']=4
                        return original_session(**kwargs)
                    model.predict_session=session
                return model
            stack.enter_context(patch.object(birdnet,'load',load_birdnet))
        if variant=='sqlite-reuse':
            original_connect_context=storage.connect
            @contextmanager
            def reused(root):
                if Path(root)!=scratch:
                    with original_connect_context(root) as db: yield db
                else:
                    with connection: yield connection
            with original_connect_context(scratch) as connection:
                # Also replace aliases imported before storage.connect was patched.
                for module in list(sys.modules.values()):
                    if getattr(module,'__file__',None) and Path(module.__file__).parent==REPO/'apps/sidecar':
                        if getattr(module,'connect',None) is original_connect_context:
                            stack.enter_context(patch.object(module,'connect',reused))
                yield
            return
        yield


def snapshot(root,target):
    from storage import connect
    from reports import classification_windows, candidate_list
    arrays=defaultdict(list)
    with connect(root) as db:
        for row in db.execute('''SELECT a.*,r.seconds FROM analyses a JOIN recordings r ON r.id=a.recording_id
            ORDER BY r.recorded_at,a.engine'''):
            engine=row['engine']
            with np.load(row['scores_path'],allow_pickle=False) as saved:
                scores=saved['logits' if engine=='perch' else 'scores']
                if scores.ndim==1:
                    order=np.lexsort((saved['labels'],saved['start_s']))
                    scores=scores[order]
                    starts=saved['start_s'][order]; names=saved['labels'][order]
                    bounds=np.r_[0,np.flatnonzero(np.diff(starts))+1,len(scores)]
                    arrays[engine+'.top1'].append(np.array([names[first+np.argmax(scores[first:last])]
                        for first,last in zip(bounds[:-1],bounds[1:])]))
                arrays[engine+'.scores'].append(scores)
                if scores.ndim==2: arrays[engine+'.top1'].append(scores.argmax(axis=1))
            allowed=candidate_list(db,row)
            labels,starts,probabilities,_=classification_windows(row,row['seconds'],allowed)
            arrays[engine+'.detections'].append(probabilities>=.1)
            vectors=[np.frombuffer(r[0],dtype='<f4') for r in db.execute('''SELECT v.embedding FROM segments s
                JOIN vectors v ON v.rowid=s.id WHERE s.analysis_id=? ORDER BY s.start_s''',(row['id'],))]
            if vectors: arrays[engine+'.embeddings'].append(np.stack(vectors))
    result={key:np.concatenate(value) for key,value in arrays.items()}
    np.savez_compressed(target,**result)
    return {key:hashlib.sha256(value.tobytes()).hexdigest() for key,value in result.items()}


def compare(first,second):
    result=dict(identical=True,max_abs_scores=0.,max_abs_embeddings=0.,top1_changes=0,detection_changes=0)
    with np.load(first,allow_pickle=False) as reference,np.load(second,allow_pickle=False) as tested:
        if set(reference.files)!=set(tested.files): raise ValueError('Snapshot engines differ')
        for key in reference.files:
            a,b=reference[key],tested[key]
            if a.shape!=b.shape: raise ValueError('Snapshot shapes differ: '+key)
            result['identical'] &= np.array_equal(a,b)
            if key.endswith(('.scores','.embeddings')):
                field='max_abs_embeddings' if key.endswith('.embeddings') else 'max_abs_scores'
                # Bound comparison working memory for full-hour dense scores.
                for start in range(0,len(a),64):
                    diff=np.max(np.abs(a[start:start+64].astype(np.float64)-b[start:start+64]),initial=0.)
                    result[field]=max(result[field],float(diff))
            elif key.endswith('.top1'): result['top1_changes']+=int(np.count_nonzero(a!=b))
            elif key.endswith('.detections'): result['detection_changes']+=int(np.count_nonzero(a!=b))
    return result


def birdnet_acoustic_probe(args):
    """Diagnostic only: package backend without worker IPC, geo, saving or overview."""
    from models import configure_birdnet,REGISTRY
    configure_birdnet(args.models)
    from birdnet.acoustic.models.v2_4.tf import AcousticTFBackendFP32V2_4
    from ai_edge_litert import interpreter
    from birdnet.utils.helper import flat_sigmoid_logaddexp_fast
    from statistics import median
    paths=fixtures(args.work_dir/'fixtures-0.1'/'long','long',.1)
    audio,rate=sf.read(paths[0],frames=48000*60,dtype='float32')
    waveform=audio.reshape(-1,144000); original=interpreter.Interpreter
    results=[]; reference=None
    for threads,batch in ((1,1),(2,1),(4,1),(8,1),(1,8),(1,32)):
        def create(*a,**kw): return original(*a,**{**kw,'num_threads':threads})
        with patch.object(interpreter,'Interpreter',create):
            backend=AcousticTFBackendFP32V2_4(args.models/REGISTRY['birdnet']['files'][0],'CPU',False,inference_library='litert')
            backend.load()
        times=[]; scores=None
        for repeat in range(3):
            backend.predict(waveform[:batch])
            started=perf_counter()
            scores=np.concatenate([backend.predict(np.ascontiguousarray(waveform[i:i+batch]))
                for i in range(0,len(waveform),batch)])
            times.append(perf_counter()-started)
        probabilities=flat_sigmoid_logaddexp_fast(scores,sensitivity=-1.,bias=1.)
        if reference is None: reference=probabilities.copy()
        results.append(dict(threads=threads,batch=batch,seconds=60,wall_s=median(times),
            seconds_per_hour=median(times)*60,max_abs_scores=float(np.max(np.abs(probabilities-reference))),
            top1_changes=int(np.count_nonzero(probabilities.argmax(axis=1)!=reference.argmax(axis=1))),
            detection_changes=int(np.count_nonzero((probabilities>=.1)!=(reference>=.1)))))
        backend.unload()
        print('BirdNET acoustic only',results[-1],flush=True)
    output=args.output or args.work_dir/'birdnet-acoustic.json'
    output.write_text(json.dumps(dict(scope='Acoustic backend only; not a job benchmark',results=results),indent=2))


def trial(args):
    import analysis,storage
    from models import configure,configure_birdnet
    from species import initialize_species
    configure_birdnet(args.models)
    analysis.warm_imports()
    paths=sorted(args.fixture.glob('*.wav'))+sorted(args.fixture.glob('*.flac'))
    engines=ENGINE_SETS[args.engine_set]
    stages=Stages(); profile=cProfile.Profile() if args.profile else None
    report=dict(shape=args.shape,engine_set=args.engine_set,engines=engines,variant=args.variant,
                seconds=sum(sf.info(p).duration for p in paths),files=len(paths),profiled=bool(profile),
                script_sha256=analysis.digest(Path(__file__)))
    with tempfile.TemporaryDirectory(prefix='analysis-job-',dir=args.work_dir) as folder:
        root=Path(folder); storage.initialize(root); initialize_species(root); configure(root,args.models)
        main = engines[0] if len(engines) == 1 and engines[0].startswith('kinglet') else 'perch'
        (root/'models.json').write_text(json.dumps(dict(main=main, extra=[e for e in engines if e != main],confirmed=True,directory=str(args.models))))
        with storage.connect(root) as db:
            db.execute("INSERT INTO sites VALUES(1,'Benchmark','Asia/Tokyo',35,138)")
            db.execute("INSERT INTO datasets VALUES(1,1,'Benchmark',?)",(str(args.fixture),))
            db.executemany('''INSERT INTO recordings(dataset_id,path,original_time,timezone,recorded_at,
                seconds,sample_rate,size,mtime_ns) VALUES(1,?,?,?,?,?,?,?,?)''',
                (storage.inspect_audio(path,'Asia/Tokyo') for path in paths))
            records=[dict(r) for r in db.execute('SELECT * FROM recordings ORDER BY recorded_at')]
            site=dict(db.execute('SELECT * FROM sites').fetchone())
            job=db.execute("INSERT INTO jobs(dataset_id,status,total) VALUES(1,'queued',?)",(len(records)*len(engines),)).lastrowid
        try:
            with instruments(stages,args.variant,args.models,root):
                stages.seconds.clear(); stages.calls.clear()
                if profile: profile.enable()
                started=perf_counter()
                with stages.time('planning'): pending=analysis.plan(root,args.models,records,site)
                analysis.run(root,args.models,job,records,site,threading.Event(),pending)
            report['wall_s']=perf_counter()-started
            if profile: profile.disable()
            try:
                import resource
                peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                report['peak_rss_bytes']=int(peak if sys.platform=='darwin' else peak*1024)
            except ImportError:
                report['peak_rss_bytes']=None
            with storage.connect(root) as db:
                status=dict(db.execute('SELECT status,error FROM jobs WHERE id=?',(job,)).fetchone())
            report.update(status)
            if status['status']=='complete':
                report['seconds_per_hour']=report['wall_s']*3600/report['seconds']
                report['npz_bytes']=sum(p.stat().st_size for p in (root/'analyses').rglob('*.npz'))
                report['db_bytes']=(root/'echoroo.sqlite').stat().st_size
                report['derived_bytes']=sum(p.stat().st_size for p in (root/'window-batches').rglob('*') if p.is_file())
                report['hashes']=snapshot(root,args.snapshot)
                if args.reference and args.reference.exists(): report['equivalence']=compare(args.reference,args.snapshot)
        except Exception as error:
            if profile: profile.disable()
            report.update(status='failed',error=repr(error))
        if profile: profile.dump_stats(str(args.snapshot.with_suffix('.prof')))
        report['stages_s']=dict(sorted(stages.seconds.items()))
        report['calls']=dict(sorted(stages.calls.items()))
        report['stage_sum_s']=sum(stages.seconds.values())
        report['other_s']=max(0.,report.get('wall_s',0)-report['stage_sum_s'])
    args.result.write_text(json.dumps(report,indent=2,allow_nan=False))
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models',type=Path,default=REPO/'poc/models')
    parser.add_argument('--work-dir',type=Path,default=Path(tempfile.gettempdir())/'echoroo-analysis-bench')
    parser.add_argument('--output',type=Path)
    parser.add_argument('--shapes',default='long,short,flac')
    parser.add_argument('--engine-sets',default='perch,kinglet-s,dual,all')
    parser.add_argument('--variant',choices=VARIANTS,default='current')
    parser.add_argument('--repeats',type=int,default=3)
    parser.add_argument('--scale',type=float,default=1.,help='Diagnostic subset only; 1 is the requested full workload')
    parser.add_argument('--reference-dir',type=Path,help='Directory containing snapshots from a previous run')
    parser.add_argument('--profile',action='store_true',help='cProfile is additional overhead; compare equally profiled trials')
    parser.add_argument('--birdnet-acoustic-only',action='store_true',help='Isolated backend probe; not whole-job timing')
    parser.add_argument('--worker',action='store_true',help=argparse.SUPPRESS)
    for name in ('fixture','snapshot','result','reference'): parser.add_argument('--'+name,type=Path,help=argparse.SUPPRESS)
    parser.add_argument('--shape',help=argparse.SUPPRESS); parser.add_argument('--engine-set',help=argparse.SUPPRESS)
    args=parser.parse_args(); args.work_dir=args.work_dir.resolve(); args.models=args.models.resolve()
    args.work_dir.mkdir(parents=True,exist_ok=True)
    if args.worker: trial(args); return
    if args.birdnet_acoustic_only: birdnet_acoustic_probe(args); return
    if not 0<args.scale<=1 or args.repeats<1: parser.error('0 < scale <= 1 and repeats >= 1 are required')
    if any(value not in SHAPES for value in args.shapes.split(',')): parser.error('Unknown input shape')
    if any(value not in ENGINE_SETS for value in args.engine_sets.split(',')): parser.error('Unknown engine set')
    import onnxruntime as ort
    from analysis import digest
    from models import REGISTRY
    output=(args.output or args.work_dir/f'{args.variant}.json').resolve()
    if output.suffix!='.json': parser.error('output must be a .json file')
    artifacts=output.with_suffix(''); artifacts.mkdir(parents=True,exist_ok=True)
    if args.reference_dir and args.reference_dir.resolve()==artifacts: parser.error('Reference and output directories must differ')
    metadata=dict(platform=platform.platform(),cpu_count=os.cpu_count(),python=sys.version,
        numpy=np.__version__,soundfile=sf.__version__,onnxruntime=ort.__version__,providers=ort.get_available_providers(),
        revision=subprocess.run(['git','rev-parse','HEAD'],cwd=REPO,capture_output=True,text=True).stdout.strip(),
        analysis_sha256=digest(REPO/'apps/sidecar/analysis.py'),scale=args.scale,
        script_sha256=digest(Path(__file__)),
        models={engine:digest(args.models/model['files'][0]) for engine,model in REGISTRY.items()
                if (args.models/model['files'][0]).is_file()},
        label_sha256={engine:digest(args.models/model['files'][1]) for engine,model in REGISTRY.items()
                if engine!='birdnet' and (args.models/model['files'][1]).is_file()},
        japan_sha256=digest(REPO/'apps/sidecar/data/japan-birds.json'),
        stage_notes=['Stage times are exclusive within a thread; prefetch stages overlap across threads.',
            'inference.birdnet includes the package worker resampling, IPC and result collection.',
            'Catalog/fixture creation, import warm-up and numeric comparisons are outside wall_s.',
            'Model loading and worker cleanup are inside wall_s; OS caches and other applications are uncontrolled.'])
    rows=[]
    for shape in args.shapes.split(','):
        paths=fixtures(args.work_dir/f'fixtures-{args.scale}'/shape,shape,args.scale)
        for engines in args.engine_sets.split(','):
            for repeat in range(args.repeats):
                name=f'{shape}-{engines}-{repeat+1}'
                command=[sys.executable,str(Path(__file__).resolve()),'--worker','--models',str(args.models),
                    '--work-dir',str(args.work_dir),'--shape',shape,'--engine-set',engines,'--variant',args.variant,
                    '--fixture',str(paths[0].parent),'--snapshot',str(artifacts/(name+'.npz')),
                    '--result',str(artifacts/(name+'.json'))]
                if args.profile: command+=['--profile']
                reference=(args.reference_dir/(f'{shape}-{engines}-1.npz')) if args.reference_dir else None
                if repeat and reference is None: reference=artifacts/(f'{shape}-{engines}-1.npz')
                if reference: command+=['--reference',str(reference.resolve())]
                log=artifacts/(name+'.log')
                result=artifacts/(name+'.json'); result.unlink(missing_ok=True)
                with log.open('w') as stream:
                    try: code=subprocess.run(command,stdout=stream,stderr=subprocess.STDOUT,timeout=900).returncode
                    except subprocess.TimeoutExpired: code='timeout'
                row=json.loads(result.read_text()) if result.exists() else dict(shape=shape,engine_set=engines,variant=args.variant,status='failed',error=f'Child exited {code}; see {log}')
                if row['status']!='complete': row['error_details']=log.read_text(errors='replace')[-4000:]
                row.update(repeat=repeat+1,log=str(log),snapshot=str(artifacts/(name+'.npz')))
                rows.append(row); output.write_text(json.dumps(dict(metadata=metadata,trials=rows),indent=2))
                print(shape,engines,args.variant,repeat+1,row.get('status'),round(row.get('wall_s',0),3),row.get('equivalence',row.get('error','')),flush=True)
                if row['status']!='complete': break
                # Only the first numeric reference needs to stay on disk.
                if repeat: (artifacts/(name+'.npz')).unlink()
    print('REPORT',output,flush=True)


if __name__=='__main__': main()
