# メインAI・比較AI API（0.9.0、2026-10-26）

認証は既存の Bearer token。`apps/web` は別実装。

`GET /models`、`PUT /models`、`POST /models/benchmark` は同じ形を返す。
初期設定では `main:"perch"`、`extra:[]`、`confirmed:false`。

```json
{
  "main": "perch",
  "extra": [],
  "confirmed": false,
  "seconds_per_job": null,
  "models": [
    {"id":"perch","name":"Perch V2","window_s":5,"available":true,"enabled":true,"main_capable":true,"experimental":false,"species_count":14795,"license":"Apache-2.0","seconds_per_hour":null,"seconds_per_file":null},
    {"id":"birdnet","name":"BirdNET 2.4","window_s":3,"available":true,"enabled":false,"main_capable":false,"experimental":false,"species_count":6522,"license":"CC-BY-NC-SA-4.0","seconds_per_hour":null,"seconds_per_file":null},
    {"id":"birdnet-v3","name":"BirdNET+ V3.0 (preview)","window_s":5,"available":true,"enabled":false,"main_capable":false,"experimental":true,"species_count":11560,"license":"CC-BY-SA-4.0","seconds_per_hour":null,"seconds_per_file":null},
    {"id":"kinglet-s","name":"Kinglet-S (preview)","window_s":5,"available":true,"enabled":false,"main_capable":true,"experimental":true,"species_count":317,"license":"Apache-2.0","seconds_per_hour":null,"seconds_per_file":null},
    {"id":"kinglet-xs","name":"Kinglet-XS (preview)","window_s":5,"available":true,"enabled":false,"main_capable":true,"experimental":true,"species_count":317,"license":"Apache-2.0","seconds_per_hour":null,"seconds_per_file":null}
  ]
}
```

`available` は必要ファイルの有無。モデルは登録順で、利用不可でも一覧に残る。
`enabled` は `id == main || extra.includes(id)`。`required` フィールドは廃止。

`PUT /models` の本文は `{"main":"kinglet-s","extra":["perch","birdnet-v3"]}`。
メインは利用可能な Perch / Kinglet-S / Kinglet-XS の1つ。追加は任意。
追加の重複IDは除去する。未知・利用不可のID、メインと同じ追加ID、
メインにできないIDは **400** `{"detail":"invalid_input"}`。
本文の型や必須項目の不備は **422**、同じエラー本文。
保存すると `confirmed:true`。初回の選択画面はこれが false の間だけ表示する。
データルートの `models.json` に `main / extra / confirmed / benchmark` を原子的に保存し、
内部用のモデル設置先 `directory` も保持する。
旧 `enabled` 配列は `main:"perch"`、その他の有効IDを `extra`、`confirmed:true` に移行する。

`POST /models/benchmark` は本文不要。**選択外も含む利用可能な全モデル**を順番に測る。
固定seedの小さなノイズとチャープによる1分・4分の48 kHzモノラルPCM16 WAVを一時生成する。
モデル・ラベル・BirdNET地域モデルと推論sessionを先に読み込み、4分ジョブでウォームアップする。
その後、各長さを3回ずつ、毎回独立した一時カタログで通常の解析ジョブとして実行し、中央値を使う。
録音処理の計測には読込・デコード、リサンプリング、窓分割、推論、候補生成、NPZ・埋め込み・
SQLite保存、表示窓の準備を含む。録音ループ外のジョブ処理とoverview作成は別に測る。
`録音処理秒数 = ファイル数 × seconds_per_file + 音声時間数 × seconds_per_hour` を非負最小二乗で推定する。

- `models[].seconds_per_hour`: 音声が1時間増えた場合の追加処理秒数。非負整数。
- `models[].seconds_per_file`: 録音1ファイルあたりの固定処理秒数。非負、小数1桁。
- トップレベル `seconds_per_job`: 録音ループ外の共通ジョブ費用。非負、小数1桁。
  各モデル・長さの残差の中央値のうち最大を採り、UIではジョブごとに1回だけ足す。

いずれも未測定は null。モデル読込・地域リスト作成・初回初期化は含めない。
BirdNET 2.4も通常のworker/session経路を使う（48 kHz・3秒窓）。測定専用の地点は
緯度35度・経度138度、Asia/Tokyo、通年の地域リスト。一時データは完了時・エラー時とも削除し、
利用者のカタログには書かない。GET / PUT / POST は同じ値を返し、保存先は
`benchmark.models.<id>.{seconds_per_hour,seconds_per_file}` と `benchmark.seconds_per_job`。
保存形式を `benchmark.method:"marginal_v2"` で識別する。ジョブ内でSQLite接続を再利用する実装に対応する。
旧 `marginal_v1`、旧 `minutes_per_hour`、1分全体から換算した旧 `seconds_per_hour` の
測定値は破棄し、再測定までは null。APIフィールドと推定式は変更しない。
モデル設定の `main / extra / confirmed` はそのまま保持する。
日時と `platform.machine()` / `os.cpu_count()` を保存し、PC情報が変われば測定値を破棄する。
この粗いPC情報ではCPU型番や負荷・電源設定の変化は判別しない。測定は概算である。
BirdNETはimport時に保存先を確定するため、起動時および共通のモデル読込処理で
`BIRDNET_APP_DATA=<models>/birdnet` をimport前に設定する。
アプリの変更用ロックを保持し、解析ジョブ実行中またはロック使用中なら
**409** `{"detail":"job_busy"}`。オフラインで動作する。

解析は開始時のメイン＋利用可能な追加AIについて、不足する録音／AIの組合せだけ実行する。
同じモデルSHAの解析済みスコアを再利用する。クラス表変更・スコア欠落・
埋め込み欠落、BirdNET 2.4の地点変更は再解析対象。メインのファイル不足は
400 `models_missing`、実行対象なしは400 `no_audio`。
`analysed_recordings` / `unanalysed_recordings` で不足を示す。

`GET /datasets/{id}/overview` に次を追加する。

```json
{"pending_analysis":[{"engine":"perch","recordings":20,"seconds":600.0}]}
```

有効かつ利用可能なAIについて、ジョブと同じSHA・保存スコア・埋め込み・クラス表・地点の条件で
不足する録音数と音声秒数を返す。不足のないAIは省略し、すべて解析済みなら `[]`。
対象は解析ボタンと同じ地点の全録音（同地点の追加インポート分も含む）で、overviewの表示期間では絞らない。
UIの推定秒数は `seconds_per_job + Σ(recordings × seconds_per_file + seconds / 3600 × seconds_per_hour)`。
`pending_analysis:[]` のときは0秒とする。未測定値を0秒として扱わない。
インポートのプレビューでは、有効な利用可能AIごとにプレビューの録音数・総秒数を使う。
複数AIの実ジョブは読込と同じレートへの変換を共有するため、合算値はその分やや大きくなる。
モデル読込、他のPC処理、音声形式、既存カタログの規模による差は含まない概算。

Perchを選んでいない場合はPerchの解析を要求しない。
複数AIのジョブは録音を1回だけ読み、32 kHzへの変換を1回で済ませ、ONNXモデル間で共有する。
BirdNET 2.4には同じ読込済み音声を `run_arrays` で渡し、既存workerの3秒窓ごとの48 kHz FFT変換を使う。
音声全体を事前変換しないため、録音末尾の時刻の丸めも従来どおりに保つ。
重い科学計算ライブラリは起動後にバックグラウンドで一度ウォームアップし、handshakeを待たせない。

選択から外したAIの解析・確認・検索・独自モデルは削除しない。
検出結果、overview、種一覧・種ページの確認、presence候補、報告・出力から隠し、
再選択時に再表示する。非表示AIの報告・確認IDを直接使うと400 `not_found`。
人の独立したpresenceマークは残す。確認回答由来のpresenceは、その確認AIの選択に従う。
保存済み検索はメインAIとモデルSHAが一致するときに使う。

Perchと両Kingletの解析は1536次元埋め込みを保存する。
`segments.analysis_id` → `analyses.engine / metadata.model_sha256` が生成元を特定するため、
既存DBのスキーマ変更は不要。類似検索、参照音のクエリ、保存検索、独自モデルは
メインAIと同じエンジン・モデルSHAのsegmentsだけを使う。
Kingletクエリは該当ONNXの `waveform` → `embedding`。
独自モデルのfeature specは `<engine>_embedding_1536_l2_v1`。
既存Perch独自モデルはメインPerchのときだけ表示・利用可能。

BirdNET V3は比較用のみ。32 kHz・5秒非重複・末尾ゼロ埋め、CPU ORT、最大32窓。
`input` → `predictions` の確率出力にsigmoidを重ねない。
保存NPZは `scores:float32[n,11560] / labels:str[11560]`。
候補と集計対象はアプリの日本鳥リストのPerch学名との積集合。
`score_type:"probability_uncalibrated"`、1280次元の埋め込みは保存しない。
報告・確認・presence・CSV・HTML・PNG/SVGはKingletと同じ登録モデル経路を使う。
進捗stageには `loading_birdnet-v3` / `birdnet-v3` が加わる。
モデルの条件・帰属 **Powered by BirdNET** は [第三者通知](../THIRD_PARTY_NOTICES.md) を参照。
