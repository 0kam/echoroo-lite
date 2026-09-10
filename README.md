# Echoroo Lite

Offline, single-user desktop edition of Echoroo — the human×machine bird-call
annotation loop (BirdNET detection + Perch similarity search + quiz-based
proficiency), rebuilt as a Tauri app with a Python sidecar and SQLite.

- Implementation status and remaining work: [docs/implementation-status.md](docs/implementation-status.md)
- Requirements: [docs/requirements.md](docs/requirements.md) (initial scope accepted)
- Design baseline: [docs/plan.md](docs/plan.md) (2026-07; requirements review reopened 2026-09)
- Session handoff: [docs/HANDOFF-2026-09.md](docs/HANDOFF-2026-09.md)
- M0 PoC artifacts: [poc/](poc/)

The full Echoroo repository (`0kam/echoroo`) remains the salvage/reference
source; Lite never merges back.

## Development preview

The first slice registers Site > Dataset recordings in place, runs BirdNET and
Perch, saves scores and embeddings, and searches by a spectrogram-selected audio
interval. Searches can be scoped by the recording's start date in its site timezone.
Saved searches support multiple references per species, a distribution of all matches,
random similarity-band sampling, and persistent judgements. Xeno-Canto key settings, search and selected
downloads are implemented and verified against the live service in the Mac release app.
Detection and search reports provide species summaries, evidence audio, spiral plots
and CSV/SVG/PNG/audio ZIP exports. A 26-species practice quiz supports custom sets,
selected audio clips and saved first answers. Validation, relinking, and Windows
release acceptance are pending.

Prerequisites: Node.js 22.12+, Rust stable, Python 3.11 and the platform's Tauri
build prerequisites. The UI uses the [official SvelteKit static configuration](https://v2.tauri.app/start/frontend/sveltekit/).

From the repository root on macOS:

```sh
uv venv --python 3.11
uv pip install --python .venv/bin/python -r apps/sidecar/requirements-dev.txt
npm ci
.venv/bin/python scripts/setup_models.py
npm run desktop
```

On Windows, create `.venv` with Python 3.11 and replace `.venv/bin/python` with
`.venv/Scripts/python.exe`. Install the Python requirements with that interpreter
(`python -m pip install -r ...`) or uv. `setup_models.py` is the explicit online
download step. It checks the pinned Perch hashes and prepares BirdNET's cache.
Do not run `uv venv` over an existing environment you want to keep.

In the app, add a site (latitude/longitude and IANA timezone), name a recording
group, choose a folder, review the import, then register it. This slice accepts
mono WAV/FLAC whose filenames end in `YYYYMMDD_HHMMSS`. It scans subfolders and
skips registered paths, invalid dates, unsupported channels and unreadable files.
Choose a recording and run analysis; candidate interval buttons play and stop
at that interval. Japanese/English is selectable and persisted.
Large recording groups show 100 rows per page and can be filtered by the recording
date in the site's local time. Filtering the list does not change the scope of
**Analyze recording group**.

To search, choose a recording or **Add local audio** (WAV/FLAC/MP3, mono/stereo).
Drag a time interval on the spectrogram or enter its start/end in seconds, listen
to it, then choose **Try this interval** for a temporary search. The reference selection
can be 0.2–30 seconds; the display loads only 5–60 seconds at a time. Results show
the 30 highest cosine similarities and play their intervals. **Back to reference**
returns to the original selection. Model input intervals and zero padding are
shown in the results. Stereo references are averaged to mono.

Search uses all embeddings, without species/region filtering, from the latest
compatible Perch analysis per recording in the selected group. The UI reports
coverage when recordings are unanalysed or use a different model version. The
reference interval itself is excluded. For persistent evidence, select a target species,
add selected intervals to its reference set, then **Search and save for all listed species**.
Each species gets its own immutable search with all similarities saved, using the
maximum over its references and retaining the winning reference. Choose a similarity
band and sample size, draw a random sample, listen, and save **Target species / Other /
Unknown**. Samples, their populations, reference versions and judgement history survive
restart and reanalysis. Unreviewed intervals are separate from negative judgements.
Similarity is not an identification probability, nor evidence of absence. See [search implementation](docs/search-implementation.md).

Open **Reports**, select detection or a saved search, set local dates and an optional
species, and create a report. Detection compares each model in complete 15-second
windows; search uses the saved 5-second similarities. Select a species and a plot cell
to listen to its evidence. **Export report** writes CSVs, the selected species' SVG/PNG
and checked audio intervals into a ZIP in your chosen folder. Reports retain their
original analysis versions and judgement state. Without validation, quality remains
explicitly unevaluated. See [report implementation](docs/report-implementation.md).

Open **Bird-call quiz** to practice with the 26 birds from `koe-e-lab/sample_data`,
without importing field recordings first. Select species/audio and save a named set,
then start a session. Answers, unknowns and progress are saved; results allow review
and replay. **Add a question from audio** uses local audio, a current field recording
or Xeno-Canto, with a spectrogram-selected 0.2–60-second interval copied into app data.
These are learning results, not estimates of identification ability or field precision/recall.
See [quiz implementation and source details](docs/quiz-implementation.md).

Species show a common name in the selected language together with the scientific
name. The offline catalog uses BirdNET names plus local additions; **Species names**
lets you search, add taxa and override Japanese/English names. Empty fields restore
bundled names. Exact matches rank above prefixes and substrings. Perch shares names on exact scientific-name matches; missing names
fall back to English, then the scientific name. Original predictions stay unchanged.
The logo and light/dark palette come from Echoroo full. Details and provenance:
[species and branding](docs/species-and-branding.md).

To use Xeno-Canto, open **Recordings and search → Reference audio → Find reference audio on Xeno-Canto**,
expand the API key settings and save your key. Then select
a target species, and search by country and exact A–E quality rating. Download only
a recording you select, then choose its interval on the same spectrogram. XC ID,
recordist, source URL and license are retained. Downloaded XC references work offline.
The key is stored in the app's data directory and is never returned by the API;
save an empty value to remove it. Live search, attributed download, spectrogram selection
and similarity search were verified in the Mac release app on 2026-09-09.

Data defaults to Tauri's app-data directory (`~/Library/Application Support/org.echoroo.lite`
on macOS). `ECHOROO_DATA` overrides it for development. Models default to `poc/models`
in development and `<data>/models` in a release; `ECHOROO_MODELS` overrides this.
The database, `analyses/` score archives and source audio references belong together.
Imported field recordings and local references stay in place; selected XC downloads
and quiz audio are app-managed copies. Include `quiz/` when backing up data.
Close the app before manually copying its data.
Reanalysis keeps older runs; cancelled jobs keep completed model/file results.
Automatic resume and a settings screen for moving data are not implemented yet.

## Checks and packaging

```sh
.venv/bin/python -m unittest discover -s apps/sidecar -p 'test_*.py'
npm run check
npm run build
cargo check --manifest-path apps/desktop/Cargo.toml
.venv/bin/python scripts/freeze_sidecar.py
npm run desktop:build
```

Freeze on the target OS. Binaries use Tauri's host-triple naming convention and
are excluded from Git. Models are currently provisioned separately in the release
data directory; the installer does not yet offer model setup.
Model and label provenance, hashes, and unresolved distribution terms are recorded
in [Model sources](docs/model-sources.md). BirdNET's model terms include a
noncommercial condition; permission for the intended business use is not yet established.
Windows CI has passed all eight API tests, frozen BirdNET/Perch inference on supplied
audio, searches, saved judgements, reports and CSV/audio exports, quizzes and clean shutdown.
EXE/MSI creation, NSIS installation and English/Japanese desktop startup also pass,
including Japanese persistence after restart on a 1024px screen. Installed desktop
checks also pass restoration of both model results, saved searches and judgements,
a 24-cell spiral report with cell selection, and quiz questions and feedback.
Model hashes are pinned; the upstream download service can return
HTTP 504, so prepare the exhibition's model cache in advance. Actual audio output,
file pickers, full offline use and annual performance on the exhibition PC remain unverified.
See [Windows preview preparation](docs/windows-preview.md).
On the M4 Pro, the 17,520-file real-audio replay completed both models, searches and
CSV/audio exports in 4 h 8 min 25 s; annual plots were then checked in the release app.
See [the annual benchmark and its limits](docs/annual-benchmark-2026-09.md).
For quiz navigation, cold spectrograms, validation completion and annual result-view
latency, see [the Mac performance measurements](docs/performance-2026-09.md).
The Mac release app also processed all 24 supplied recordings (12 hours) in about
nine minutes and exported both classifier reports and a full-data search report.
See [the real-day preview record](docs/real-day-preview-2026-09.md).

Optional real-audio checks (the supplied 30-minute recording):

```sh
.venv/bin/python apps/sidecar/test_sidecar.py --real-recording /Users/okamoto/Music/whombat_data/test/2MA04037_20240808_060000.wav
.venv/bin/python scripts/check_frozen.py apps/desktop/binaries/echoroo-sidecar-aarch64-apple-darwin --models poc/models --recording /Users/okamoto/Music/whombat_data/test/2MA04037_20240808_060000.wav
```

The first check keeps test results under ignored `.local-data/m0-smoke`; the frozen
check uses a temporary catalog. Scores are uncalibrated: BirdNET uses sigmoid scores;
Perch displays softmax scores over all 14,795 classes, retaining original logits.
Both models’ classification candidates always use the recording site's region list in the app, with no UI
toggle. Perch uses exact scientific-name matches to the same run's BirdNET list.
Filtering does not change scores.
Existing saved runs are displayed this way without reanalysis. Complete Perch class scores and BirdNET scores
within the recorded geo-filtered label set are stored as non-pickled NPZ arrays;
the UI shows only top candidates. This is not a validated species inventory.
