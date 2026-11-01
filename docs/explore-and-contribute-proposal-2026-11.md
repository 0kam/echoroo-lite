# 探索ページと提供用の書き出しの設計案（2026-11-01、未合意）

日本鳥学会 2026 のフィードバック（[requirements.md](requirements.md) の 2026-11-01 相談の段落）への対応として、
「探す → 聞いて確かめる → 提供する」を一つの流れにする 2 つの機能の設計案。
**この文書は提案であり、ユーザーの了承前の内容を合意とは扱わない。**

2026-11-01 にユーザーが決めたこと（チャットでの回答）：

- 実装は (a) 探索ページ → (b) 提供用の書き出しの順。設計は両方まとめて行う。
- クラスタを作る対象は**全区間**（スコアによる絞り込みはしない。どの種のスコアも低い
  「モデルが苦手な音」を落とさないため）。
- 投影・クラスタリングの方法は、Kaleidoscope Pro の方式を含めて調査してから決める（下の調査結果）。
- 座標のぼかしは **4 段階**（そのまま／約 10 km／都道府県単位／出さない）。
- **XC へメールで一括投稿する経路は採らない**（同日、初版への指摘で決定）。理由：Echoroo Lite からの
  提供は細切れになりうる、利用者が英語のメールのやり取りをできるとは想定できない、想定利用者は
  研究者とは限らない。提供は研究機関への受け渡しを主経路にする（下の (b) を改訂済み）。
- クラスタリングの方式は k-means と決め打ちにせず、文献調査（下記）を踏まえた候補 2 つを
  富士の全区間注釈データで比較してから決める。

## 全体の流れ

```
(a) 音の地図（探索ページ）        既存の機能                    (b) 提供用の書き出し
   クラスタを眺める・聞く   →   似た声の検索 → 聞いて確かめる  →   確認済みの音を
   気になる音を見つける          （確認済みの音が増える）            研究機関などへ渡せる形に
```

- 探索ページはレポートへの主な流れから分け、初めての人の動線には出さない
  （サイドバーの「作業の流れ」には入れない）。
- 確認の仕組みは新しく作らない。探索ページで見つけた音は、既存の「似た声の検索」
  （参照音に指定して検索 → 一音ずつ確認）へ渡す。

## 既存の土台（2026-11-01 調査）

| 部品 | 場所 | 使い方 |
|---|---|---|
| 全区間の埋め込み | SQLite の `vectors`（sqlite-vec、1536 次元 float32、rowid = `segments.id`） | 256 件ずつ流し読みするローダー（`custom_models._vector_batches` と同型）を再利用 |
| 規模 | 1 地点 1 年で約 21 万区間（292 時間、`docs/annual-benchmark-2026-09.md`） | float32 で約 1.3 GB。全件をメモリに置かず流し読みで処理 |
| 区間のスコア | `<data>/analyses/<job>/<rec>-<engine>.npz` | クラスタの「鳥らしさ」の手がかり（最大スコアの平均）に使う |
| 再生とスペクトログラム | `AudioRegion.svelte`（`fixedRange compact` で区間再生） | クラスタ内の音の一覧にそのまま使う |
| 確認済みの音 | `presence_marks`（いるか・検索の確認、3/5 秒）と `window_answers`（精度確認の target、15 秒） | (b) の書き出し候補。`presence.summaries()` が両方をまとめる |
| 地点の座標・タイムゾーン | `sites(latitude, longitude, timezone)` | (b) のメタデータ。日時は地点のタイムゾーンで出す |
| クリップの切り出し | `audio.read_interval(native_rate=True)` ＋ `workflow_export.save_audio` | (b) の WAV 書き出しに再利用（sha256 付き） |
| CSV 書き出し | `reports.csv_bytes`（UTF-8 BOM、数式インジェクション対策済み） | (b) の CSV に再利用 |
| sidecar の依存 | numpy・scipy あり。scikit-learn・UMAP・numba は**なし** | 方法の選択に影響（下記） |

録音者・ライセンス・標高は今のデータベースのどこにも無い。(b) の書き出し時に入力してもらう。

## 方法の調査（2026-11-01）

### Kaleidoscope Pro のクラスタ分析の方式

Kaleidoscope Pro User Guide（2024-11-11 版、Wildlife Acoustics の
[Knowledge Base](https://answers.wildlifeacoustics.com/v/u/en-US/Kaleidoscope-User-Guide)
から取得した PDF の "Cluster Analysis Theory"）より：

1. **信号検出が先**。周波数範囲・長さ・音節間の最大間隔で「候補の鳴き声（フレーズ）」を切り出す。
   背景雑音のスペクトルは移動平均で推定する。
2. **特徴量**はフレームごとの正規化スペクトルの DCT 係数（可変長の列）。
3. 全データで HMM を学習（K-means で初期化、Viterbi で学習）し、各鳴き声を HMM に対する
   **Fisher スコア**で固定長ベクトルにして単位長に正規化する（距離は 0〜2）。
4. **クラスタリングは適応型**：近いクラスタ中心（距離が閾値以内）があればそこへ、なければ新しい
   クラスタを作る。クラスタは「よくある音」順（Cluster.00 が最頻のパターン）、クラスタ内は中心に
   近い順に並ぶ。既定では中心から距離 1 を超える検出は一覧から除外し、珍しい音を探すときは
   1.5〜2 に上げる使い方が案内されている。

つまり Kaleidoscope の本体は「埋め込み（Fisher スコア）＋ K-means 系の適応クラスタ＋
中心に近い順の一覧」で、評判の良さは散布図ではなく**クラスタの一覧と即時再生**の操作にある。
Perch の埋め込みは DCT＋Fisher スコアより新しい表現なので、同じ型の操作を
既存の埋め込みでそのまま再現できる。

### 文献の調査（2026-11-01、Consensus で検索）

k-means で良いかという問いへの、文献からの答え：

- **k-means 単独は比較のベースライン扱いで、密度ベースの手法に劣ることが多い。**
  齧歯類の鳴き声では UMAP＋HDBSCAN＋階層クラスタの組み合わせが k-means・GMM・スペクトラル
  クラスタリングなどを内部指標すべてで上回った
  （[DCUSV](https://consensus.app/papers/details/2d4eab9d7db45a7db2b1f0897ac95c1d/?utm_source=claude_desktop)、
  Anis et al. 2026, Scientific Reports）。マナティーの個体識別でも PaCMAP＋HDBSCAN が
  K-means・階層クラスタより良かった
  （[Merchan et al. 2024](https://consensus.app/papers/details/bab717c30eb55efe95d150c75a818726/?utm_source=claude_desktop)）。
- **今回の使い方（検出を挟まない全区間のクラスタリング）に最も近い研究も UMAP＋HDBSCAN。**
  オーストラリアの 6 地点・19,230 個の 4.5 秒区間を MFCC＋UMAP＋HDBSCAN で整理し、
  人の注釈との一致 F1 = 83.7–98.3%
  （[Napier et al. 2026, Ecological Informatics](https://consensus.app/papers/details/ca038bcc398a555f8d75aca7dd616695/?utm_source=claude_desktop)）。
  2,764 時間からフクロウ類の声を整理した例も UMAP＋HDBSCAN
  （[Alexander et al. 2025](https://consensus.app/papers/details/e275d0c554765bcb8b8259dcc7fc4245/?utm_source=claude_desktop)）。
- **クラスタの質はアルゴリズムより表現（埋め込み）の質で決まる**という比較研究があり
  （[Best et al. 2023, PLOS ONE](https://consensus.app/papers/details/29a885c7547050288e46ab986115427b/?utm_source=claude_desktop)、
  [Williams et al. 2024](https://consensus.app/papers/details/71f96afa00fb5919b8cffd57c61e5dc0/?utm_source=claude_desktop)）、
  Perch の埋め込みを使う前提はこの点で妥当。
- **注意：鳴き声の空間は連続的で、はっきりした「型」に分かれるとは限らない**
  （[Goffinet et al. 2021, eLife](https://consensus.app/papers/details/90b8934c92f05076880109c56a053878/?utm_source=claude_desktop)）。
  画面では「種類」ではなく「まとまり」と呼び、探すための便宜であって分類ではないと扱う
  （モックの文言はこの方針）。

k をどう決めるかへの、文献からの答え：

- k-means を使う場合、k は内部指標で選ぶのが定石。最も標準的なのはシルエット係数
  （[Rousseeuw 1987](https://consensus.app/papers/details/4eb6ded5335759cd82803787585d701d/?utm_source=claude_desktop)）で、
  近年の比較でもシルエットと Davies-Bouldin が Dunn・Calinski-Harabasz・Gap statistic より
  当てになるとされる
  （[Chicco et al. 2025](https://consensus.app/papers/details/e439ca1dd26a539e8e5834f3336dc8d2/?utm_source=claude_desktop)、
  自動 k 選択のベンチでは CH とシルエットが安定
  [Ikotun et al. 2025](https://consensus.app/papers/details/15763501f4895adab2b64477fdc1927e/?utm_source=claude_desktop)）。
  指標の山がはっきりしないときは k 自体が不確かと読む
  （[Pakgohar et al. 2024](https://consensus.app/papers/details/309bca00936b51d18c4ce7bb7e5bb1a9/?utm_source=claude_desktop)）。
- ただし**正解ラベルがあるなら内部指標より外部検証で選ぶべき**という指摘がある
  （[Yerbury et al. 2024, ACM TKDD](https://consensus.app/papers/details/dcaa58f1c8c85540a47b3ec917f26d67/?utm_source=claude_desktop)）。
  うちには富士の全区間注釈（60 クリップ・2,160 区間、種の正解付き）があるので、これが使える。
- HDBSCAN には k がそもそも無い（最小クラスタサイズなどの少数のパラメータだけで、
  クラスタ数はデータから決まり、どこにも入らない音は「ノイズ」として外に置ける）。
  これが文献で HDBSCAN が選ばれる理由の一つ。

依存の制約：UMAP・PaCMAP は numba（＋llvmlite、約 +100–150 MB）依存で PyInstaller の
onefile 配布と相性が悪く、採らない（[PaCMAP の依存](https://depscope.dev/pkg/pypi/pacmap)）。
HDBSCAN は scikit-learn 1.3 以降に入っており、t-SNE の高速実装
[openTSNE](https://pypi.org/pypi/openTSNE)（Cython、numba 不要、依存は numpy・scipy・scikit-learn）と
合わせても約 +40–50 MB で済む。

### 提案：候補 2 つを富士のデータで比較してから決める

文献どおりなら B が有力だが、+40–50 MB の依存は「必要性を示してから」（AGENTS.md）。
そこで**外部検証で決める**（上の Yerbury et al. の推奨とも一致）：

| 候補 | 2 次元投影 | クラスタ | k の決め方 | 追加依存 |
|---|---|---|---|---|
| **A（基準）** | PCA（第 1・2 主成分） | PCA 50 次元での k-means | シルエット係数（部分標本）で k の候補列から自動選択。利用者には見せない | なし |
| **B（文献の定石形）** | openTSNE で 2 次元 | その 2 次元での HDBSCAN | k なし（最小クラスタサイズは区間数から自動） | scikit-learn ＋ openTSNE（約 +40–50 MB） |

- 比較は実装の最初の段階として `poc/` の検証スクリプトで行う（アプリには入れない）。
  データは富士の全区間注釈（2,160 区間・種の正解付き）と、速度確認用に 1 地点 1 年相当
  （約 21 万区間）。
- 指標：(1) 種ラベルとの一致（homogeneity・NMI）、(2) 「上位のまとまりから順に聞いたとき、
  各種の最初の 1 音に届くまでに開くまとまりの数」（探索の実効性）、(3) ノイズ扱いになる区間の
  割合、(4) 実行時間とメモリ。
- **B が (1)(2) で明確に上回れば B を採り、依存の追加を受け入れる。差が小さければ A。**
  結果はこの文書に追記し、ユーザーの確認を得てから本実装に進む。
- どちらでも UI は同じ（散布図＋まとまりの一覧）。B なら散布図の座標と
  クラスタが同じ空間になり、見た目のまとまり＝一覧のまとまりになる利点もある。

計算の設計（sidecar、解析ジョブと同じ単一ジョブの仕組みに載せる。A・B 共通部分）：

1. メインの AI の全区間ベクトルを 256 件ずつ読み、共分散行列（1536×1536）を累積 → 固有分解で
   上位 50 主成分（PCA は厳密、サンプリング不要）。
2. もう一度流し読みして 50 次元へ射影（float32、21 万×50 ≒ 42 MB。以降はメモリ上で処理）。
3. ベンチで選んだ方式でクラスタと 2 次元座標を計算（A：k-means＋PCA 1・2 軸、
   B：openTSNE＋HDBSCAN。ノイズ扱いの区間は「その他」として一覧の末尾に出す）。
4. クラスタごとに、件数・中心に近い代表区間・最大スコアの平均（鳥らしさの目安）・
   AI の最頻の種（候補の名前）を保存する。
5. 録音の追加・再解析で古くなったら「作り直す」を出す（自動では作り直さない）。

## (a) 探索ページ「音の地図」

- ルート：`/datasets/[id]/explore`。入口は地点の結果ページの下部に 1 つ
  （「音の地図（実験的）— AI の種名に頼らずに、録音の中の音をながめて聞けます」）。
  サイドバーの「作業の流れ」には入れない。
- ページ名の案：日本語「音の地図」、英語 "Sound map"。

画面（モック：[prototypes/explore-mock.html](prototypes/explore-mock.html)）：

- 初回は空の状態＋「地図を作る」ボタン＋所要時間の目安（約◯分。解析と同じく進み具合を表示、
  実行中は他のジョブと同様に 1 つだけ）。
- 作成後は左右 2 列：
  - **左：散布図**（Canvas 2D に全点を描く。色＝クラスタ。ホイールで拡大、ドラッグで移動、
    クラスタ一覧と連動して強調）。点データは独自のバイナリ（float32 配列）で 1 回取得
    （21 万点で数 MB。JSON にしない）。
  - **右：クラスタの一覧**（多い順）。各行＝件数・代表音のスペクトログラムと再生・
    AI の一番の候補（あれば参考として）・鳥らしさの目安。行を押すと展開。
- クラスタを開くと、中の音を**中心に近い順**に 20 件ずつ表示（`AudioRegion` の
  `fixedRange compact`）。各行に時刻（地点の時刻）と「この録音を開く」。
- 行の操作は 1 つだけ：**「この音に似た声を探す」**→ 既存の類似検索ページへ、その区間を
  参照音として渡す（確認の流れは既存のまま）。探索ページ自体は読むだけで、回答や印は付けない。
- 文言は locale キー（ja/en）を最初から用意し、文字の役割は
  [typography-2026-11.md](typography-2026-11.md) の 7 役割に合わせる。

新しいテーブルと API（sidecar）：

```
explore_runs(id, site_id, engine, model_sha, params, created_at)
explore_points(run_id, segment_id, x, y, cluster, dist)
explore_clusters(run_id, cluster, size, rep_segment_id, score_mean, top_species)
```

- `POST /explore`（作成・作り直し。実行中は 409 `job_busy`）
- `GET /explore`（状態とクラスタ一覧）
- `GET /explore/points`（バイナリの点データ）
- `GET /explore/clusters/{n}/segments?offset=`（クラスタ内の音、ページング）

## (b) 提供用の書き出し

### 提供の経路（2026-11-01 改訂）

利用者が XC とメールでやり取りする経路（FAQ にある一括投稿）は**採らない**（ユーザー決定。
理由：Echoroo Lite からの提供は細切れになりうる、利用者が英語のメールのやり取りをできるとは
想定できない、想定利用者は研究者とは限らない）。

主経路は**研究機関への受け渡し**にする。学会フィードバックの「研究機関に一時集約して
Zenodo 等で公開する」流れに合わせ、アプリは日本語だけで完結する「提供パッケージ」を
作るところまでを受け持つ。細切れの提供は機関側でまとめる前提とし、機関側の受け口
（どこへ・どうやって渡すか）はアプリの外の課題として未決に残す（下の論点）。

パッケージの一覧表（CSV）の列は、XC の公開テンプレート
「[Xeno-canto batch upload template (v2. 2022 reviewed: 2024-1)](https://docs.google.com/spreadsheets/d/1aMLIF97Im-vX-Py-2bRAANpniTByBGlDC8HYPuVulVs)」
互換のままにする。理由：(1) 公開されていて列の定義が明確。(2) 機関が集約後にそのまま XC へ
一括提供したり（[XC の FAQ](https://xeno-canto.org/help/FAQ) の一括投稿を使うのは機関であって
利用者ではない）、Zenodo 用に変換したりできる。(3) XC のアカウントを持つ利用者が自分で
ウェブフォームから投稿するときの転記元にもなる。

テンプレートの 29 列と、こちらで埋められるもの：

| 列 | 必須 | 埋め方 |
|---|---|---|
| filename | ○ | 自動（`<地点>_<日時>_<学名>.wav`、重複しない連番付き） |
| latitude / longitude | 任意 | 地点の座標に**ぼかしの段階**を適用（下記） |
| country | ○ | "Japan"（変更可） |
| location name | ○ | 地点名を初期値に（ぼかし時は書き換えを促す） |
| elevation | ○ | 保存していないため書き出し時に入力（地点ごとに記憶） |
| recording date / time of day | ○ | 録音時刻＋区間の開始（**地点のタイムゾーン**、YYYY-MM-DD / hh:mm） |
| license | ○ | 6 種の CC から選択（全クリップ共通） |
| recordist name | ○ | 書き出し時に入力して記憶。「名前を出さない」で空欄にできる（下記） |
| type | ○ | "single species" |
| automatic recording | ○ | "yes"（自動録音機のため） |
| genus / species | ○ | 確認済みの種の学名を分割 |
| animal seen? | ○ | "no" |
| recording method | ○ | "field recording" |
| playback used? | ○ | "no" |
| quality | ○ | A–E を全クリップ共通で選択（既定 C。CSV なので後から個別に直せる） |
| sound type(s)・sex(es)・life stage(s)・subspecies・background species ほか | 任意 | 空欄（推測で埋めない） |
| remarks | 任意 | 自動の説明（確認に使ったアプリとモデル・スコア、確認した区間、座標をぼかした旨） |

### 匿名化（希少種向け）

- **座標のぼかし（4 段階）**：
  1. そのまま（小数 4 桁）
  2. おおよそ（0.1 度 ≒ 約 10 km に丸める）
  3. 都道府県まで（座標は出さず、location name を「都道府県名, Japan」にする。
     都道府県は地点座標から最寄りを推定して初期選択し、利用者が直せる
     47 件の選択式。境界の近くでは推定がずれ得るため確認を促す）
  4. 出さない（座標・地名とも空欄。location name は "Japan"）
  - XC の仕様上、座標は任意項目なので 3・4 も受理される。XC 自体にも希少種の公開を制限する
    仕組みがある（FAQ "Why are recordings of certain species restricted?"）。
- **提供者の匿名化**：「名前を出さない」チェック 1 つ。選ぶと CSV の recordist name は
  空欄になり、README に「提供者は非公開（受け取った機関で管理する）」と記す。
  提供先の選択肢は作らない（経路は 1 つ、パッケージも 1 種類）。

### 画面と流れ（モック：[prototypes/contribute-mock.html](prototypes/contribute-mock.html)）

- ルート：`/datasets/[id]/contribute`。入口は地点の結果ページの「確認できた種」の近くに
  「確認した音を提供する」。
- ① 確認済みの音の一覧（種・日時・確認方法つき、チェックで選ぶ。既定は全選択。
  以前のパッケージに入れた音には「書き出し済み」の印を付け、二重の提供を避けられるようにする）
  → ② 録音の情報（録音者名と「名前を出さない」・ライセンス・標高・座標のぼかし）
  → ③ 確認して書き出し（フォルダ or ZIP。既存のレポート書き出しと同じ保存先選択と履歴）。
- パッケージの中身：
  ```
  echoroo-contribution-<地点>-<日付>/
    audio/<filename>.wav        … 確認した区間の前後に 5 秒ずつ足した切り出し（元のサンプリング周波数）
    metadata.csv                … XC テンプレート互換の 29 列（UTF-8 BOM）
    README.txt                  … 日本語と英語の説明（中身の構成、機関に渡すときの扱い、
                                   自分で XC のフォームから投稿する人向けの列の対応）
    manifest.json               … 各ファイルの sha256（既存の書き出しと同じ）
  ```
- クリップは確認済みの区間（3〜15 秒）だけだと文脈が無さすぎるため、前後 5 秒ずつ足して
  書き出す（録音の端では切り詰め）。確認した区間そのものは remarks に秒で記す。
  固定の既定値とし、設定は付けない。

### API（sidecar）

- `GET /contributions/candidates`（確認済みの音と埋められるメタデータの一覧）
- `POST /contributions`（選択＋入力 → パッケージ作成。途中失敗で中間ファイルを残さない、
  既存の staging→rename 方式）
- `GET /contributions` / `POST /contributions/{id}/open`（履歴と開く）
- 録音者名・標高・前回のライセンスは `<data>` 内に記憶して次回の初期値にする。

## 範囲外・やらないこと

- Kinglet と Perch の検索品質の比較（相談段落の (c)）は別の作業。
- 探索ページからの直接の種の確定・印付け（既存の検索→確認の流れを使う）。
- XC API 経由の自動アップロード（API v3 は検索・ダウンロード専用。2026-11-01 確認）と、
  利用者によるメールでの一括投稿（却下。上の「提供の経路」）。
- UMAP・PaCMAP（numba 依存のため）。scikit-learn＋openTSNE は比較ベンチで B が明確に
  上回った場合だけ入れる。

## 未決の論点（ユーザーに確認したいこと）

1. ライセンスの既定値（案：CC BY-NC-SA 4.0。モデル学習用には CC BY 4.0 の方が使いやすいが、
   提供者の意向を優先して毎回選択にする）。
2. クリップの前後 5 秒の固定値でよいか。
3. ページ名「音の地図」／「確認した音を提供する」という言い方でよいか。
4. 標高を `sites` に列として足してよいか（書き出しのたびに入力しなくて済む）。
5. 機関側の受け口（どの機関に・どうやって渡すか）。アプリは当面パッケージを作るところまで。

## 実装の進め方（了承後）

1. (a) 方法の比較ベンチ（Codex に依頼、`poc/` のスクリプト。アプリには入れない）：
   富士の全区間注釈で候補 A・B を上記の指標で比較し、結果をこの文書に追記する。
   方式はユーザーが結果を見て確定する。
2. (a) sidecar：確定した方式で作成ジョブとテーブル・API（Codex に依頼、検証スクリプト付き）。
   1 地点 1 年相当（約 21 万区間）で所要時間を確認して報告。
3. (a) web：探索ページ（Opus のサブエージェントに依頼。i18n・typography・UX チェックリスト遵守）。
4. (b) sidecar → web を同様に分担。
5. 各段階で差分を確認してからコミット。`docs/ux-checklist-2026-09.md` の観点で流れを通す。
