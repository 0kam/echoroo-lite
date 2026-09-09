# Echoroo Lite 開発ハンドアウト(2026-09-08 版)

> 2026-09-09更新: 年間相当の実音声反復負荷はM4 Proで検索・報告出力まで4時間8分25秒。
> Windowsはサービス凍結・EXE/MSI作成、NSIS導入・画面起動・接続表示・正常終了、モデル不要のサービス起動・26種クイズをCIで確認済み。
> 日本語切り替え・再起動後の保持とモデル取得も通過。実推論はBirdNETの処理用プロセス起動時に停止し、修正を検査中。以下の9月8日時点の記述と区別し、
> [実装状況](implementation-status.md) と [Windows検証結果](windows-preview.md) を参照する。

> 2026-09-08 Mac 側レビュー追記: 初期範囲を合意し、実装に着手した。
> まず [implementation-status.md](implementation-status.md) の実装済み範囲と残作業を読む。
> 以下の「設計ロック済み」は従来の状態を示す。Perch の約12秒/30分は合成音声の
> 推論速度からの換算であり全工程実測ではない。Windows 配布は未検証。
> BirdNET runtime は下記の Mac 実音源 PoC で LiteRT 経路を確認。提案と合意済み事項を区別する。
> 同日のユーザー確認: Site > Dataset を維持し、Project は追加しない。
> Perch は埋め込み検索に加えて種分類結果も表示し、BirdNET と比較する。
> 初回は取り込み・解析・検索を優先。M4 Pro で 1 地点年 292 時間の処理を検証する。
> テスト音源の所在も解消: `/Users/okamoto/Music/whombat_data/test`。
> 実測では 30 分 WAV × 24 本（12 時間）。地点・日本時間もユーザー確認済み。
> 実行結果と再現手順は [../poc/REAL_AUDIO_NOTES.md](../poc/REAL_AUDIO_NOTES.md) を参照。
>
> 同日の追加合意: **11/28 に Windows PC で展示**。音源・解析結果・報告図の表示、検索、
> クイズが必須で、検証操作はオプション。1 地点年 292 時間を **12 時間以内**に処理できれば
> 最低限合格。展示 PC は必要スペックの計測後に用意する予定。
> 検出と検索で目的・出力を分ける。種一覧・根拠音・spiral plot・CSV を提供する。
> 通常の検出結果の逐次訂正を省き、検証と検索標本への判定に集約する初期案を了承済み。
> 実装着手後の現状は [implementation-status.md](implementation-status.md) を最初に読む。
> 初期クイズは **`../koe-e-lab/sample_data` の26種**を使用。26 MP3 と対応画像を確認済み。
> オリジナル音源追加と UI 範囲選択、NumPy / SciPy による混合が合意済み。
> [reporting-design.md](reporting-design.md) に詳細案・既存 full の読み取り結果、
> [validation-design.md](validation-design.md) に検証・能力評価の設計を記録した。
> 以下の古い「クイズは M3」「報告図の初回要否」等は、この展示範囲に読み替える。
> 種名と外観も更新済み: **BirdNET の一般名＋ユーザー追加**を採用し、選択言語の一般名と
> 学名を併記する。ローカル UUIDv4 を保持し、full の多段の分類・和名解決は移植しない。
> full の配色・ロゴを流用した。詳細は [species-and-branding.md](species-and-branding.md)。

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
- **M0 検証 2: ⏳ 未了** — 類似検索の主観品質確認。
  今回はユーザー指定の `whombat_data/test`（12 時間）を使う。元の test1 の探索は不要。
- **PyInstaller freeze(Windows)未検証** — onnxruntime は TF と違い freeze が
  素直なので低リスク見込みだが、実証がまだ。→ §4 の CI 戦略で潰す。

### full Echoroo 側(2026-08〜09 の参考情報)
種名管理を全面再設計した(WS-A v2、PR #259〜#267、全 merge 済み)。
以下は full の仕組みと旧移植案の記録。Lite は同日の追加方針により BirdNET＋ユーザー追加
としたため、以下の「Lite でも踏襲」「同じ順位を実装」「焼き込む」は現在の実装指示ではない。

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
| ML | 2 パス: BirdNET V2.4(検出, LiteRT, 3.0s)+ Perch ONNX(分類と埋め込み 1536 次元, 5.0s)。アプリは両モデルの地域フィルタを常時適用し、切り替え UI は設けない。Perch は同じ地域リストと学名一致で表示対象を限定。全クラス softmax 後の0〜1を表示し、元 logit を保持 |
| ベクタ検索 | sqlite-vec(1536 次元)。約21万ベクトル/地点年で総当たり KNN の性能を要実測 |
| DB | ユーザー選択フォルダ内の中央 SQLite 1 個(習熟度が縦断データのため) |
| 階層 | Site > Dataset(Project 層は廃止) |
| 音源 | 参照(リンク)のみ、コピーしない。「見つからない→再リンク」導線 |
| 輸出 | camtrapDP(Site→Data Package, Dataset→Deployment) |
| 種マスタ | BirdNET の日英一般名＋ユーザー追加。Perch の生物ラベルも保持し、学名の完全一致で名前を共有。種はローカル UUIDv4 で保持 |
| 削除するもの | 認証/2FA/BFF/権限/投票/モデル学習/KMS/S3/Celery/Redis/Postgres |
| Lite の種 ID | 初回作成時に UUIDv4 を保存し、名前の変更・再起動で維持する。学名から生成する UUIDv5 の旧提案は採用しない。インストール間で同一 ID になる保証は設けない |

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
- Mac の onnxruntime(CPU) と BirdNET 用 runtime は実行確認が必要。
  従来の `tflite-runtime` は Windows/macOS 向け公式 wheel がないため、動作済みと
  みなさない([公式資料](https://developers.google.com/edge/litert/microcontrollers/python))。
  Perch の既存 parity スクリプトは Linux 固定パスの移植が必要。
- モデル類(`perch_v2_no_dft.onnx` 390MB、BirdNET tflite)はリポジトリに入れない
  (.gitignore 済)。初回に birdnet pip / HuggingFace から取得するスクリプトを書く。
- チャットは日本語、コード・コメントは英語。**国内イベント向け初回プレビューから
  日本語・英語 UI を提供**し、日本語環境では日本語を初期表示する。
  **UI は初めから文言を翻訳リソースへ分離し、設定でロケールを変更可能な設計にする**
  （ユーザー指定）。日本語は後回しにせず、進捗・エラーを含むプレビューの操作全体を対象にする。
- 録音日時は基本的に**設置場所のローカルタイム**。Site の IANA タイムゾーンと
  アプリの表示ロケールは別設定とし、言語変更で録音時刻や日別集計が変わらないようにする。

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
- `scripts/build_species_names.py` で既存のモデルラベルから名前データを生成する。
  `apps/sidecar/data/species.json` を同梱し、SQLite にはローカル ID とユーザー上書きを保存する。
- 「種名の管理」で検索・追加・日英名の上書きができる。空欄保存で同梱名へ戻る。
  目録8版の一括読み込み等は初期版の範囲外。

### Step 3: M0 縦切り(リスク高い順、plan.md §6)
録音 1 本 import → BirdNET 検出(geo フィルタ)→ Perch ONNX 埋め込み →
sqlite-vec 格納 → UI 一覧表示 → 類似検索、を最小 UI で貫通させる。
- **検証 2 = test1 データセットでの類似検索の主観品質**。test1 を Mac に持ち込む
  こと(所在は要確認)。
- サルベージ元(full リポジトリ内、TF→ONNX 等の改変前提):
  `ml/birdnet_wrapper.py`(検出+geo)/ `ml/perch/direct_inference.py`(埋め込み)/
  `workers/upload_tasks.py::_parse_datetime_from_filename`(ファイル名日時)/
  `api/v1/xeno_canto.py`(XC proxy、任意機能)。和名解決は上記の Lite 用方針で実装済み。

### Step 4: M1 以降
M1 コア(閲覧/検索/種管理/camtrapDP)→ M2 評価(P/R/F1+閾値カーブ)→
M3 クイズ・習熟度(ToriTore は新規実装)。UI は full の Svelte コンポーネントを
移植し、データ層だけ localhost sidecar に張り替える。

---

## 5. 未決事項・確認事項(次セッションの冒頭で)

1. **テスト音源は所在解決済み**。`whombat_data/test` で類似検索の主観品質を確認する。
2. 種名は BirdNET＋ユーザー追加、ローカル UUIDv4 で実装済み（§2）。
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
