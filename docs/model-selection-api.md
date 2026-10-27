# メインAI・比較AI API（2026-10-27）

認証は既存の Bearer token。`GET /models`、`PUT /models`、
`POST /models/benchmark` は同じ形式を返す。初期値は次のとおり。

```json
{
  "main": "perch",
  "extra": [],
  "confirmed": false,
  "seconds_per_job": null,
  "models": [
    {
      "id": "perch",
      "name": "Perch V2",
      "window_s": 5,
      "main_capable": true,
      "experimental": false,
      "default_threshold": 0.1,
      "species_count": 14795,
      "license": "Apache-2.0",
      "available": true,
      "enabled": true,
      "seconds_per_hour": null,
      "seconds_per_file": null
    }
  ]
}
```

例では先頭1件のみ掲載。実際は利用不可でも次の6件をこの順で返す。
`available` は必要ファイルの有無、`enabled` はメインまたは追加に選択済みかを表す。

| id | name | window_s | main_capable | experimental | default_threshold | species_count | license |
|---|---|---:|---|---|---:|---:|---|
| perch | Perch V2 | 5 | true | false | 0.1 | 14795 | Apache-2.0 |
| kinglet-b | Kinglet-B | 5 | true | false | 0.5 | 317 | Apache-2.0 |
| kinglet-s | Kinglet-S | 5 | true | false | 0.5 | 317 | Apache-2.0 |
| kinglet-xs | Kinglet-XS | 5 | true | false | 0.5 | 317 | Apache-2.0 |
| birdnet | BirdNET 2.4 | 3 | false | false | 0.1 | 6522 | CC-BY-NC-SA-4.0 |
| birdnet-v3 | BirdNET+ V3.0 (preview) | 5 | false | true | 0.1 | 11560 | CC-BY-SA-4.0 |

`PUT /models` の本文例は `{"main":"kinglet-b","extra":["perch","birdnet"]}`。
利用可能で `main_capable:true` のAIをメインにできる。追加IDの重複は除去する。
未知・利用不可のID、メインと同じ追加IDは400 `invalid_input`、本文の型不備は422。
保存後は `confirmed:true`。設定はデータルートの `models.json` へ原子的に保存する。
選択外の結果は削除せず隠す。メインと同じエンジン・モデルSHAの埋め込みを検索・独自モデルに使う。

`POST /models/benchmark` は本文不要。利用可能な全AIを順番に、通常の一時解析ジョブで測る。
48 kHz WAVの640秒ジョブでウォームアップ後、160秒×1本、640秒×1本、160秒×4本を各3回測定する。
中央値から `処理秒数 = seconds_per_job + ファイル数 × seconds_per_file + 音声時間数 × seconds_per_hour`
を非負最小二乗で推定する。モデル読込は含まない。未測定はnull、時間単価は非負整数、固定費は非負・小数1桁。
保存形式は `benchmark.method:"marginal_v3"`。旧形式・PC情報の変化で測定値を破棄する。
解析中・変更ロック使用中は409 `job_busy`。利用者の録音・カタログには書かない。

解析はメイン＋利用可能な追加AIの不足する録音／AIの組合せだけ実行する。
モデルSHA・クラス表・保存スコア・必要な埋め込み・BirdNET地域情報で再利用を判定する。
`GET /datasets/{id}/overview` の `pending_analysis` は `[{"engine":"kinglet-b","recordings":2,"seconds":60.0}]` の形式。
旧版の解析はモデルSHAが異なる版として既存の `mixed_models` で扱い、DB移行はしない。

Kingletは [v0.1.0](https://github.com/0kam/kinglet/releases/tag/v0.1.0) の通常版だけを使用する。
公開データを使って Perch 2.0 から蒸留した Apache-2.0 モデル。
[モデルカード](https://github.com/0kam/kinglet/blob/main/MODEL_CARD.md)を参照。
32 kHz・5秒の `waveform:float32[batch,160000]` を入力し、
`embedding:[batch,1536]` と `logits:[batch,317]` を得る。
`sigmoid(logits)` を保存し、メタデータは `score_type:"sigmoid_calibrated"`。
クラスCSVは列名で読み、背景分位の4列は使用しない。
既定閾値は上表に従い、種ごとに確かめて決めた閾値があれば優先する。
presence候補もその閾値以上をスコア順で表示する。確認標本と出力する全スコアは閾値未満も含む。
