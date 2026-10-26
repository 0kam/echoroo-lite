# メインAI・比較AI API（0.9.0、2026-10-26）

認証は既存の Bearer token。`apps/web` は別実装。

`GET /models`、`PUT /models`、`POST /models/benchmark` は同じ形を返す。
初期設定では `main:"perch"`、`extra:[]`、`confirmed:false`。

```json
{
  "main": "perch",
  "extra": [],
  "confirmed": false,
  "models": [
    {"id":"perch","name":"Perch V2","window_s":5,"available":true,"enabled":true,"main_capable":true,"experimental":false,"species_count":14795,"license":"Apache-2.0","seconds_per_hour":null},
    {"id":"birdnet","name":"BirdNET 2.4","window_s":3,"available":true,"enabled":false,"main_capable":false,"experimental":false,"species_count":6522,"license":"CC-BY-NC-SA-4.0","seconds_per_hour":null},
    {"id":"birdnet-v3","name":"BirdNET+ V3.0 (preview)","window_s":5,"available":true,"enabled":false,"main_capable":false,"experimental":true,"species_count":11560,"license":"CC-BY-SA-4.0","seconds_per_hour":null},
    {"id":"kinglet-s","name":"Kinglet-S (preview)","window_s":5,"available":true,"enabled":false,"main_capable":true,"experimental":true,"species_count":317,"license":"Apache-2.0","seconds_per_hour":null},
    {"id":"kinglet-xs","name":"Kinglet-XS (preview)","window_s":5,"available":true,"enabled":false,"main_capable":true,"experimental":true,"species_count":317,"license":"Apache-2.0","seconds_per_hour":null}
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
固定seedの小さなノイズとチャープによる60秒・32 kHzモノラルのPCM WAVを一時生成する。
エンジンごとに独立した一時カタログを作り、通常と同じ解析ジョブを2回実行する。
モデル・ラベル・BirdNET地域モデルと推論sessionを先に読み込み、1回目をウォームアップ、
2回目を計測する。計測にはファイル読込・デコード、各AI用のリサンプリング、窓分割、
推論、候補生成、NPZ・埋め込み・SQLite保存、表示用結果の準備を含める。
BirdNET 2.4も通常のworker/session経路を使う（48 kHz・3秒窓）。測定専用の地点は
緯度35度・経度138度、Asia/Tokyo、通年の地域リスト。モデル読込・地域リスト作成・
初回初期化は計測外。一時データは完了時・エラー時とも削除し、利用者のカタログには書かない。
`seconds_per_hour` は `round(計測秒数 × 3600 / 音声秒数)` の非負整数で、未測定は null。
GET / PUT / POST と `models.json` 内の `benchmark.seconds_per_hour` に同じ値を保存する。
旧 `minutes_per_hour` は推論だけの測定で比較できないため破棄し、再測定までは null。
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
Perchを選んでいない場合はPerchの解析を要求しない。

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
