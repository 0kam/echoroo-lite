# Kinglet preview1: API 引継ぎ（0.9.0）

`apps/web` はこの実装では編集しない。認証は既存と同じ Bearer token。

## モデルの設定

`GET /models` は登録順の配列を返す。全ファイルがある初回の正確な JSON は次のとおり。

```json
[
  {"id":"perch","name":"Perch V2","window_s":5,"required":true,"experimental":false,"species_count":14795,"license":"Apache-2.0","available":true,"enabled":true},
  {"id":"birdnet","name":"BirdNET 2.4","window_s":3,"required":false,"experimental":false,"species_count":6522,"license":"CC-BY-NC-SA-4.0","available":true,"enabled":true},
  {"id":"kinglet-s","name":"Kinglet-S (preview)","window_s":5,"required":false,"experimental":true,"species_count":317,"license":"Apache-2.0","available":true,"enabled":true},
  {"id":"kinglet-xs","name":"Kinglet-XS (preview)","window_s":5,"required":false,"experimental":true,"species_count":317,"license":"Apache-2.0","available":true,"enabled":true}
]
```

`PUT /models` の本文は `{"enabled":["perch","kinglet-s"]}`。
返却形式・順序・全フィールドは GET と同じで、この例では BirdNET と Kinglet-XS の
`enabled` だけが `false` になる。Perch の省略や空配列でも Perch を有効に保つ。
重複は除く。不明な ID または利用できない ID を指定すると HTTP 400、
`{"detail":"invalid_input"}`。本文の型・必須項目の不備は既存APIと同じ HTTP 422、
`{"detail":"invalid_input"}`。利用できないモデルも一覧には残り、`available:false` になる。

設定はデータルートの `models.json` に原子的に保存する。
`enabled` 未設定時は、利用できる全モデルを使う。`directory` は保存結果を読む既存処理が
利用可否を確認するためのモデルフォルダで、起動時に現在のインストール先へ更新する。
Perch は常に `enabled:true`。`GET /health` の既存 `birdnet` は利用可否のまま保持し、
`models` には有効かつ利用可能な ID の配列を追加した。

## 解析と結果

- `POST /jobs` の本文・返却 `{"id":123}` は従来どおり。開始時に有効かつ利用可能な
  エンジンと未実行の組合せを固定する。途中の設定変更は次のジョブに反映する。
  同じ `model_sha256` の最新解析がある録音／モデルは実行しない。Kinglet のクラス一覧の
  SHA 変更、出力ファイル欠落、BirdNET の地点変更がある場合は実行する。
  Perch が利用できない場合は HTTP 400、`{"detail":"models_missing"}`。
  実行対象がなければ従来と同じ HTTP 400、`{"detail":"no_audio"}`。
- `/catalog` のジョブ `total` は実際に実行する録音／モデルの組合せ数で、`done` は完了数。
  新しい stage は `loading_kinglet-s`、`loading_kinglet-xs`、`kinglet-s`、`kinglet-xs`。
  表示する進捗メッセージを日英で追加すること。
- `/datasets/{id}/overview` の `analysed_recordings` は、全ての有効かつ利用可能なエンジン
  （Perch は必須）の解析がそろった録音数。追加した `unanalysed_recordings` は不足のある録音数。
  設定変更時には再取得し、不足があれば解析ボタンを表示する。
- 解析設定は保存済みの結果を隠さない。結果の `models`・種の `checks` には保存済みの
  Kinglet も含む。`mixed_models` は同じエンジンの複数バージョンを示し、4種類の
  エンジンがあるだけでは `true` にならない。表示名は登録名を使う。
- `/datasets/{id}/species/{species_id}?engine=kinglet-s` または `kinglet-xs` を受け付ける。
  `auto` は既定スコア以上の検出区間数が最多のエンジンを選ぶ。同数なら既存の順序を保つ。
  `region` の根拠は引き続き保存済み BirdNET 地域リストのみ。
- `POST /reports/detection` の `engine`、`POST /validations` の `model` に両 Kinglet ID を追加。
  本文の他のフィールドとレスポンス形式は従来どおり。Kinglet の `window_s` は 5。
  presence、CSV、HTML、PNG/SVG も同じ保存スコアを使う。

## Kinglet の保存形式

入力は32 kHzモノラル、5秒・5秒ホップ、末尾ゼロ埋め。CPU ONNX session を各エンジンで
1つだけ作り、最大32窓のバッチで `logits` のみ取得する。Perch と同じ ORT スレッド設定。
sigmoid をかけた後の非 pickle NPZ は `scores: float32[n_windows,317]` と
`labels: str[317]`。窓開始は行番号×5秒。末尾も保存するが、レポートと検証は従来どおり
完全な窓だけを使う。Kinglet 埋め込み・segments・vectors は保存しない。

解析 `metadata` の例（ハッシュは実ファイル、species はCSV順317学名）:

```json
{
  "model":"Kinglet-S (preview)",
  "model_sha256":"a21fdfc6f3356e209d800656fc0ce1977a6fb37935eafef69998f7d94098cc03",
  "classes_sha256":"1b58543e61ba505763c2f570e65ebccecf71d93f3a8177564d68738f85e6dcaa",
  "window_s":5,
  "score_type":"sigmoid_uncalibrated",
  "species":["Prunella collaris","Scolopax mira","Anthus rubescens"],
  "stored_top_k":null,
  "display_top_k":5,
  "last_window":"zero_pad",
  "resampling":"scipy.resample_poly"
}
```

上の `species` だけは紙面上3件に省略した。表示候補は窓ごとの上位5種で、録音の結果APIは
全窓の上位100件まで返す。集計・確認には317種すべてのスコアを使う。
Perch の softmax 経路、BirdNET の地域フィルタ経路を Kinglet には適用しない。

## UI 側でつなぐ箇所

設定の1か所で上の GET / PUT を使い、`required` を解除不可、`available:false` を選択不可にする。
設定保存後は overview を取り直す。モデル名・プレビュー表示・API型・進捗文・AIごとの確認・
図のエンジン選択を4モデルに対応させる。検索は Perch のまま。較正されていない試験版であること、
対象が日本の鳥317種であることを表示する。

## 変更ファイルと確認結果

変更したファイル（既存の `docs/requirements.md` と別エージェントの `apps/web` の変更は保持）:

- モデル登録・推論・API: `apps/sidecar/models.py`（新規）、`analysis.py`、`server.py`。
- 集計・確認・出力: `apps/sidecar/management.py`、`results_workflow.py`、`reports.py`、
  `validation.py`、`workflow_export.py`、`report_figures.py`、`report_document.py`。
- 同梱データ: `apps/sidecar/data/species.json`、`data/report/messages.json`。
- テスト: `apps/sidecar/test_models.py`（新規）、`test_management.py`、
  `test_report_document.py`、`test_sidecar.py`。
- 準備・配布: `scripts/setup_models.py`、`scripts/check_frozen.py`、
  `apps/desktop/tauri.bundle.conf.json`、`.github/workflows/release.yml`、`desktop.yml`、
  `THIRD_PARTY_NOTICES.md`。
- 文書: `README.md`、`docs/model-sources.md`、`docs/install-guide.md`、本書（新規）。

2026-10-26 の確認:

- `cd apps/sidecar && ../../.venv/bin/python -m unittest discover -s .`: **52 tests OK**。
  偽 ONNX session を使い、初期設定、保存・再起動、無効ID・利用不可ID、Perch必須、
  CORS PUT、追加分だけの解析、ハッシュ変更時の再解析、末尾ゼロ埋め、317種のスコア、
  埋め込み非保存、overview / species / presence / 検証 / CSV / HTML / SVG を確認。
- `scripts/setup_models.py --kinglet-from poc/models/kinglet_preview1`: 成功。
  Kinglet の4ファイルを `poc/models/kinglet/` に作成し、固定 SHA-256 を検証。
  各CSVの317学名すべてが表示用種マスタに対応することも確認。
- このワークツリーの `poc/models` は、許可範囲外を指すシンボリックリンクだった。
  元の各モデルを引き続き参照する子リンクを持つローカルディレクトリに置き換え、
  Kingletだけをその中へコピーした。元の共有モデルは変更していない。
- `/tmp/echoroo-kinglet-sidecar` に指定の Python server ラッパーを作成し、
  `scripts/check_frozen.py` を `--models poc/models --data /tmp/echoroo-kinglet-real-check-20261026
  --sample-audio scripts/fixtures/horornis-diphone-xc1177906.mp3` で実行。
  サンドボックスにより `127.0.0.1` の bind が拒否され、推論開始前に停止した。
- 実モデルを使う補助の TestClient チェックでも、4エンジン時は BirdNET の
  POSIX shared memory 作成が拒否された。BirdNET を API で無効にした3エンジンでは、
  30秒×2録音の実推論（6組、約2.64秒）、各モデルのレポートと検証、Perch検索、
  日英 HTML / CSV / PNG / SVG 出力、同一解析のスキップが成功。
  これはプロセス起動・凍結版・実UI・Windows の検証結果ではない。
  補助スクリプトは `/tmp/check_kinglet_real_api.py`、結果は
  `/tmp/kinglet-real-api-three.log` に保持した。

Kinglet release assets は公開予定のURLのまま。CIが新規取得できること、凍結・
インストーラー同梱、4エンジン実行の完走、UIでの操作は未検証。コミットしていない。
