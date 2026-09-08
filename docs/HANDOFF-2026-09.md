# Echoroo Lite 開発ハンドアウト(2026-09-08 版)

MacBook での開発開始用の自己完結ドキュメント。Linux 開発機のセッションメモリは
Mac からは見えないため、必要な文脈はすべてここに書く。正本の設計書は
[plan.md](plan.md)(2026-07 に design locked)。本書はその後の進展と、次の
セッションで実際にやることをまとめたもの。

---

## 1. いま何がどこまで終わっているか

### Echoroo Lite 側
- **設計ロック済み**([plan.md](plan.md)): Tauri + SvelteKit + Python sidecar
  (FastAPI, PyInstaller)+ SQLite/sqlite-vec。single-user・オフライン・認証なし。
  マイルストーン M0(縦切り PoC)→ M1(コア)→ M2(評価)→ M3(クイズ・習熟度)。
- **M0 検証 1: ✅ GO** — Perch V2 の自己変換(tf2onnx)は不可能(StableHLO
  XlaCallModule)だが、コミュニティ変換 **`justinchuby/Perch-onnx` の
  `perch_v2_no_dft.onnx`(390 MB)** が onnxruntime CPU でドロップイン動作。
  **parity cosine 1.0 / max abs diff 1.3e-5、30 分録音 ~12 秒(8 threads)**。
  TensorFlow 同梱が不要になる。詳細は [../poc/CONVERSION_NOTES.md](../poc/CONVERSION_NOTES.md)。
- **M0 検証 2: ⏳ 未了** — test1 データセット(30 分録音)での類似検索の主観品質
  確認。**test1 の所在をユーザーに確認すること**(前回 Linux 機では見つからず)。
- **PyInstaller freeze(Windows)未検証** — onnxruntime は TF と違い freeze が
  素直なので低リスク見込みだが、実証がまだ。→ §4 の CI 戦略で潰す。

### full Echoroo 側(2026-08〜09 の大きな進展、Lite に直接効く)
種名管理を全面再設計した(WS-A v2、PR #259〜#267、全 merge 済み)。Lite が
そのまま使える成果物:

- **和名の版付きデータ一式**が full リポジトリに同梱されている:
  `apps/api/echoroo/data/vernacular/`
  - `ioc_ja.csv` — IOC World Bird List **15.2** Multilingual の和名 **10,722 種**
    (CC BY 3.0、出典 meta.json 併置)。IOC は 15.2 が最終版(以後 AviList に統合)。
  - `birdnet_crosswalk.csv` — BirdNET V2.4 ラベル → AviList v2025b 学名の対応
    **6,384/6,522**(+AvibaseID, Cornell code。CC BY 4.0)。BirdNET(eBird/Clements)
    と IOC 系の属名差(Accipiter→Tachyspiza 等)をこれで吸収する。
  - `overrides.csv` — 日本関連の手動対応 4 件(Parus minor→Parus cinereus 等)。
  - 再生成スクリプト: `apps/api/scripts/build_vernacular_bundle.py`
    (入力 XLSX の URL は同スクリプトと meta.json に記載)。
- **日本鳥類目録 改訂第8版**(国内 690 種の権威和名): 公式 XLSX
  (https://ornithology.jp/checklist.html の `jpbirdlist8ed_ver1.xlsx`)を
  `apps/api/scripts/convert_osj_checklist.py` で CSV 化して使う。
  **ライセンス上、アプリへの同梱は不可**(「二次的に加工して使用可」のみ)。
  Lite でも「運用者が自分で入手して読み込む」方式を踏襲する。
- **和名の優先順位設計**(表示品質=データ整合性という思想):
  `authority(目録8版) > user > ioc > inaturalist > gbif > birdnet`。
  Lite の表示解決も同じ順位を実装する(full の
  `apps/api/echoroo/services/vernacular.py` が参照実装)。
- **分類の同一性設計**: ローカル ID を不変 PK にし、外部 ID(COL XR など)は
  張り替え可能なメタデータとして持つ。full は COL XR 照合
  (`GET https://api.gbif.org/v2/species/match?checklistKey=xcol`、release pin 必須)
  まで実装済み — Lite ではオフライン優先なので照合はせず、**種マスタ生成時に
  full 側の照合結果(col_xr_id 等)を焼き込む**だけでよい。

---

## 2. 確定済みの設計判断(plan.md より再掲+更新)

| 項目 | 決定 |
|---|---|
| リポジトリ | 独立リポジトリ `0kam/echoroo-lite`(このリポジトリ)。full へは merge しない |
| 対象 OS | **Windows が第一ターゲット**、macOS/Linux は後続(開発は Mac で行う) |
| ML | 2 パス: BirdNET(検出, tflite, 3.0s)+ Perch ONNX(埋め込み 1536 次元, 5.0s)。geo フィルタ必須 |
| ベクタ検索 | sqlite-vec(1536 次元、総当たり KNN で十分) |
| DB | ユーザー選択フォルダ内の中央 SQLite 1 個(習熟度が縦断データのため) |
| 階層 | Site > Dataset(Project 層は廃止) |
| 音源 | 参照(リンク)のみ、コピーしない。「見つからない→再リンク」導線 |
| 輸出 | camtrapDP(Site→Data Package, Dataset→Deployment) |
| 種マスタ | BirdNET 種セット + 手動追加。学名(BirdNET/Clements 表記)がビジネスキー |
| 削除するもの | 認証/2FA/BFF/権限/投票/モデル学習/KMS/S3/Celery/Redis/Postgres |
| **(新規提案)Lite の種 ID** | **UUIDv5(固定 namespace, scientific_name) の決定的 UUID** — どのインストールでも同じ種は同じ ID になり、将来の Lite↔full/Lite↔Lite データ交換が学名照合なしで成立する。full 側はランダム UUID のままで良い(交換時は学名/col_xr_id で突合)。※未承認の提案 — 着手時にユーザーに一言確認 |

---

## 3. Mac 環境セットアップ

```bash
# 前提ツール
xcode-select --install                # Xcode Command Line Tools
brew install git gh node uv rustup    # rustup 経由で Rust (Tauri v2 要件)
rustup-init -y && source ~/.cargo/env
cargo install tauri-cli --version '^2'

# リポジトリ
gh auth login
git clone git@github.com:0kam/echoroo-lite.git ~/Projects/echoroo-lite
git clone git@github.com:0kam/echoroo.git ~/Projects/echoroo   # 参照/サルベージ用(読み取り専用の位置づけ)

# sidecar 用 Python
uv python install 3.11
```

メモ:
- Apple Silicon で onnxruntime(CPU)・tflite-runtime とも動く。Perch ONNX の
  実行確認は Mac 上でやり直す(数値 parity は poc のベンチスクリプトを流用)。
- モデル類(`perch_v2_no_dft.onnx` 390MB、BirdNET tflite)はリポジトリに入れない
  (.gitignore 済)。初回に birdnet pip / HuggingFace から取得するスクリプトを書く。
- チャットは日本語、**コード・コメント・アプリ内文言は英語**(従来ルール)。

---

## 4. 進め方(推奨順序)

### Step 1: scaffold(最初のセッション)
- `apps/desktop`(Tauri v2)/ `apps/web`(SvelteKit static)/ `apps/sidecar`
  (FastAPI + uv 管理)の骨組み。
- sidecar 起動プロトコル: 空きポートに bind → stdout にポートを 1 行出力 →
  Tauri が読んで UI に渡す(plan.md §2)。ここまでを「Hello から往復」で動かす。
- **CI を最初に置く**: GitHub Actions で
  (a) `macos-latest` — lint + unit test + Tauri build、
  (b) **`windows-latest` — sidecar を PyInstaller で freeze → 起動 smoke**。
  Windows 実機を持たずに第一ターゲットの freeze リスクを毎コミット検証できる。
  これが M0 の残リスク(PyInstaller×onnxruntime)の実質的な検証手段。

### Step 2: 種マスタ生成
- full リポジトリの `apps/api/echoroo/data/vernacular/` 成果物(§1)から、Lite 同梱の
  SQLite 種マスタ(taxa + vernacular_names)を生成するスクリプトを `tools/` に作成。
  UUIDv5 方式(§2 表)を採用する場合はここで確定。
- 目録8版はビルド時に**同梱しない**。設定画面 or CLI で運用者の XLSX/CSV を
  読み込む導線を M1 で作る(full の converter を流用)。

### Step 3: M0 縦切り(リスク高い順、plan.md §6)
録音 1 本 import → BirdNET 検出(geo フィルタ)→ Perch ONNX 埋め込み →
sqlite-vec 格納 → UI 一覧表示 → 類似検索、を最小 UI で貫通させる。
- **検証 2 = test1 データセットでの類似検索の主観品質**。test1 を Mac に持ち込む
  こと(所在は要確認)。
- サルベージ元(full リポジトリ内、TF→ONNX 等の改変前提):
  `ml/birdnet_wrapper.py`(検出+geo)/ `ml/perch/direct_inference.py`(埋め込み)/
  `workers/upload_tasks.py::_parse_datetime_from_filename`(ファイル名日時)/
  `services/vernacular.py`(和名解決)/ `api/v1/xeno_canto.py`(XC proxy、任意機能)。

### Step 4: M1 以降
M1 コア(閲覧/検索/種管理/camtrapDP)→ M2 評価(P/R/F1+閾値カーブ)→
M3 クイズ・習熟度(ToriTore は新規実装)。UI は full の Svelte コンポーネントを
移植し、データ層だけ localhost sidecar に張り替える。

---

## 5. 未決事項・確認事項(次セッションの冒頭で)

1. **test1 データセットの所在**(M0 検証 2 のブロッカー。NAS?別マシン?)
2. Lite の種 ID を UUIDv5 決定方式にするか(§2 の提案)
3. GitHub Actions の Windows ビルドを最初から回すか(推奨: Yes)
4. (full 側の積み残し、Lite とは独立)taxon 無しタグの検出のマスキング既定値
   (H3_RES_9 のまま/保守的上限を設けるか)— full PR #264 参照

## 6. full 側の参照メモ(困ったときに見る場所)

- 種名管理の設計と経緯: full リポの PR #259〜#267 の本文が最も詳しい
- COL XR API の使い方(checklistKey=xcol 必須、NONE の confidence 100 は
  スコアではない等の罠): PR #265 本文
- 和名データの出典・ライセンス一覧: full の `THIRD_PARTY_LICENSES.md`
- 運用手順(seed → 同梱和名 → 目録8版 → bulk-import): full の
  `docs/onboarding/internal_preview.md` のタクソノミー節
