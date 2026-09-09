# Echoroo Lite — Design Plan

Offline, single-user desktop rebuild of Echoroo. This document captures the design
decisions reached during scoping and the salvage survey of the current codebase
(`apps/api`). It is the reference for turning the design into SpecKit specs/tasks.

Status: **architecture baseline from 2026-07; product requirements review reopened
2026-09-08. M0 is incomplete.** See [requirements.md](requirements.md) for open
product decisions, evidence limits, and proposed acceptance criteria. Its proposals
are not approved changes to this baseline.

The 2026-09-08 user discussion establishes non-expert corporate/municipal users,
report-ready visualizations (including the full app's spiral plot), sound-search
and learning as the final purpose. Initial priority is import, analysis, and
embedding search. The recording protocol produces 292 audio hours per site-year;
the first performance target machine is the user's M4 Pro Mac (24 GiB RAM).
The user confirmed Site > Dataset without a Project layer, and requested both
BirdNET and Perch classification results for comparison, in addition to Perch
embedding search. Initial review/reporting details remain under review.

Latest exhibition decisions: **2026-11-28 on Windows PCs**. Audio, analysis results,
report plots, similarity search, and quizzes are required; validation operations
are optional at the event. The minimum processing target is one site-year
(292 recorded hours / 17,520 one-minute files) within **12 hours**. Hardware will
be selected after measurement. Reuse the **26-species `koe-e-lab/sample_data`**
quiz assets, including original-audio import and UI time-range selection.
Detection reports and similarity-search reports have distinct purposes and metrics;
see [reporting-design.md](reporting-design.md) for confirmed scope and detailed
proposals. Human labels are collected in validation and sampled search judgments; generic
detection correction is omitted from the initial release (accepted 2026-09-08).
Implementation has started; see [implementation-status.md](implementation-status.md).

---

## 1. Vision & Scope

Echoroo Lite is the **single-user, offline** edition of the human×machine annotation
loop that Echoroo targets: field workers are trained via bird-call quizzes (proficiency)
so they can use machine annotation (BirdNET/Perch) correctly, then human annotation is
used to evaluate and steer the machine.

### Simplifications vs full Echoroo
- **Single user, no auth** — remove login/2FA/permissions/BFF/collaborators.
- **Species taxonomy = BirdNET + Perch labels + manual additions**, with verified
  cross-model mappings (not a live GBIF backbone).
- **Detection results are review/display-oriented** (no community voting/consensus).
- **No model development** — search only (metadata + acoustic similarity).
- **Offline-first desktop app** — core features run with no network.

### Offline boundary (honest framing: "works offline", not "never touches network")
- **Fully offline:** import, BirdNET detection + geo filter, Perch embedding, detection
  browse/search, local acoustic similarity search, annotation, evaluation, quiz on cached
  clips, camtrapDP export.
- **Online (optional only):** xeno-canto reference/quiz clip acquisition. Once fetched,
  clips are cached locally and usable offline.

---

## 1.5 Repository Strategy

- **New standalone repository `echoroo-lite`** (`/home/okamoto/Projects/echoroo-lite`). Not a
  branch (Lite never merges back into the actively-developed full app) and not a monorepo dir
  (keeps Lite's simplicity decoupled from the churny full repo).
- **Full Echoroo repo is retained locally as the salvage/reference source.** The ~6 salvaged
  modules are all heavily adapted (TF→ONNX, pgvector→sqlite-vec, Celery/async removal,
  lazy-fetch→bundled), so history/blame value is low; prior implementations stay referenceable
  in the full repo via `git show <commit> -- <file>`.
- **Layout:** `apps/desktop` (Tauri shell) / `apps/web` (UI copied from full app, data layer
  rewired to localhost backend) / `apps/sidecar` (FastAPI + salvaged ML modules, onnxruntime).
- This `plan.md` moves into `echoroo-lite` once the repo is scaffolded.
- **M0 PoC runs in the full repo environment** (the Perch SavedModel + TF 2.20 live there); the
  artifact (`perch_v2.onnx` + conversion recipe) is carried into `echoroo-lite`.

---

## 2. Architecture

```
┌─────────────────────────────────────────────┐
│ Tauri app (Rust shell)                        │
│  ┌────────────────────┐   ┌────────────────┐ │
│  │ SvelteKit UI        │──▶│ Python sidecar │ │
│  │ (reused components,  │◀──│ FastAPI        │ │
│  │  data layer rewired) │   │ (PyInstaller)  │ │
│  │  localhost:<dynamic> │   │  BirdNET+Perch │ │
│  └────────────────────┘   │  SQLite        │ │
│                            │  onnxruntime   │ │
│                            └────────────────┘ │
│  Data folder (user-chosen): ~/EchorooData/    │
│   ├── echoroo.db  (SQLite: meta + sqlite-vec) │
│   ├── clips/      (cached xeno-canto/upload)  │
│   └── models/     (BirdNET tflite, Perch onnx)│
│  Audio recordings: referenced in place (link) │
└─────────────────────────────────────────────┘
```

- **Shell / packaging:** Tauri (Rust) + SvelteKit static build + Python sidecar frozen
  with PyInstaller. **Windows is the primary target**; macOS/Linux follow.
- **Sidecar startup:** backend binds a free port, prints it on stdout; Tauri reads it and
  passes it to the UI (avoids fixed-port collisions).
- **Background jobs:** thread/process pool + progress polling/SSE. **Celery and Redis are
  removed** — inference is CPU-bound and must run off the FastAPI event loop.
- **Bundled binaries:** `ffprobe` (metadata), BirdNET tflite model, Perch ONNX model.

---

## 3. ML Pipeline (two passes — salvaged from current code)

The current Echoroo **already implements the target arrangement** (detection = BirdNET,
embedding = Perch), as **two independent passes**. This is salvage, not new design.

| Concern | Current implementation | Lite treatment | Difficulty |
|---|---|---|---|
| **Detection** | `ml/birdnet_wrapper.py::analyze_file()`, birdnet pip, TF/TFLite, 3.0s segments → (scientific_name, confidence, start, end) | **BirdNET V2.4 via LiteRT** (`birdnet==1.1.1`, `library="litert"`) verified on the M4 Pro; Windows packaging remains open. | Low |
| **Geo filter (required)** | BirdNET geo model, lat/lon/week, `custom_species_list` at detection | Always apply the region list in the app for both models. Perch uses the same run's list with exact scientific-name matching. Show one shared explanation; no UI toggle. Preserve all Perch scores. | Low |
| **Perch embedding + classification** | `ml/perch/direct_inference.py`, Perch V2, **1536-dim**, 5.0s segments; stored in Postgres `embeddings.vector` (pgvector) | **Perch ONNX + onnxruntime**, vectors in **sqlite-vec (1536-dim)**. Also consume classification output for comparison with BirdNET (user confirmed 2026-09-08); validate labels, score semantics, and both-output performance. | Med |
| **Vernacular / 和名 (required)** | Full uses a multi-source vernacular resolver. | **BirdNET label names + local user additions**, following the user's 2026-09-08 preference. Show the selected-language common name and scientific name. Share names with Perch only on exact scientific-name matches; retain unmatched taxa. See [species-and-branding.md](species-and-branding.md). | Low |
| **Metadata** | `workers/upload_tasks.py::_parse_datetime_from_filename()` (user regex + strptime + IANA TZ) + ffprobe | **Salvage as-is** (stdlib). Bundle `ffprobe`. | Low |
| **xeno-canto** | `api/v1/xeno_canto.py`, API v3 proxy, **no caching**, needs API key | Salvage proxy, **add local cache**. **Optional feature; user registers own API key.** | Med |

Detection and embedding stay **two passes** (BirdNET 3s, Perch 5s) — simultaneous
inference is neither possible nor needed for single-user batch processing.

Perch display scores use softmax over the complete class vector **before** region
filtering, following the model's training head. Keep original logits in NPZ, and
identify 0–1 display scores as uncalibrated. See [details and source](species-and-branding.md).

> **M0 PoC result (confirmed):** Self-conversion of Perch V2 via tf2onnx is impossible
> (the CPU SavedModel is a single StableHLO `XlaCallModule` op). But the community export
> **`justinchuby/Perch-onnx` (`perch_v2_no_dft.onnx`, 390 MB)** runs drop-in on onnxruntime
> CPU with **parity cosine 1.0 / max abs diff 1.3e-5** at **~12s per 30-min recording**
> (8 threads) — 4–5× faster than TF/XLA and **removes the need to bundle full TensorFlow**.
> `perch_v2.onnx` (409 MB) is the bit-exact fallback. See `poc/CONVERSION_NOTES.md`.

### Species master & identity
- Master covers **BirdNET and Perch biological labels + manual additions**. Display
  names use **local overrides > BirdNET names** per locale, then English, then the
  scientific name. Initial implementation bundles Japanese and English names.
- Store a persistent local UUIDv4 independently of display names. Preserve each
  model's original versioned label and score. Name edits never rewrite predictions.
  Never use an unversioned positional label index as species identity.
- Match models by exact scientific name only; keep unmatched Perch taxa without
  inventing common names. Taxonomic synonyms/splits are not merged automatically.
  COL, IOC/OSJ imports and iNaturalist name fetching are outside the initial design.
- Manual species are annotation/quiz candidates. **Optional upside (defer to M3 decision):**
  Perch embeddings are trained to be linearly separable, so a few-shot linear probe could
  make manual species actually *detectable* from a handful of examples.

---

## 4. Data Model & Persistence

- **Central SQLite** in a **user-chosen data folder** (default under app-data dir). Single
  writer (single user) → safe; folder can live on external/synced storage for portability.
- **Grouping hierarchy: `Site > Dataset`** (the full-app `Project` top level is removed).
- **Recording time:** default to the installation site's local time, using an IANA
  timezone on Site. Preserve the original parsed timestamp and the import timezone/
  offset alongside the UTC instant. Later locale or Site-setting changes must not
  silently reinterpret existing recordings. Flag ambiguous/nonexistent DST times.
- **Audio = referenced in place (link)**, with "file not found → relink" recovery. No copy;
  field WAVs are large. Playback/spectrogram require the source to remain accessible.
- **Scope split:**
  - *User-global (cross-site):* proficiency, species master (BirdNET + manual), cached clip
    library.
  - *Site/Dataset-scoped:* recordings, detections, annotations, evaluations.
  - Proficiency is inherently longitudinal/user-level → this is why a central DB is chosen
    over per-project files.
- **Vector store:** sqlite-vec virtual table, 1536-dim, brute-force KNN (fine for the
  original assumption of thousands–tens-of-thousands of detections). **Scale review:**
  the current recording protocol produces 210,240 non-overlapping 5s embeddings per
  site-year if all audio is indexed. Brute-force search performance is unverified at
  this scale; measure before accepting or replacing this baseline.

### camtrapDP export mapping
- **Site → Data Package**, **Dataset → Deployment** (location + period), **recording →
  Media**, **detection → Observation**. Media exported as references.
- Export is the portability/handoff/backup path (replaces per-project files).

---

## 5. UX Surface (7 areas)

1. **Home** — counts (recordings/detections/species), recent jobs, "N unreviewed",
   "low-proficiency target species".
2. **Recordings** — import folder/files; BirdNET analysis (confidence threshold, geo
   filter via lat/lon/week); batch progress + cancel; recording list. Time from filename
   parse; **location entered per Dataset/deployment (manual)**.
3. **Detections** — list filter/sort by species/confidence/time/dataset/location; per
   detection: playback + spectrogram (region highlight, click-to-seek) + confidence;
   **raw model candidates, with human labels handled in validation/search**; species/recording
   rollups; camtrapDP export.
4. **Search** — metadata filter; **acoustic similarity** (sqlite-vec) with query = own
   detection *or* xeno-canto reference clip ("species X sounds like this → find it in my
   recordings" = human recovers machine's misses).
5. **Evaluation** — pick a **bounded subset**, label it independently (confirm/reject +
   **add missed**) → ground truth → **precision/recall/F1** + **confidence-threshold curve**
   → pick a threshold → apply to the rest.
6. **Quiz & Proficiency** — pick target species → build call quiz (source: upload or
   xeno-canto) → score → per-species proficiency + history → feed self-awareness into
   annotation ("low proficiency for this species, take care"). Rebuild the local quiz
   flow while reusing the user-selected **26 audio/image/silhouette sets from
   `../koe-e-lab/sample_data`**. Source inventory is in
   [quiz-source-inventory.csv](quiz-source-inventory.csv). Each species currently has
   one original audio file; independent proficiency testing needs unseen sources.
7. **Species / Settings** — browse BirdNET/Perch species (scientific/English/和名); manual add;
   target-species sets; data folder, model/sensitivity, default location/TZ, language,
   **optional xeno-canto API key**.

### Locale and time presentation (user confirmed 2026-09-08)

- App locale is a user-global, changeable setting independent of Site timezone and
  PC timezone. Use a supported OS locale initially, falling back to English.
- Ship **Japanese and English in the first preview**, as requested for a domestic
  event. Japanese OS locales default to Japanese; persist the user's language choice.
  Use keyed locale resources and English fallback. Future languages must not require
  rewriting components. Verify Japanese coverage and layout across all preview flows,
  including progress, empty states, confirmation dialogs, and user-facing errors.
- Format UI dates and numbers with native locale facilities, explicitly using the
  Site timezone for recording times. Local-day/hour aggregation and spiral plots
  use Site local time and identify that time basis when comparing sites.
- Keep taxon IDs, model labels, and scientific names independent of localized display
  names. Machine-readable exports retain fixed field names and ISO 8601 timestamps
  with an explicit offset or UTC, regardless of UI locale.

### Removed from Lite
Auth/2FA/user management, permissions/sharing/collaborators, voting/consensus review,
model training/custom SVM/embedding-run management, BFF, KMS, S3, Celery, Redis, Postgres.

---

## 6. Milestones

The original M1/M2/M3 sequence below is a baseline, not the current exhibition
priority. Report plots and a working quiz must be available by the Windows
exhibition; full validation UI may follow. Build and test Windows packaging early.

- **M0 — Vertical-slice PoC (highest risk first).**
  Tauri → sidecar startup (port handshake) → import 1 recording → BirdNET detection →
  **Perch ONNX via onnxruntime on CPU** → embedding → SQLite/sqlite-vec → UI display →
  similarity search. **Primary validations:**
  1. Perch ONNX runs on CPU, freezes cleanly with PyInstaller on Windows, acceptable speed
     on a 30-min recording (test1 dataset).
  2. Detection precision with the BirdNET geo filter, and **subjective quality of
     BirdNET→Perch similarity search**, on test1.

  **Status:** Validation 1 **PARTIAL**: historical Linux Perch synthetic-input parity
  and inference throughput support GO for that component only. The ~12s/30-min value
  is extrapolated inference time, not a recording end-to-end measurement. Windows
  freeze remains unverified. Mac real-audio execution of Perch and BirdNET/LiteRT
  is now recorded in [REAL_AUDIO_NOTES.md](../poc/REAL_AUDIO_NOTES.md).
  Validation 2 (subjective search quality) is still pending; the user supplied
  `whombat_data/test`, so dataset availability is no longer a blocker.
- **M1 — Core.** Import/analyze, detection browse, metadata/search, similarity search,
  species management, settings, camtrapDP export.
- **M2 — Evaluation.** Bounded-subset lite annotation, P/R/F1, threshold curve + apply.
- **M3 — Quiz & Proficiency.** Quiz build (xeno-canto optional + upload), scoring,
  per-species proficiency; decide on few-shot custom-species detection.

---

## 7. Risks & Open Items

- **Perch runtime — RESOLVED for CPU parity/speed (M0).** Use `justinchuby/Perch-onnx`
  (`perch_v2_no_dft.onnx`, 390 MB) via onnxruntime CPU: parity cosine 1.0 / max abs diff
  1.3e-5, ~12s per 30-min recording (8 threads). Self-conversion (tf2onnx) is impossible
  (StableHLO `XlaCallModule`). **Remaining:** confirm PyInstaller freeze of onnxruntime + the
  ONNX on Windows (low risk — onnxruntime freezes cleanly, unlike TF). Fallback: bundle the
  TF+XLA SavedModel (works, ~66s/30-min, but heavy Windows freeze).
- **Similarity-search quality** — BirdNET embeddings were rejected as too weak; Perch is
  SOTA, but confirm subjectively in M0.
- **Cross-platform binaries** — ffprobe + BirdNET tflite + Perch ONNX must be bundled per
  OS (Windows first).
- **Vernacular table build** — offline pre-generation of `taxon_vernacular_name`
  from the versioned bundle described in the handoff, checking both model label sets.
- **xeno-canto API key** — optional feature; user registers their own key in Settings.

---

## 8. Salvage vs Rebuild summary

- **Salvage (lift, minimal change):** BirdNET detection wrapper, BirdNET geo filter,
  filename datetime parse + ffprobe metadata, vernacular resolve logic, xeno-canto proxy.
- **Salvage + change:** Perch embedding (TF→ONNX), vector store (pgvector→sqlite-vec),
  vernacular data (lazy fetch → bundled table), xeno-canto (add local cache, make optional).
- **Rebuild fresh:** local quiz/proficiency UI with reused koe-e-lab assets, persistence layer (central SQLite,
  Site>Dataset), background jobs (Celery→thread/process pool), Tauri shell + sidecar,
  SvelteKit data layer (rewire off BFF/auth to localhost backend).
- **Drop entirely:** auth/2FA/BFF/permissions/collaborators, voting/consensus, model
  training, KMS, S3, Redis, Postgres.
