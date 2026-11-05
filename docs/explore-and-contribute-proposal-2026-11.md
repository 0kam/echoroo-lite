# 探索ページと提供用の書き出しの設計案（2026-11-01）

日本鳥学会 2026 のフィードバック（[requirements.md](requirements.md) の 2026-11-01 相談の段落）への対応として、
「探す → 聞いて確かめる → 提供する」を一つの流れにする 2 つの機能の設計。
**改訂版を 2026-11-01 にユーザーが了承し、実装に進む。クラスタリング方式は同日 A に確定。**
以後の変更は追記で記録し、了承前の変更を合意とは書かない。

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
- 比較ベンチの結果（下記）を受けて、**クラスタリングは A（PCA＋k-means、追加依存なし）に確定**
  （2026-11-01 ユーザー決定）。実装では**実行速度をできる限り速くするオプションを検討・採用する**
  （同日ユーザー指示。下の「計算の設計」に反映）。

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

計算の設計（sidecar、解析ジョブと同じ単一ジョブの仕組みに載せる。方式 A、
速度最優先の 2026-11-01 ユーザー指示を反映）：

1. **SQLite の流し読みは 1 回だけ**（年ベンチでは全走査だけで約 78 秒かかっており IO が支配的）。
   読んだ 1536 次元 float32 は `<data>/explore/` のキャッシュ（memmap＋解析 ID ごとの行範囲）に
   書き、解析の組が同じ限り再利用する。録音を追加した作り直しでは**新しい解析の分だけ読み足す**
   （解析が消えた場合だけ全読み直し）。
2. 共分散は raw moment（Σx・ΣxxT）の 1 パス累積 → `eigh` → 上位 50 主成分。射影は memmap から
   BLAS でまとめて（21 万×50 ≒ 42 MB）。
3. k の選択は**部分標本（最大 2 万区間）で格子を並列に**走らせ、シルエット（最大 4,000 標本）で
   選ぶ。本番の k-means は**選んだ k の 1 回だけ**、部分標本の中心を初期値にして少ない反復で
   収束させる。格子の上限は n に応じて 200 まで。
4. 散布図の点データは作成時にバイナリファイルへ書き出し、API はそのまま返す
   （リクエスト時の計算ゼロ）。クラスタの要約（件数・中心に近い代表区間・最大スコアの平均・
   AI の最頻の種）も作成時に保存する。
5. 録音の追加・再解析で古くなったら「作り直す」を出す（自動では作り直さない）。
6. 目標：1 地点 1 年（約 21 万区間）で、埋め込みキャッシュ済みなら 1 分以内、
   初回（SQLite 読み込み込み）でも数分以内。実測値をこの文書に追記する。

### 比較ベンチの結果（2026-11-01 実測。同日、ユーザーが A の採用を決定）

富士の 60 クリップ（2,160 窓）の Perch V2 埋め込み（生成 142 秒、キャッシュ再利用可）で
A・B を比較した。正解は注釈 CSV から作成（0.5 秒以上の重なり、または短い声全体が窓内。
unknown と重なる 397 窓は指標から除外、単一種 357 窓で一致を評価、
正例 5 窓以上の 40 種で探索の実効性を評価）。集計は
[cluster-bench-2026-11-01.json](benchmarks/cluster-bench-2026-11-01.json)、
再現は `poc/cluster_bench/README.md`。

| 指標 | A：PCA＋k-means | B：openTSNE＋HDBSCAN |
|---|---|---|
| まとまりの数 | 25（k はシルエットで自動選択） | 103 ＋ノイズ 11.9% |
| 種との一致（単一種の窓） | homogeneity 0.752 / NMI **0.674** | homogeneity **0.838** / NMI 0.648 |
| 〃（背景クラスを含む） | 0.675 / 0.595 | 0.743 / 0.543 |
| 最初の 1 音までに開くまとまりの数（40 種の中央値） | **1**（最悪：コゲラ 9・トラツグミ 10） | **1**（最悪 15〜53 が 6 種：エナガ 20・キセキレイ 20・サメビタキ類 46 など） |
| 21 万区間の処理時間・最大メモリ | **0.5 分・1.5 GB** | 2.6 分・3.0 GB |
| 追加依存 | なし | scikit-learn ＋ openTSNE（約 +40–50 MB） |

読み取り（事前に決めた判断基準「B が一致と実効性で明確に上回れば B」に照らして）：

- B はまとまりの純度（homogeneity）は高いが、103 個に細分化されて NMI はむしろ低く、
  **明確な上回りではない**。
- 探索の実効性は中央値では同点、**珍しい種の尻尾では A が明確に良い**（A の最悪 10 位に対し、
  B は 6 種が 15〜53 位。ノイズ扱い 11.9% に落ちた音も一覧の最後に回る）。
  「モデルが苦手な音・珍しい音を見つけて提供につなげる」という目的には尻尾が重要。
- 速度・メモリ・依存も A が軽い。

**提案：A（PCA＋k-means、追加依存なし）を採用する。** 留意点：
(1) シルエットは k=20〜50 でほぼ平ら（0.14〜0.15）で、k の厳密な最適はデータから
決まらない。探索の便宜としては致命的でないが、実装では 21 万区間で k の上限を
広げる（ベンチの格子は 60 まで。21 万区間では上限の 50 が選ばれており、実データでは
100〜200 程度まで試す）。(2) 富士は夜明けの合唱が多く複数種の窓が 56% を占める。
単一種窓での一致はその分だけ楽観・悲観どちらにも振れ得る。(3) 注釈のローマ字表記ゆれ
（hototgisu／soshicho など）は両候補に同じように影響し、結論は変えない。

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

### 改訂案 v2：全点の階層と非線形の地図（2026-11-03。同日ユーザーが了承、実装へ）

**経緯**：2026-11-02 のユーザーの試用（自分の録音約 8,700 区間、AI は 12 種を検出）で、
まとまりは 11 個、うち 4 個は無音とセミ、残りに 12 種以上が混ざり「探索には使い物に
ならない」。同日、葉を k-means で作って Ward 法で木にする v1 を提示したが、
2026-11-03 にユーザーから 3 点の指摘：(1) k-means の葉ではなく普通の階層的クラスタリングに
して、利用者が望む限り深掘りできること（葉の数も結局 k）、(2) 階層の行き来が直感的で
分かりやすい UI、(3) PCA の地図はまとまりが混ざって選びにくいので、依存の少ない
非線形の埋め込みにすること。v2 はこの 3 点に沿って試作で確かめた案。

**診断（v1 から継続）**：実データ 2 件（8,700・21,840 区間）とも、シルエットは試した
格子の最小の k=10 を選んでいた（k を増やすほど単調に下がる）。平らな k-means＋
シルエットは実データでは最も粗い分け方に落ち、種が混ざる。

#### (1) まとまり：全点の Ward 法で単音まで

- 区間数が 1 万以下：全点を直接 Ward 法（`scipy.cluster.hierarchy`、追加依存なし）で
  木にする。**k はどこにも出てこない**。8,700 区間で 0.9 秒。
- それより多いとき：距離行列が n² で載らない（21,840 区間で 1.9 GB、21 万区間では不可能）
  ので、2,000 個の小さな葉に k-means で量子化してから葉の中心を Ward 法で結合し、
  **各葉の中も全点の Ward 法で単音まで続ける**。2,000 は見える単位ではなく継ぎ目の位置で、
  試した 2 件とも全点の結果と同等以上だった。21 万区間で 1.7＋0.04＋0.4 秒。
- 段の作り方は v1 と同じ（ある分かれ目の 0.6 倍以上の高さの枝はきょうだい、1 段最大 8 個）。
  1 段 2〜8 個（中央値 3）、深さは単音まで 7〜11 段。

| データ（正解）／最良の節の F1 | 平らな k-means（現行） | v1：葉 n/40＋Ward | **v2：全点の Ward** |
|---|---|---|---|
| 富士 2,160 窓・40 種（注釈） | k=15：中央値 0.15、F1≥0.5 が 5 種 | 0.26、12 種 | **0.47、16 種**（葉 500 経由でも 0.48、17 種） |
| 実演用 8,700 区間・22 種（AI の候補を仮の正解） | k=10：0.06、3 種 | 0.25、7 種 | **0.34、7 種**（葉 2,000 経由：0.32、7 種） |
| テスト音源 21,840 区間・7 種（同上） | k=10：0.19、2 種 | 0.45、2 種 | 0.45、3 種（**葉 2,000 経由：0.51、4 種**、6.8 秒→1.6 秒） |

#### (3) 地図：numpy だけで実装する PaCMAP

候補の整理：UMAP・PaCMAP 本家は numba 依存（PyInstaller の単一ファイルで不可）。
openTSNE は scikit-learn を連れてくる（約 +40〜50 MB、21 万区間で 2.6 分・3 GB）。
PaCMAP（Wang ほか 2021, JMLR）は「近傍・中距離・遠方の対」に対する全対の勾配と Adam
だけで書けるので、numpy のベクトル化に向く。試作は約 120 行、**追加依存なし**。

| 2 次元での 10 近傍の同ラベル率 | 50 次元（上限の目安） | PCA 2 次元（現行） | **PaCMAP（numpy）** | openTSNE（参考） |
|---|---|---|---|---|
| 富士（単一種の 357 窓・20 種） | 0.798 | 0.584 | **0.807**（1.0 秒） | 0.791（3.6 秒） |
| 実演用 8,700（AI の候補 2,145 区間・22 種） | 0.753 | 0.328 | **0.675**（4.0 秒） | 0.725（13.4 秒） |
| テスト音源 21,840（同 2,123 区間・7 種） | 0.932 | 0.381 | **0.845**（11.2 秒） | 0.898（24.0 秒） |

- 21 万区間では**ランドマーク**方式：1 万点を PaCMAP で配置し、残りは 50 次元で近い 5 点の
  加重平均の位置に置く。21,840 区間で 5,000 点のランドマークにしても 0.841（全点 0.845）。
  21 万区間で 1 万点：4.8＋12.5 秒（2 万点なら 11＋24 秒）。
- 階層は 50 次元で計算し、地図は見せ方だけに使う（2 次元で分けない）。

#### (2) 画面：地図そのものを行き来の手段にする

モック（実データ入り、音は鳴らない）：[prototypes/explore-hierarchy-mock.html](prototypes/explore-hierarchy-mock.html)。
富士（注釈あり）と実演用（AI の候補）を切り替えられる。

- **1 枚の地図を拡大・縮小して階層を行き来する**（地図アプリと同じ感覚）。いま見ている
  まとまりの区間に色、中のまとまりごとに番号の札、それ以外は薄く描く。
- 点を押す＝そのまとまりを選ぶ（右の一覧も同期して他は薄く）。二度押し、または一覧の
  「中を見る」＝その中へ（地図がそのまとまりへズーム）。「↑ 一つ上へ」「全体に戻る」と
  パンくず「全体 › まとまり 2 › まとまり 2-3」。ブラウザの戻るも 1 段上へ。
- 右：中のまとまりの一覧（多い順。件数と割合・AI の候補と候補のある割合・代表音・中の個数）。
  その下に「このまとまりの音（中心に近い順）」20 件ずつ、各行に「この音に似た声を探す」。
- 単音まで続くので「これ以上は分かれません」は単音のときだけ出る。
- 操作確認で分かったこと：ドリル直後の連打で 2 段降りてしまうため、ドリル後 0.4 秒は
  クリックを無視する（モックに反映済み）。

#### テーブルと API（v2）

点を木の深さ優先の順に並べると、どの節の中身も連続した範囲になる。節は範囲 `[lo, hi)` だけを持ち、
中身の点を節ごとに保存しない。

```
explore_runs(id, site_id, engine, model_sha, params, created_at)        -- 現行のまま
explore_points(run_id, segment_id, position, x, y)                       -- position＝木の順、x,y＝PaCMAP
explore_nodes(run_id, node, parent, depth, lo, hi, rep_segment_id,
              hint_rate, top_species, top_share)                         -- 平らにした木の節（単音の節は持たない）
<data>/explore/pca50-<run>.f32                                           -- 木の順の 50 次元（中心に近い順の並べ替え用）
```

- `GET /explore?dataset=&node=`（状態、パンくず、その節の要約と中のまとまりの一覧）
- `GET /explore/points?dataset=`（全点を 1 回だけ。x, y, segment_id を木の順で。
  画面はこの配列を節の範囲で切って描く）
- `GET /explore/nodes/{n}/segments?dataset=&offset=`（その節の音、中心に近い順）
- `POST /explore` は現行どおり。スキーマは v4（v3 の explore_* は作り直し。地図は作り直せばよい）。
- 作成の所要時間の見込み（21 万区間）：PCA 3 秒＋木 2 秒＋地図 17 秒＋要約 1 秒 ≈ 25 秒。

#### 決定（2026-11-03、ユーザーが v2 を了承）

v2 で実装する。確認事項 2・3 は提案どおり（1 万区間を超える地図はランドマーク 1 万点、
まとまりは「まとまり 2-3」のような番号で呼ぶ）。実装上の判断：**20 件以下のまとまりは
その場で全部聞けるので、そこで木を止める**（単音の節は作らない。一覧 1 ページ＝20 件）。
分担は sidecar を Codex、画面を Opus のサブエージェント。実装役はコミットしない。

#### 実装仕様 v2（sidecar と画面の共通の契約）

定数（`apps/sidecar/explore.py`）：`EXACT_MAX=10_000`（全点 Ward の上限）、`LEAF_COUNT=2_000`、
`FIT_MAX=20_000`（葉の k-means の学習標本）、`TERMINAL_MAX=20`、`TAU=0.6`、`MAX_CHILDREN=8`、
`LANDMARK_MAX=10_000`（PaCMAP を全点で配置する上限。超えたら 1 万点を配置し残りは
50 次元で近い 5 点の逆距離加重平均）、PaCMAP は近傍 10・中距離 5・遠方 20、450 反復、Adam lr 1.0、
初期値 PCA 2 次元×0.01、乱数は `SEED`。`SCORE_THRESHOLD=0.1` は現行どおり。

木の作り方：

1. PCA 50 次元（現行）。`n <= EXACT_MAX` なら `scipy.cluster.hierarchy.linkage(X, 'ward')`。
2. それ以上：`lloyd` で 2,000 葉（学習は 2 万件の部分標本、全件は `_nearest` で割り当て、空の葉は除く）。
   葉の中心で Ward、各葉（2 件以上）の中でも Ward。葉の中の木の高さは、親の結合の高さの
   0.9 倍を超えないように葉ごとに一律に縮めてつなぐ（継ぎ目で高さの単調性を保つ）。
   木は配列（left, right, height, size）で持ち、再帰は使わない（21 万点で深さが Python の
   再帰上限を超えうる）。
3. 平らにする：節の高さ H に対し、高さが `TAU*H` 以上の枝を高い順に展開して同じ段に並べる
   （最大 `MAX_CHILDREN`）。件数が `TERMINAL_MAX` 以下の節は子を作らない。
4. 並び：深さ優先で点に `position` を振る。子は件数の多い順、末端の節の中は節の平均
   （50 次元）に近い順。これでどの節の中身も `[lo, hi)` の連続範囲になる。
5. 名前：根は空文字、子は親の名前＋「-」＋多い順の番号（根の子は「1」「2」…）。
6. 要約（節ごと）：`size`、`rep_segment_id`（平均に最も近い区間）、`hint_rate`
   （最大スコア ≥ 0.1 の割合）、`top_species`（候補ありの区間での最頻の argmax、候補が 1 件もなければ null）、
   `top_share`（候補ありの中での割合）。

スキーマ v4（v2・v3 から移行。v3 の `explore_*` は捨てて作り直し、`points-*.bin` も削除。
地図は作り直せばよい）：

```
explore_runs(id, site_id, engine, model_sha, params, created_at)                 -- 現行のまま
explore_points(run_id, position, segment_id, x, y)            PRIMARY KEY(run_id, position)
explore_nodes(run_id, node, parent, name, depth, lo, hi, rep_segment_id,
              hint_rate, top_species, top_share)              PRIMARY KEY(run_id, node)
```

ファイル：`<data>/explore/points-<run>.bin`（`<I n`、f32 x[n]、f32 y[n]、i64 segment_id[n]、
position 順）、`<data>/explore/pca50-<run>.f32`（n×50 の f32、position 順。中心に近い順の
並べ替えに使う）。埋め込みキャッシュは現行のまま。

ジョブの段階：`explore_queued` → `explore_cache` → `explore_pca` → `explore_tree` →
`explore_layout` → `explore_summarizing` → `finished`（total 5）。取消は段階の間と
PaCMAP の 10 反復ごとに確認。

API：

- `POST /explore {dataset_id}`：現行どおり（実行中は 409 `job_busy`）。
- `GET /explore?dataset=&node=`（`node` 省略時は根）：
  `{status, created_at, engine, segment_count, node, path, children}`。
  作成済みの地図があれば `status` が `stale`・`building` でも `node`・`path`・`children` を返す
  （作り直し待ちの地図もそのまま見られる。実装時に変更）。地図がなければ 3 つとも返さない。
  節の形は `{node, name, parent, depth, size, lo, hi, child_count, rep: {segment_id, recording_id, path,
  start_s, end_s, recorded_at, timezone}, hint_rate, top_species: {id, scientific_name}|null,
  top_share}`（`child_count` は実装時に追加。画面が「中に K 個」を出すため）。`path` は根からその節まで（`{node, name, size}` の列）、`children` は多い順。
  知らない `node` は 404 `not_found`。
- `GET /explore/points?dataset=`：上のバイナリ。
- `GET /explore/nodes/{node}/segments?dataset=&offset=`：`{total, segments: [{segment_id,
  recording_id, path, start_s, end_s, recorded_at, timezone, dist}]}`。20 件ずつ、節の平均に
  近い順（末端は position 順がそのままその順。それ以外は `pca50` から要求時に計算）。

画面（`/datasets/[id]/explore`）：モック [prototypes/explore-hierarchy-mock.html](prototypes/explore-hierarchy-mock.html) の
操作をそのまま。全点を 1 回だけ取り、節の `[lo, hi)` で切って描く。いま見ている節の子ごとに
色と番号の札、他の点は薄く。点を押す＝子を選ぶ（一覧と同期）、二度押し／「中を見る」＝その子へ
（350 ms でズーム、直後 0.4 秒はクリックを無視）。「↑ 一つ上へ」「全体に戻る」とパンくず。
場所は URL のクエリ `node=` に持ち、戻ると 1 段上へ。一覧の下に「このまとまりの音（中心に近い順）」
20 件ずつと「この音に似た声を探す」（現行の引き継ぎ）。AI の候補は `hint_rate >= 0.2` のとき
「AI の候補: 種名（top_share%）」、未満なら「AI の候補なし（無音や虫の声の可能性）」。
どちらも「候補あり hint_rate%」を添える。文言は日英の locale キー、文字は 7 役割。

### 改訂 v2.1：まとまりから音を直接引き出す（2026-11-03。同日ユーザーが了承、実装へ）

**経緯**：v2 を Tauri で試したユーザーの指摘（2026-11-03）。(1) AI の候補は 1 種だけでなく主要な
ものを複数出す、(2) 音ごとにも AI の候補を出す、(3) 地図の点を押したらその音を表示する、
(4) 「中のまとまり」と「このまとまりの音」が上下に分かれていて分かりにくい。まとまりから
プルダウンのように音を直接引き出せる形に設計し直す。

**画面の再設計**（モック：[prototypes/explore-hierarchy-mock.html](prototypes/explore-hierarchy-mock.html)。
実演用の実データに、日本の鳥に絞った上位 3 の AI 候補を付けてある。モックの制約：音は鳴らず、
行を開いたときの音の並びは中心に近い順ではない（本実装では近い順））

- 右の一覧は**中のまとまりの行だけ**にし、行を押すとその場で開いて中の音（中心に近い順、
  20 件ずつ）が出る。「このまとまりの音」という別の節はなくす。開いた行の先頭に
  「中を見る（中に K 個のまとまり）」。末端（20 件以下）の行は開けば全部聞けるので
  「中を見る」はない。代表音の再生は行から外し、開いた一覧の 1 件目（中心に最も近い音）が
  代表を兼ねる。
- 行の見出しの AI の候補は**上位 3 種と割合**（候補のある音の中での最頻 3 つ）。候補のある音が
  2 割未満なら「AI の候補なし（無音や虫の声の可能性）」。
- **音ごとの AI の候補**：各音に上位 3 種とスコア（%）。録音ページと同じ考え方で、Perch は
  全クラスで softmax してから日本の鳥の候補リスト（解析メタデータの `candidate_filter`）に
  絞る。Kinglet は保存済みのスコアに同じ絞り込み。5% 未満は出さない。まとまりの候補も
  この絞った上位 1 位から数える（カエルや「Water」などの非対象クラスは出なくなる）。
- **地図の点を押すと、地図の下に「選んだ音」のカード**：時刻、スペクトログラムと再生、
  AI の候補（上位 3）、「このまとまりを開く」（右の行を開いてそこへ）、「この音に似た声を探す」。
  押した点には輪を描き、その音が属する行を強調する。押したら**すぐ再生する**
  （押す操作そのものが再生の合図。カードで止められる）。二度押しは今までどおりその中へ。
- パンくず・「↑ 一つ上へ」・「全体に戻る」・Esc・`?node=` は v2 のまま。

**sidecar の変更**：

- 節：`top_species` を上位 3 の配列 `[{id, scientific_name, share}, …]` にする（`top_share` は廃止）。
  `hint_rate` は絞り込み後の最大スコア ≥ 0.1 の割合。`rep_segment_id` は残す（API 互換）。
- 音：`explore_points` に `candidates TEXT`（JSON `[[scientific_name, score], …]` 上位 3、5% 以上）を
  追加し、作成時に埋める（スコアは作成時に全解析分を読むので追加の読み込みはない）。
  `GET /explore/nodes/{n}/segments` の各行と、新しい `GET /explore/points/{position}?dataset=`
  （地図の点から 1 音の情報を引く）で `candidates` を `[{id, scientific_name, score}, …]` として返す。
- スキーマは v5（v4 の explore_* は作り直し）。

#### 決定（2026-11-03、ユーザーが v2.1 を了承）

v2.1 で実装する。点を押したらすぐ再生、一覧は 20 件ずつ、末端は 20 件以下のまま。
「音ごと」は 5 秒の区間ごと。分担は v2 と同じ（sidecar を Codex、画面を Opus、コミットは確認後）。

#### 実装仕様 v2.1（追加・変更分）

- 候補の計算（作成時）：解析メタデータに `candidate_filter.species` があれば（Perch）その学名に
  絞る。Perch は全クラスで softmax してから絞る（録音ページの `predictions.perch_candidates` と同じ）。
  Kinglet は保存済みの `scores` をそのまま使う（フィルタなし）。各区間の候補は絞った中の
  上位 3 でスコア 0.05 以上。`hint_rate` と節の候補は、絞った中の最大スコア ≥ 0.1 を「候補あり」
  として数える。
- テーブル（スキーマ v5。v2〜v4 からの移行は explore_* を捨てて作り直し）：
  `explore_points(run_id, position, segment_id, x, y, candidates)`（`candidates` は JSON
  `[[scientific_name, score], …]`、なければ `[]`）、
  `explore_nodes(run_id, node, parent, name, depth, lo, hi, rep_segment_id, hint_rate, top_species)`
  （`top_species` は JSON `[{id, scientific_name, share}, …]` 上位 3。`top_share` 列は廃止）。
- API：節の形は `top_species` が配列になり `top_share` が消える。音の形
  `{segment_id, recording_id, path, start_s, end_s, recorded_at, timezone, candidates: [{id, scientific_name, score}, …]}`
  を `GET /explore/nodes/{n}/segments` の各行（`dist` 付き）と、新しい
  `GET /explore/points/{position}?dataset=`（地図の点 1 つ。範囲外は 404 `not_found`）で返す。
  `id` は `species` 表に学名があればその id、なければ null。
- 画面：右は中のまとまりの行だけ。行を押すと開き、先頭に「中を見る（中に K 個のまとまり）」
  （末端にはない）、続けてその行の音を中心に近い順に 20 件ずつ（`/explore/nodes/{子}/segments`）。
  各音は時刻・再生・AI の候補（上位 3、%）・「この音に似た声を探す」。地図の点を押すと
  `/explore/points/{position}` を引いて地図の下に「選んだ音」のカードを出し、すぐ再生する。
  カードには「このまとまりを開く」（その行を開いて移動）と「この音に似た声を探す」。
  点には輪、属する行を強調。二度押しはその中へ。いまの節が末端なら音の一覧をそのまま出す。
  まとまりの候補は「AI の候補: A 24%・B 12%・C 8%（候補のある音 52%）」、2 割未満は
  「AI の候補なし（無音や虫の声の可能性）・候補のある音 7%」。

### 改訂 v2.2：試用後の調整（2026-11-05。ユーザーの指示で実装、7e50baf）

**経緯**：v2.1 を Tauri で試したユーザーの指摘（2026-11-05）。(1) 地図の点を押すとスペクトログラムが
途中までしか見えず、スクロールして見えるようにしても、押すたびに画面が上へ戻る。ページの移動以外で
クリックのたびにスクロールが動くのは使いにくいので、全体で該当箇所を洗い出す。(2)「表示を元に戻す」は
不要。「一つ上へ」「全体に戻る」は地図の上に移し、「戻る」「進む」「全体に戻る」にしてもよい。
(3)「この音に似た声を探す」は不要（押しても似た音が出るわけではなく、種名を入れる検索になる。地図が
そもそも似た音を探すためのもの）。(4) 音の欄を小さくして一度にたくさん見たい（スペクトログラムの
大きさはそのまま）。(5) 一覧を、地図で選んだ点に近い順に並べ替えたい。

**変えたこと**（明示の指示に沿った調整なので、モックを挟まず実装した。記録は
[implementation-status.md](implementation-status.md) の 2026-11-05 の節）

- 地図の点を押してもページは動かない（行の強調だけ）。カードは次の音が届くまで前の音を出し、高さを
  固定。地図・検証・在不在の貼り付く欄は画面の高さを上限にした。
- 地図の左上に「‹ 戻る」「進む ›」「全体に戻る」。戻る・進むは見たまとまりの順。「表示を元に戻す」
  「↑ 一つ上へ」「この音に似た声を探す」と検索ページの受け取りを削除（パンくずと Esc は残す）。
  まとまりの移動は履歴を置き換え、ブラウザの戻るは地図を離れる。
- 音の欄：再生ボタンを左、日時と AI の候補をスペクトログラムの上の 1 行に（時間軸なし）。
- 「並べ方」：まとまりの中心に近い順（既定）／選んだ音に近い順（PCA 50 次元の距離。
  `GET /explore/nodes/{n}/segments?near=<位置>`）。
- 選んだ音は、その音を含むまとまりへ入ったときとページに戻ったときにも残す（再生はしない）。

**追加の調整**（同日、ユーザーの指示）：並べ方の既定を「選んだ音に近い順」にした（音を選ぶまでは中心に
近い順）。開いた行を見ているときに別の行の点を押すと、薄くなるだけで、その音を見るには行を畳んで別の行を
開く必要があり分かりにくかった。点を押すと、その音のまとまりの行が開く（行は同時に 1 つだけ。`<details>` の
`name` で、ブラウザが前の行を閉じる）。新しく開いた行が画面の外なら、二度押しの間（0.36 秒）を待ってから
その行の先頭まで送る。地図の欄は画面に固定されているので動かない。同じ行の点なら送らずに並べ直すだけ。
選んだ音を持ったまま中に入ったときも、その音を含む小さいまとまりの行を開いて送る。カードの
「このまとまりを開く」は不要になったので外した。

### 改訂案 v4（予備検討）：まとまりを地図に合わせる（2026-11-05。提案、未合意）

**経緯**：v3 を試したユーザーの指摘（2026-11-05）。まとまりと地図上の表示がもう少し一致していると使いやすい。
ほかの方法を予備的に検討する。

**原因**（[図](benchmarks/agreement-3-2026-11-05.png)、実演用 8,700 区間）：いまの方式（M0）は、まとまりを PCA 50 次元の
Ward 法で作り、地図は別に PaCMAP で描く。このため、地図上の 1 つの島（点の固まり）が 2 色に割れたり（左の島の上半分が
まとまり 1、下半分がまとまり 3）、同じまとまりが離れた島にまたがって番号の札が点のない所に立ったりする。地図上で
近い 10 点が同じまとまりである割合は 0.98 と高く、局所的には混ざっていない。ずれは島と札の単位で起きている。

**比べた方法**（試作 `poc/cluster_bench/agreement_proto.py`、記録 [agreement-proto-2026-11-05.txt](benchmarks/agreement-proto-2026-11-05.txt)、
図は [富士](benchmarks/agreement-fuji-2026-11-05.png)・[実演用](benchmarks/agreement-3-2026-11-05.png)）

- M0 いまの方式。M1 地図の 2 次元座標で Ward 法。M2 PaCMAP 10 次元で Ward 法。M3 M0 の木で、まとまりに入るたびに
  その中だけで地図を作り直す。M4 M0 の木で、同じまとまりの点を引き寄せて地図を描く。M5 近傍グラフのスペクトル埋め込みで
  Ward 法。M6 50 次元の Ward 法で、地図上で隣り合うまとまりだけを合併（scikit-learn、試作だけ）。M7 50 次元に地図の座標を
  足して Ward 法（地図の重み 0.5〜8 を試し、2 で頭打ち）。
- 地図との一致は、見ている段の点で数えた。**島の純度**（島が 1 色か）、**札の当たり率**（点がいちばん近い番号の札が自分の
  まとまりか）、偶然の分を差し引いた近傍の一致（κ）。種への当てはまりは以前と同じ、種ごとに最も合う節の F1
  （富士は注釈 40 種、実演用は AI の候補 1 位が 0.1 以上の 7 種なので揺れが大きい）。

| 方法 | 島の純度 最上段（富士／実演用） | 札の当たり率 最上段 | 札の当たり率 1 段下 | F1 中央値（富士／実演用） | 富士で F1≥0.5 |
|---|---|---|---|---|---|
| M0 いまの方式 | 0.58／0.88 | 0.78／0.83 | 0.74／0.85 | 0.469／0.370 | 16/40 |
| M1 地図の座標で Ward | 1.00／1.00 | 0.99／0.95 | 0.95／0.99 | 0.459／0.308 | 17/40 |
| M7 50 次元＋地図（重み 2） | 1.00／1.00 | 0.99／0.95 | 0.96／0.97 | 0.460／0.276 | 17/40 |
| M3 段ごとに地図を作り直す | 0.58／0.88 | 0.78／0.83 | 0.73／0.89 | 0.469／0.370 | 16/40 |
| M4 同じまとまりを引き寄せる | 0.99／0.90 | 0.77／0.90 | 0.98／0.99 | 0.469／0.370 | 16/40 |

M2・M5・M6 は一致の改善が小さいか、F1 が下がった（記録を参照）。

**読み取り**：

- M1 と M7 は、島が必ず 1 色になり、札の位置と点がほぼ一致する（どの段でも、分け方が地図上の位置に沿う）。富士の
  F1 は M0 とほぼ同じ（中央値 0.469 → 0.459、F1≥0.5 の種は 16 → 17）。実演用の F1 の差は、AI の候補 7 種の揺れの範囲で
  判断できない。地図の 10 近傍の同ラベル率は 50 次元とほぼ同じだった（2026-11-03、富士 0.81 対 0.80）ので、地図の配置で
  分けても種の分かれ方はほとんど失われない、という結果と合う。
- M4 は数値はよいが、図のとおり点が丸い固まりに押し込まれ、まとまりの中の形が地図から消える。M3 は段ごとに地図が
  変わるのに改善が小さい。どちらも勧めない。

**提案**：M1（地図の 2 次元座標で Ward 法）に切り替える。M7 と結果はほぼ同じで、重みという調整の値を持たない分だけ
単純。作成の手順は「地図 → 木」の順に入れ替えるだけで、計算時間は変わらない（Ward 法は 2 次元のほうが軽い）。
まとまりの中の並べ方（中心に近い順・選んだ音に近い順）は、似た音の順として今までどおり 50 次元で測る。データの形は
変わらず、地図を作り直すと新しい方式になる。

**未確認**：2 段目より深い段の一致、21,840 区間のテスト音源（確認用データに地図がない）、使ったときの分かりやすさ。
ユーザーが合意すれば、地図の作成を M1 に変えて Tauri で試してもらう。

## (b) 提供用の書き出し

### 改訂案 v3：地図で選んだ音に情報を付けて書き出す（2026-11-05。同日ユーザーが了承、実装へ）

**経緯**：v2.1 を試したユーザーの構想（2026-11-05）。最終的には、まとまりからいくつかの音を選び、
種名など Xeno-canto（XC）に紐付ける情報を入れて提供してもらう。その一歩手前として、まとまりごとに
出るスペクトログラムにチェックを入れ、必要な項目（種名、ほかの種の有無、さえずりか地鳴きか、
録音の質など）を入れて、ダウンロードできるようにしたい。イメージは Kaleidoscope Pro（まとまりに
ラベルを付け、表で書き出す）。種名はまとまりの中でおおむね同じはずなので、毎回入力しなくて
済むようにしたい。

**以前の (b) との関係**：以前の (b)（下の「提供の経路」以降）は、在不在の確認で「この種の声だ」を
付けた音から書き出す設計だった。v3 は書き出す音の出どころを**地図で聞いて選んだ音**にし、
録音者名・ライセンス・座標のぼかしの入力は、実際に提供する段階（次の一歩）へ回す。CSV の列
（XC の一括登録の表と同じ 29 列）とパッケージの形は以前の (b) を流用する。「範囲外」に書いた
「探索ページからの直接の種の確定・印付け」は、この案が合意されればやめる。

**画面**（モック：[prototypes/explore-label-mock.html](prototypes/explore-label-mock.html)。
実演用の実データのまとまり 1-1-4 の中、行 1-1-4-1（AI の候補はヒガラが 88%）の 8 音の
スペクトログラムを埋め込んだ。モックの制約：音は鳴らず、地図は画像、保存と書き出しは画面の動きだけ。
作り方は `poc/cluster_bench/label_mock.py`）

① 音の地図（いまのページに足す）

- 開いた行の先頭に**「このまとまりの種」**。AI の候補の上位 3 をそのまま押せるボタンにし、
  「ほかの種を選ぶ…」で種名の検索（既存の `SpeciesPicker`）。決めた種が、この行で選んだ音の種に入る。
- 各音の左（再生ボタンの上）に**チェック「選ぶ」**。チェックすると、その音の下に入力が出る：
  - 種：まとまりの種が入る（「まとまりの種」と表示）。この音だけ変えることもでき、
    「まとまりの種に戻す」で戻せる。
  - 声：さえずり／地鳴き／わからない（XC の sound type の song／call／空欄）。
  - 質：A〜E（XC の基準。「質の目安」で短い説明。未選択のままでもよい）。
  - ほかの種の声：なし／あり。ありのときは種を足せる（分からなければ足さなくてよい）。
- まとまりの種を後から変えると、その行で選んだ音のうち、個別に変えていない音の種も変わる。
- 地図の点を押したときの「選んだ音」のカードにも同じ「選ぶ」を付ける。
- 画面の下に帯：「選んだ音 N 件・種が未入力 a 件・質が未入力 b 件［一覧と書き出しへ］」。

② 選んだ音の一覧と書き出し（新しいページ `/datasets/[id]/explore/picked`）

- 選んだ音を 1 行ずつ（小さいスペクトログラムと再生、日時、まとまり、ファイルと秒、①と同じ入力、
  「外す」）。ここでも直せる。未入力は目立たせる。
- 「書き出す（ZIP）」で保存先を選ぶ。未入力があっても書き出せる（その列は空欄）。押す前に件数と
  未入力の数を示す。書き出した音には「書き出し済み」の印と日時を付ける（二重の提供を避ける）。
- 録音者名・ライセンス・座標のぼかしはこの書き出しに入れない、座標はそのまま入る、と画面と README に書く。

**UX チェックリストとの関係**：「一画面に違う目的のフォーム・一覧を混在させない」に対しては、
聞いて選ぶことと種などを入れることを同じ作業とみなし（ユーザーの構想どおり、スペクトログラムの
そばで入れる）、入力はチェックした音にだけ出す。見直しと書き出しは②の別ページに分ける。
入力した内容は sidecar に保存し、戻る・アプリの再起動でも消えない。

**書き出す中身**

```
echoroo-picked-<地点>-<日付>.zip
  audio/<filename>.wav   … 聞いて選んだ 5 秒そのまま（元のサンプリング周波数。確認したいこと 3 を参照）
  metadata.csv           … XC テンプレート互換の 29 列（UTF-8 BOM）
  README.txt             … 日本語と英語。座標をぼかしていないこと、空欄の列は提供の段階で埋めること
  manifest.json          … 各ファイルの sha256
```

- CSV で埋める列：filename、latitude / longitude（地点の座標）、country（Japan）、location name（地点名）、
  recording date / time of day（地点のタイムゾーン）、genus / species（選んだ音の種。未入力は空欄）、
  sound type(s)（song／call／空欄）、quality（A〜E／空欄）、background species（ほかの種の学名を `;` で）、
  type（single species）、automatic recording（yes）、animal seen?（no）、recording method（field recording）、
  playback used?（no）、remarks（「Echoroo Lite の音の地図で聞いて選んだ 5 秒」と、種不明のほかの声が
  あればその旨）。
- 空欄で残す列：recordist name、license、elevation（提供の段階で入れる）と、sex・life stage など推測で
  埋めないもの。

**保存（sidecar）**

- 新しい表 `picked_sounds(recording_id, start_s, end_s, species_id, sound_type, quality, others,
  background, exported_at, updated_at)`。主キーは録音と区間にし、地図の作り直しや再解析で区間の id が
  変わっても残す（在不在の印と同じ考え方）。地点の一覧は録音から引く。
- API：`GET /explore/picked?dataset=`（一覧）、`PUT /explore/picked`（1 音の登録・更新）、
  `DELETE /explore/picked`（外す）、`POST /explore/picked/species`（まとまりの種をその範囲の選んだ音へ
  反映。種が空か、前のまとまりの種のままの音だけ）、`POST /explore/picked/export {dataset_id, path}`
  （既存の書き出しと同じ staging→rename）。`/explore/nodes/{n}/segments` と `/explore/points/{p}` の
  各音に `picked`（登録内容か null）を付ける。
- 「このまとまりの種」は画面の下書き（localStorage）に持つ。入力の手間を省くためのもので記録ではない
  （消えても、選んだ音の種は残る）。

**確認したいこと**

1. 声の種類は「さえずり／地鳴き／わからない」の 3 つでよいか（XC には alarm call・flight call なども
   あるが、初めての人には分けにくい）。
2. 録音の質は未選択のまま書き出せる形でよいか（既定値を入れると、聞いて決めていない値が残るため）。
3. 書き出す音の長さ。初めは前後に 5 秒ずつ足した 15 秒を提案した。理由は (1) 5 秒の区切りは鳴き声に
   合わせた位置ではなく、声が区切りをまたいで切れることがある、(2) さえずりは数秒おきに繰り返すので、
   前後があると種を判断しやすい、の 2 つ（以前の (b) の「確認した区間だけでは文脈が無さすぎる」と同じ）。
   2026-11-05 にユーザーが理由を質問。見直すと、v3 では種・ほかの種の声・質を聞いた 5 秒で決めるので、
   足した 10 秒は聞いていない部分になり、入力と書き出した音が食い違うおそれがある（足した部分で別の種が
   鳴いている、質が違うなど）。続けて選んだ区間どうしは音が重なる。このため**聞いて選んだ 5 秒をそのまま
   書き出す**案に改める（未合意）。前後が要るときは、録音と区間を保存してあるので、提供の段階で元の録音から
   切り出し直せる。
4. 録音者名・ライセンス・座標のぼかしは今回の書き出しに入れず、提供の段階で入れる、でよいか。
5. 以前の (b)（「この種の声だ」を付けた音から書き出す経路）は、この形に置き換えてよいか。


#### 決定（2026-11-05、ユーザーが v3 を了承）

v3 で実装する。確認したいことは提案どおり：声の種類は 3 つ（さえずり／地鳴き／わからない）、録音の質は
未選択のまま書き出せる、書き出す音は聞いて選んだ 5 秒そのまま、録音者名・ライセンス・座標のぼかしは
提供の段階へ、以前の (b) の出どころ（「この種の声だ」を付けた音）はこの形に置き換える。分担は v2 と同じ
（sidecar を Codex、画面を Opus のサブエージェント、コミットは差分の確認後）。

#### 実装仕様 v3

XC の一括登録テンプレート（v2、2026-11-05 にシートを取得して確認）の 29 列は次の順：
`filename, latitude, longitude, country, location name, elevation, recording date, time of day, license,
recordist name, recording device, microphone, type, automatic recording, genus, species, subspecies, sex(es),
life stage(s), animal seen?, sound type(s), recording method, collection date, playback used?, temperature,
background species, collection specimen reference, remarks, quality`。鳥の選択肢では recording method は
`field recording`、sound type(s) は `song`／`call` など（複数はカンマ区切り）、background species は学名を
カンマ区切り。以前の (b) の表のうち、この 3 点は上の確認に合わせる。

**sidecar**

- スキーマ v6（`MIGRATABLE_VERSIONS` に 5 を足す。v5 からは表を足すだけで、explore_* は消さない。
  v2〜v4 からの移行は今までどおり explore_* を作り直す）。
  ```
  picked_sounds(recording_id INTEGER NOT NULL REFERENCES recordings ON DELETE CASCADE,
    start_s REAL NOT NULL, end_s REAL NOT NULL,
    species TEXT,                                  -- 学名。未入力は NULL
    sound_type TEXT NOT NULL DEFAULT '' CHECK(sound_type IN ('','song','call')),
    quality TEXT NOT NULL DEFAULT '' CHECK(quality IN ('','A','B','C','D','E')),
    others INTEGER NOT NULL DEFAULT 0,              -- ほかの種の声あり
    background TEXT NOT NULL DEFAULT '[]',          -- 学名の JSON 配列
    exported_at TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(recording_id, start_s, end_s))
  ```
  地点の一覧は録音から引く（`site_recordings` の `scope_id` が dataset）。
- 音の形（以下 item）：`{recording_id, start_s, end_s, path, recorded_at, timezone, species: {id, scientific_name} | null,
  background: [{id, scientific_name}], sound_type, quality, others: bool, exported_at, updated_at}`。
  `id` は `species` 表に学名があればその id、なければ null（`explore._species_ids` と同じ）。
- `GET /explore/picked?dataset=` → `{items: [item…]}`（録音時刻の順）。
- `PUT /explore/picked` body `{dataset_id, recording_id, start_s, end_s, species: str|null, background: [str] (≤20),
  sound_type, quality, others}` → item（全項目を置き換える登録・更新。録音がその地点のものでなければ 404 `not_found`、
  `end_s <= start_s` などは 422）。
- `DELETE /explore/picked?dataset=&recording_id=&start_s=&end_s=` → `{deleted: bool}`。
- `POST /explore/picked/species` body `{dataset_id, node, species: str|null, previous: str|null}` → `{updated: n}`。
  いまの地図でその節の範囲 `[lo, hi)` に入る音のうち、選んだ音で `species` が NULL か `previous` と同じものを
  `species` にする（まとまりの種の反映。個別に変えた音は残る）。節がなければ 404。
- `POST /explore/picked/export` body `{dataset_id, folder}` → `{path, count}`。その地点の選んだ音すべてを、
  `folder` に `echoroo-picked-<dataset_id>-<地点の現地時刻 YYYYMMDD-HHMMSS>.zip` として書く（フォルダの中の
  一時ディレクトリで作ってから rename。途中で失敗したら何も残さない）。成功後に書き出した音の `exported_at` を
  入れる。選んだ音がなければ 400 `nothing_picked`、フォルダがなければ `folder_missing`、書けなければ
  `report_write_failed`（既存のキー）。
- ZIP の中身：
  - `audio/<filename>`：`[start_s, end_s]` をそのまま、元のサンプリング周波数の WAV（元が 24 bit なら PCM_24、
    それ以外は PCM_16）。`filename` は `<現地の YYYYMMDD_HHMMSS>_<Genus_species または unknown>.wav`、重なれば `_2` などを足す。
  - `metadata.csv`：上の 29 列、UTF-8 BOM 付き。filename、latitude / longitude（地点、小数 4 桁）、country `Japan`、
    location name（地点名）、recording date `YYYY-MM-DD` と time of day `hh:mm`（区間の開始、地点のタイムゾーン）、
    type `single species`、automatic recording `yes`、genus / species（学名の 1・2 語目。3 語目があれば subspecies）、
    animal seen? `no`、sound type(s)（`song`／`call`／空欄）、recording method `field recording`、playback used? `no`、
    background species（学名をカンマ区切り）、remarks（英語：`Selected by listening on the Echoroo Lite sound map
    (5 s interval at <start>–<end> s of the source recording).` に、ほかの種の声ありで種名がなければ
    ` Other species audible (not identified).`）、quality（A〜E／空欄）。elevation・license・recordist name ほかは空欄。
  - `README.txt`：日本語と英語。中身の構成、空欄の列（録音者名・ライセンス・標高）は提供のときに埋めること、
    座標はぼかしていないこと（珍しい種は渡す前に注意）、機関に渡すか XC のフォームへの転記元に使えること。
  - `manifest.json`：`{schema: 'echoroo_picked_sounds_v1', created_at, site: {name, timezone, latitude, longitude},
    items: [{filename, source_file (録音ファイルのベース名), start_s, end_s, recorded_at, timezone, species, background,
    sound_type, quality, others}], files: {相対パス: {sha256, bytes}}}`。

**web**

- 地図のページ：開いた行の音と末端の音の一覧の各音に、再生ボタンの左にチェック「選ぶ」。選んだ音は下に入力
  （共通部品 `PickForm`：種〔表示名と「まとまりの種」の印、「変える」で `SpeciesPicker`、まとまりの種がある
  ときは「まとまりの種に戻す」〕、声〔さえずり／地鳴き／わからない〕、質〔A〜E と「質の目安」〕、ほかの種の声
  〔なし／あり、ありなら種を足す・消す〕）。変えるたびに `PUT`。チェックを外すと `DELETE`。
- 開いた行（末端なら音の一覧）の先頭に「このまとまりの種」：その行の AI の候補の上位 3 をボタンに、
  「ほかの種を選ぶ…」で `SpeciesPicker`。決めたら `POST /explore/picked/species` で反映し、以後その行で
  チェックした音の種に入れる。まとまりの種は localStorage（地図の作成日時ごと）に持つ。
- 地図の点を押したときのカードにも「選ぶ」（入力は一覧の行で行う。点を押すとその行が開き、先頭がその音）。
- 地図の欄のカードの下に 1 行：「選んだ音 N 件・種が未入力 a 件・質が未入力 b 件［一覧と書き出しへ］」
  （`GET /explore/picked` で数える。0 件なら案内文）。地図の高さはこの行の分だけ縮め、欄は画面に収める。
- 新しいページ `/datasets/[id]/explore/picked`：戻るリンク「音の地図に戻る」、選んだ音の一覧（小さい
  スペクトログラムと再生、日時、ファイル名と秒、`PickForm`、「外す」、書き出し済みの印）、書き出し
  （件数と未入力の数、保存先フォルダの選択〔Tauri はフォルダ選択、ブラウザでは入力欄〕、「書き出す（ZIP）」、
  保存先の表示、録音者名・ライセンス・座標のぼかしは入らない旨）。
- 文言は日英。チェックや入力でページがスクロールしないこと（v2.2 の方針）。

実装時に決めたこと（2026-11-05、実装の確認で）：書き出した後に入力を直しても `exported_at` は消さない（その音は一度渡したことを示す印のため）。ZIP のほかの種はカンマだけで区切る（テンプレートの例どおり）。remarks の区間の長さは実際の長さ。CORS の許可に DELETE を足した。

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

2026-11-01：1〜3 を実施した（記録は [implementation-status.md](implementation-status.md) の同日の節）。

1. (a) 方法の比較ベンチ（Codex に依頼、`poc/` のスクリプト。アプリには入れない）：
   富士の全区間注釈で候補 A・B を上記の指標で比較し、結果をこの文書に追記する。
   方式はユーザーが結果を見て確定する。
2. (a) sidecar：確定した方式で作成ジョブとテーブル・API（Codex に依頼、検証スクリプト付き）。
   1 地点 1 年相当（約 21 万区間）で所要時間を確認して報告。
3. (a) web：探索ページ（Opus のサブエージェントに依頼。i18n・typography・UX チェックリスト遵守）。
4. (b) sidecar → web を同様に分担。
5. 各段階で差分を確認してからコミット。`docs/ux-checklist-2026-09.md` の観点で流れを通す。
