# Echoroo Lite

Offline, single-user desktop edition of Echoroo — the human×machine bird-call
annotation loop, rebuilt as a Tauri app with a Python sidecar and SQLite.
The standard edition uses Perch V2. A separately distributed non-commercial
edition adds BirdNET 2.4.

- Installation (Windows, macOS, Linux; unsigned-app warnings): [docs/install-guide.md](docs/install-guide.md)
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

The first slice registers Site > Dataset recordings in place, runs the included
models, saves scores and embeddings, and searches by a spectrogram-selected audio
interval. Xeno-Canto key settings, search and selected downloads are implemented and
verified against the live service in the Mac release app. Reports provide species
summaries, evidence audio, spiral plots and portable HTML reports with CSV/SVG/PNG/audio
ZIP exports. A practice quiz with bundled Xeno-Canto recordings (CC BY, CC BY-SA or CC0) supports custom sets, selected audio clips and
saved first answers. Windows acceptance and an independent, unseen-audio capability test
remain pending.

Each species page answers two questions with two cards (proposal:
[species flow](docs/species-flow-proposal-2026-09-30.md)).

- **Is it here?** — **Listen and confirm** lists the sounds most likely to be the species,
  from the top: 15-second windows interleaved from each model's score ranking (BirdNET,
  Perch and a species model), labelled with the model that flagged them, or — for a
  searched species — its 5-second search hits by similarity. The only action is
  **This is its call** (press again to undo). One mark confirms the species; marked sounds
  and "Heard" answers from steps ② and ③ below become report evidence (step ① answers do
  not). When nothing is found, **Record that it could not be confirmed** saves
  "listened to the top N sounds without confirming", where N is the unbroken run of
  played sounds from the top.
- **When and how much?** — each model is checked on its own: ① 20 sounds from the
  threshold-setting days, one per equal-width bin of the model's logit score (empty bins
  are filled from the others; no adaptive re-selection). A Firth-penalised logistic curve
  of "heard" against logit score sets the lowest threshold at which the fitted share of
  calls among windows at or above it is at least 90%. If nothing was heard, or scores and
  answers show no positive relation, the check states that no threshold could be set and
  nothing is counted (no default is used). ② and ③ are the fixed 30-sound accuracy and
  optional missed-call samples from the other days. An answer about one 15-second window
  of a species is reused by that species' other models. A check (all its ①–③ answers not
  used by another model's check) can be deleted from the species page.

Recordings are split into threshold-setting and checking halves by local day (by
recording when there is only one day), reproducibly per site and hidden from users. See
[the validation design](docs/validation-design.md) for sampling and uncertainty.

A **species model** is built on its own page from the species' search: label 5-second
search hits **Voice / No voice**; "Not heard" answers from steps ② and ③ add no-voice
material (each 15-second window as three 5-second pieces). At least three of each are
needed. The fixed Perch-embedding classifier is applied to the site's recordings, its
15-second score is the maximum of three pieces, and it is checked like BirdNET and Perch
(windows it learned from are left out of its check).

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
.venv/bin/python scripts/setup_models.py --perch-only --output .local-data/perch-models
ECHOROO_MODELS=.local-data/perch-models npm run desktop
```

On Windows, create `.venv` with Python 3.11 and replace `.venv/bin/python` with
`.venv/Scripts/python.exe`. Install the Python requirements with that interpreter
(`python -m pip install -r ...`) or uv. Developers use `setup_models.py --perch-only`
for the standard edition, or omit `--perch-only` for the BirdNET development preview
and both release builds.
Installed apps already include these models and need no model setup or network access for analysis.
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

To search, open search from the site list or a species page, choose the species and
reference audio from this PC or Xeno-Canto, then drag a time interval on the spectrogram
or enter its start/end in seconds. Add one or more intervals as examples and start the
search; the reference selection can be 0.2–30 seconds and the display loads only 5–60
seconds at a time. Stereo references are averaged to mono. Each species has exactly one
search per site; when it finishes, the app opens that species' **Is it here?** list of
search results. To search again, delete the search on the species page first: the
confirmation lists what goes with it — its "This is its call" marks, not-found records,
voice/no-voice labels and any species model built from it (with that model's check).

Search uses all embeddings, without species/region filtering, from the latest
compatible Perch analysis per recording in the selected group. The reference interval
itself is excluded. Similarity is the maximum cosine over the references. Searches are
not detectors for reporting: they have no threshold, no accuracy check and no time
chart. Similarity is not an identification probability, nor evidence of absence. See
[search implementation](docs/search-implementation.md).

From a site's results, choose **Export report**, keep or change the included species,
and select a destination folder. The app saves a readable `index.html` with native
text and tables, species plots, validation results, supplementary quiz practice
records and playable audio. **Copy text and tables** supports drafting in Word;
**Save figure** provides a labelled PNG. PDF and DOCX generation are not included.
The same contents are packaged in a ZIP: recipients extract it before opening
`index.html`. All report resources are local; no web service is needed for playback.

The report's species table has two columns: **Present** (confirmed by listening with
the number of evidence sounds / listened to the top N without confirming / not checked)
and **When and how much** (the models whose check met the accuracy goal). Time charts
are grouped in model tabs and include unchecked, goal-missed and undecided models with
that status stated. Detection counts use complete 15-second windows; a checked threshold
applies to all eligible intervals, including unreviewed ones, and an unchecked model
uses the default threshold labelled "not checked". AI examples, evidence sounds and
check answers remain separate. Saved files retain their original recording scope,
analysis, thresholds, answers and contemporaneous quiz records. See
[report implementation](docs/report-implementation.md) for provenance and verification limits.

Step ② fixes at most 30 predicted-positive windows per species and model before
answers are seen. The formal precision result is calculated only after every fixed window
is answered: a one-sided 95% lower bound of at least 80% meets the goal. Progress can be
saved and resumed, but interim answers never provide a guarantee or automatic pass.
Step ③ (recall) is optional and samples missed detections; precision alone does not
establish recall. Corrections retain their history and never rewrite saved exports.
Detection truth is presence anywhere in 15 seconds, compared with the maximum of five
BirdNET 3-second scores or three Perch 5-second scores. BirdNET predictions used as a
functional QA oracle are provisional test data, not scientific ground truth.

The 30-window limit is a design choice. Earlier overlap-reduction estimates are
simulations, not measurements of human effort. Source-web QA, production web,
Mac `.app` and DMG builds have passed. Native Mac checks cover starting, saving,
resuming and correcting evaluations, results and local HTML playback. Windows has
not been tested. See the [fixed-sample QA record](docs/validation-effort-qa-2026-09-24.md).

Open **Bird-call quiz** to practice with the bundled Xeno-Canto clips (13 Japanese birds whose
recordings are CC BY, CC BY-SA or CC0; credits are shown after each answer and in
`apps/sidecar/data/quiz/selection.json`; rebuild with `scripts/bundle_quiz.py`),
without importing field recordings first. Select species/audio and save a named set,
then start a session. Answers, unknowns and progress are saved; results allow review
and replay. **Add a question from audio** uses audio chosen on this PC or Xeno-Canto,
with a spectrogram-selected 0.2–60-second interval copied into app data.
These are learning results, not estimates of identification ability or field precision/recall.
See [quiz implementation and source details](docs/quiz-implementation.md).

Species show a common name in the selected language together with the scientific
name. The offline catalog uses the Checklist of Japanese Birds and Wikidata plus local additions; **Species names**
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
in development and the app's bundled read-only resources in a release;
`ECHOROO_MODELS` overrides this.
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
.venv/bin/python scripts/setup_models.py
.venv/bin/python scripts/freeze_sidecar.py
npm run desktop:build
npm run desktop:build:birdnet
```

Freeze on the target OS. Binaries use Tauri's host-triple naming convention and
are excluded from Git. Run `setup_models.py` before `npm run desktop:build`; the build
fails if a required model is absent. The standard bundle contains only Perch;
`desktop:build:birdnet` additionally contains BirdNET 2.4 and uses a separate app
identifier and data folder. Installed users do not need a separate model setup.
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
These Windows, frozen and release checks predate the 2026-09-30 species-flow redesign
(presence list, per-model checks, one search per species) and have not been repeated.
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
