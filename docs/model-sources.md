# モデル・種名データの出典（2026-09-08確認）

2026-10-24 の合意により、配布版は BirdNET 2.4 と Perch V2 を含む非商用・研究用の1種類に変更した。
2026-10-26 の合意により、鳥の鳴き声の録音は同梱しない。
同日の合意により、Kinglet-S (preview) と Kinglet-XS (preview) を試験的に追加した。
以下の標準版／BirdNET付き版の記述は、それ以前の確認記録である。

実装で使用するモデルと表示名の記録。配布物全体のライセンス確認が完了した記録ではない。
Pythonライブラリのライセンスと、学習済みモデル・種名・個別音源の条件を分ける。

## 出典と確認できた条件

| 対象 | 現在の取得元・版 | 配布元の表示と残項目 |
|---|---|---|
| BirdNET Python | `birdnet==1.1.1` | 同梱METADATAと[公式README](https://github.com/birdnet-team/birdnet#license)はコードをMIT、モデルをCC BY-NC-SA 4.0と表示 |
| BirdNET 2.4音響モデル | [Zenodo record 15050749、Version 1](https://zenodo.org/records/15050749)、`BirdNET_v2.4_tflite.zip` 内の `audio-model.tflite` | [公式モデル文書](https://github.com/birdnet-team/BirdNET-Analyzer/blob/main/docs/models.rst)はCC BY-NC-SA 4.0。一方、同日に取得した[Zenodo API](https://zenodo.org/api/records/15050749)の `metadata.license.id` は `cc-by-nc-4.0`。表記差を未解決として保持 |
| BirdNET 2.4地域モデル | 同じZenodoレコードの `BirdNET_v2.4_tflite_int8.zip` 内の `meta-model.tflite` | 同上。音響・地域の取得先はインストール済みbirdnetのダウンローダーを確認 |
| Perch V2 ONNX | [justinchuby/Perch-onnx](https://huggingface.co/justinchuby/Perch-onnx)、revision `da88bf8c37c0b3068fc706fe509f633b9fbd28c1`、`perch_v2_no_dft.onnx` | 固定revisionのモデルカードはApache-2.0。Googleのモデルを変換しDFTをMatMulへ置換した版。Lite側で重みを変更していない |
| Perch V2ラベル | [cgeorgiaw/Perch](https://huggingface.co/cgeorgiaw/Perch)、revision `c3c3c816976a6c1bb75140b8fbaef7397a6f708f`、`assets/labels.csv` | 固定revisionのモデルカードはApache-2.0。学名の一覧として利用 |
| Kinglet-S / Kinglet-XS preview1 | モデル提供者から直接受領。`poc/models/kinglet_preview1/README_handover.md`（2026-10-26）。配布予定：[GitHub release](https://github.com/0kam/echoroo-lite/releases/tag/models-kinglet-preview1) | Apache-2.0。Perch 2.0 から蒸留した試験版。日本の鳥317種、32 kHz・5秒入力。sigmoid(logits) は未較正の順位用スコアであり、生息確率ではない。0.9.0 は埋め込みを保存せず、検索には Perch を使う。公開URLからの取得は未検証 |
| 日本鳥類目録 | 日本鳥学会「日本鳥類目録 改訂第8版」公開xlsx | Part A・Bの種をPerch候補と和名に使用。xlsx自体はコミットせず、URL・SHA-256・名寄せ結果を生成JSONへ記録 |
| Wikidata | [Wikidata Query Service](https://query.wikidata.org/) | 学名・シノニムの対応と日英ラベルに使用（CC0）。取得日と件数を生成JSONへ記録 |
| eBird種コード | [eBird Taxonomy v2025](https://www.birds.cornell.edu/clementschecklist/download/)（Cornell Lab of Ornithology）の `eBird_taxonomy_v2025-4.csv` から `scripts/build_ebird_codes.py` で学名→SPECIES_CODE（CATEGORY=species、11,167種）を `apps/sidecar/data/ebird-codes.json` に変換（2026-09-24） | 種ページへのリンク作成にのみ使用。引用：Clements, J. F., et al. 2025. The eBird/Clements checklist of Birds of the World: v2025. 再配布条件は未確認。2026-09-30、問い合わせずに現状のまま残すと判断 |

Perchの元モデルは変換者のカードが示す[GoogleのKaggle配布](https://www.kaggle.com/models/google/bird-vocalization-classifier)。
固定revisionの両モデルカードとZenodo APIの応答を `.local-data/model-source-check` に保存した。
これは確認時の資料で、モデル本体の追加ダウンロードではない。

2026-09-09、通常のZenodoファイルURLで504が続いたため、同レコードのメタデータが示す
公式の `/api/records/15050749/files/<filename>/content` へセットアップの取得先を変更した。
新規取得した音響・地域アーカイブのサイズとMD5がレコードの記載に一致し、
展開後のモデル・ラベルを含む全58ファイル（Perchを含む）が既存キャッシュのSHA-256と一致した。
セットアップ時はBirdNETのモデル本体と日英ラベルも固定SHA-256で検査する。
この変更は取得経路の修正であり、使用モデル・利用条件の変更ではない。

BirdNETの2つの表記はいずれも非商用条件を含む。企業の環境部署での実務利用が
許諾範囲に入ると、アプリ側では断定しない。別途許諾・相談の有無をユーザーに確認中。
標準版はPerchだけを同梱し、BirdNETモデルを含めない。BirdNET付き版は別identifierで
非営利用として配布し、設定のライセンス欄に条件を表示する。
個別許諾の取得、モデルの交換、追加の機能制限はまだ決めていない。

## 現在使用しているファイルのSHA-256

BirdNETはインストール済みキャッシュを実測。Perchは取得スクリプトの固定値と
既存ファイルを照合済み。解析履歴にもモデルとラベルのハッシュを保存する。

| ファイル | SHA-256 |
|---|---|
| BirdNET 音響 `model-fp32.tflite` | `55f3e4055b1a13bfa9a2452731d0d34f6a02d6b775a334362665892794165e4c` |
| BirdNET 地域 `model-fp32.tflite` | `33aea6d21cc887d2414e9596d2531a480a3e5f4770c22aa257f217fb757d4653` |
| BirdNET `en_us.txt` | `b50b77b7c3dfe40cd637e8cccdca0173a0a4ddee8867b830ff3c1a566f477f16` |
| BirdNET `ja.txt` | `158c5360676d7fc154b95ed7f161cf845be9ca219a67960901463ddd870b6362` |
| Perch `perch_v2_no_dft.onnx` | `4dcf71c18a147198545944bb5149697e89e3ad2e16637fa8f0edf6d13035a017` |
| Perch `labels.csv` | `e4d5c0397d8fb08bf90c6b13a34810af53504faad927e472fcc567793c9de057` |
| Kinglet-S `kinglet-s-preview1.onnx` | `a21fdfc6f3356e209d800656fc0ce1977a6fb37935eafef69998f7d94098cc03` |
| Kinglet-XS `kinglet-xs-preview1.onnx` | `4da91d8725e909e8e85fe3333b2b57d7a300b3f9bd1956b7b7119441da622442` |
| 両 Kinglet `*.classes.csv` | `1b58543e61ba505763c2f570e65ebccecf71d93f3a8177564d68738f85e6dcaa` |

種名JSONは `scripts/build_species_names.py`、モデル準備は `scripts/setup_models.py`。
配布版は BirdNET 2.4、Perch V2、Kinglet-S (preview)、Kinglet-XS (preview) をインストーラーへ同梱する。
Kinglet の配布用ファイル名は `models/kinglet/kinglet-{s,xs}-preview1.onnx` と
同名の `.classes.csv`。`scripts/setup_models.py` が固定 SHA-256 を照合し、公開予定の
GitHub assets または `--kinglet-from DIR` の受渡しフォルダから準備する。
各317学名は既存の表示用種名と一致し、欠けていたアホウドリ（`Phoebastria albatrus`）を
日英名付きで追加した。保存形式と設定 API は [API 引継ぎ](kinglet-preview-api.md)を参照。
クイズには[利用者が追加した音源](quiz-implementation.md)だけを使う。
ロゴ・配色の流用元は[種名と外観](species-and-branding.md#echoroo-full-から流用したもの)を参照。
外部配布用の帰属表示・ライセンス本文一式は、[第三者通知](../THIRD_PARTY_NOTICES.md)に記録する。上記のモデル表記差の記録は保持する。
