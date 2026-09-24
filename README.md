# Echoroo Lite

Offline, single-user desktop edition of Echoroo — the human×machine bird-call
annotation loop (BirdNET detection + Perch similarity search + quiz-based
proficiency), rebuilt as a Tauri app with a Python sidecar and SQLite.

- Implementation status and remaining work: [docs/implementation-status.md](docs/implementation-status.md)
- UI review handoff: [docs/UI-REVIEW-HANDOFF-2026-09-24.md](docs/UI-REVIEW-HANDOFF-2026-09-24.md)
- Fixed 30-window validation QA: [docs/validation-effort-qa-2026-09-24.md](docs/validation-effort-qa-2026-09-24.md)
- Mac workflow QA, measured effort, and remaining checks: [docs/release-readiness-2026-09-22.md](docs/release-readiness-2026-09-22.md)
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
and portable HTML reports with CSV/SVG/PNG/audio ZIP exports. A 26-species practice quiz supports custom sets,
selected audio clips and saved first answers. Detection and search validation can adjust
species-specific thresholds and evaluate them on separate samples. Windows acceptance
and an independent, unseen-audio capability test remain pending.

From a site's results, **Listen and identify animals** opens shared 15-second
samples. Choose names from the stable site list, ordered by AI detection count
(or add a species), mark one or more call ranges, and save. Double-click the
spectrogram to play from that point to the end of the clip without changing marks.
Scroll to zoom; scroll over an axis to zoom time or frequency separately.
The wide spectrogram follows the comparison preview's proportions, with persistent,
larger scrollbars for panning after zooming. Unknown species and overlapping calls
are supported, and previous answers can be corrected. Unmarked species remain
unassessed. Every 10 clips, review the animals heard and optionally check one
species: marked positives are reused, only missing presence/absence answers are
requested, and detection quality is measured on recordings held aside before
sampling. HTML exports include these exact ranges, spectrograms, audio and
versioned CSV/JSON evidence even when no model detected the species. Range counts
are not extrapolated to whole-site frequencies. See
[the validation design](docs/validation-design.md) for sampling and uncertainty.

From a species result, **Create a model for this species** reuses confirmed call
ranges and search judgements to fit a small classifier over fixed Perch embeddings.
Unknown, unmarked and unfinished answers are not negative examples. Creation also
applies the model to the site's recordings. Grouped cross-validation on 5-second
training examples selects a threshold; its reference metrics are separate from
the 15-second evaluation on recordings held aside before training. Results update
every 10 answers without requiring the entire sample to be completed. The HTML
suite includes model files, scores, evidence and evaluation provenance. Neither
cross-validation nor a high point estimate alone establishes detection quality.

Spectrograms use a 16 ms Hann window, 2 ms steps and 2× zero padding, with the
Roseus palette and a linear frequency axis. The same display settings apply to
recordings, references, quizzes and new report figures; exports retain the FFT
settings. These display choices do not change audio, model scores or annotations.

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

In the app, add a site (latitude/longitude and IANA timezone), choose a folder,
review the import, then register it. The imported recording group gets a folder-based
name by default and can be renamed later. This slice accepts
mono WAV/FLAC whose filenames end in `YYYYMMDD_HHMMSS`. It scans subfolders and
skips registered paths, invalid dates, unsupported channels and unreadable files.
Choose a recording and run analysis; candidate interval buttons play and stop
at that interval. Japanese/English is selectable and persisted.
Large recording groups show 100 rows per page and can be filtered by the recording
date in the site's local time. Filtering the list does not change the scope of
**Analyze recording group**.

To search, open search from a dataset or species result, choose a target species and
reference audio from this PC or Xeno-Canto, then drag a time interval on the spectrogram
or enter its start/end in seconds. Add the interval as an example and start the search;
the reference selection can be 0.2–30 seconds and the display loads only 5–60 seconds
at a time. Results show
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

From a site's results, choose **Export report**, keep or change the included species,
and select a destination folder. The app saves a readable `index.html` with native
text and tables, species plots, validation results, supplementary quiz practice
records and playable audio. **Copy text and tables** supports drafting in Word;
**Save figure** provides a labelled PNG. PDF and DOCX generation are not included.
The same contents are packaged in a ZIP: recipients extract it before opening
`index.html`. All report resources are local; no web service is needed for playback.

Detection counts use complete 15-second windows and search uses complete 5-second
windows. Species/model detection thresholds and species/reference-set search
thresholds apply to all eligible intervals, including unreviewed ones. Calibration
and independent evaluation are distinguished; an adjusted threshold is not itself
evidence of validated precision. Search counts remain unset until a threshold can be
calibrated. AI examples, human validation intervals and positive search judgements
remain separate. Saved files retain their original recording scope, analysis,
thresholds, answers and contemporaneous quiz records. See
[report implementation](docs/report-implementation.md) for provenance and verification limits.

New validation sessions keep the 90% calibration target, then fix at most 30
predicted-positive windows for each species and model before evaluation answers are
seen. The formal precision result is calculated only after every fixed window is
answered: a one-sided 95% lower bound of at least 80% meets the evaluation goal.
Progress can be saved and resumed, but interim answers never provide a guarantee or
automatic pass. The same human annotation is reused when samples for multiple species
or models contain the same audio; because 30 is a per-species, per-model limit, the
combined task is not always 30 unique sounds. Recall is an optional, separate check
that also samples missed detections; precision alone does not establish recall.
Saved sessions using the earlier 90% evaluation method retain that method and sample.
Previous answers can be corrected where the saved method permits. Corrections retain
their history and never rewrite saved exports. Detection truth is presence
anywhere in 15 seconds, compared with the maximum of five BirdNET 3-second scores
or three Perch 5-second scores; it does not label every native subwindow.
BirdNET predictions used as a functional QA oracle are provisional test data, not
scientific ground truth or a species-performance estimate.

The 30-window limit is a design choice. Earlier overlap-reduction estimates are
simulations, not measurements of human effort. Source-web QA, production web,
Mac `.app` and DMG builds have passed. Native Mac checks cover starting, saving,
resuming and correcting evaluations, results and local HTML playback. Windows has
not been tested. See the [fixed-sample QA record](docs/validation-effort-qa-2026-09-24.md).

On a species page, **Make a model for this species** shows the checked positive and
negative material. If enough material is available, **Create a model that tells these
calls apart** applies the fixed Perch-embedding classifier to the scoped recordings;
the same page can then start an independent check on recordings not used to create it.
Reference values from the checked material remain separate from that evaluation.

Open **Bird-call quiz** to practice with the 26 birds from `koe-e-lab/sample_data`,
without importing field recordings first. Select species/audio and save a named set,
then start a session. Answers, unknowns and progress are saved; results allow review
and replay. **Add a question from audio** uses audio chosen on this PC or Xeno-Canto,
with a spectrogram-selected 0.2–60-second interval copied into app data.
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
in [Model sources](docs/model-sources.md), including the eBird taxonomy used for species-page links. BirdNET's model terms include a
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
