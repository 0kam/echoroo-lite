# モデル・種名データの出典（2026-09-08確認）

実装で使用するモデルと表示名の記録。配布物全体のライセンス確認が完了した記録ではない。
Pythonライブラリのライセンスと、学習済みモデル・種名・個別音源の条件を分ける。

## 出典と確認できた条件

| 対象 | 現在の取得元・版 | 配布元の表示と残項目 |
|---|---|---|
| BirdNET Python | `birdnet==1.1.1` | 同梱METADATAと[公式README](https://github.com/birdnet-team/birdnet#license)はコードをMIT、モデルをCC BY-NC-SA 4.0と表示 |
| BirdNET 2.4音響モデル | [Zenodo record 15050749、Version 1](https://zenodo.org/records/15050749)、`BirdNET_v2.4_tflite.zip` 内の `audio-model.tflite` | [公式モデル文書](https://github.com/birdnet-team/BirdNET-Analyzer/blob/main/docs/models.rst)はCC BY-NC-SA 4.0。一方、同日に取得した[Zenodo API](https://zenodo.org/api/records/15050749)の `metadata.license.id` は `cc-by-nc-4.0`。表記差を未解決として保持 |
| BirdNET 2.4地域モデル | 同じZenodoレコードの `BirdNET_v2.4_tflite_int8.zip` 内の `meta-model.tflite` | 同上。音響・地域の取得先はインストール済みbirdnetのダウンローダーを確認 |
| BirdNETの日英種名 | 上記音響モデルと同じ配布物の `labels/en_us.txt` と `labels/ja.txt` | モデルに付随するラベルをJSONへ変換。ラベル単独での別ライセンスは未確認。PythonコードのMITと同一と推測しない |
| Perch V2 ONNX | [justinchuby/Perch-onnx](https://huggingface.co/justinchuby/Perch-onnx)、revision `da88bf8c37c0b3068fc706fe509f633b9fbd28c1`、`perch_v2_no_dft.onnx` | 固定revisionのモデルカードはApache-2.0。Googleのモデルを変換しDFTをMatMulへ置換した版。Lite側で重みを変更していない |
| Perch V2ラベル | [cgeorgiaw/Perch](https://huggingface.co/cgeorgiaw/Perch)、revision `c3c3c816976a6c1bb75140b8fbaef7397a6f708f`、`assets/labels.csv` | 固定revisionのモデルカードはApache-2.0。学名の一覧として利用 |
| eBird種コード | [eBird Taxonomy v2025](https://www.birds.cornell.edu/clementschecklist/download/)（Cornell Lab of Ornithology）の `eBird_taxonomy_v2025-4.csv` から `scripts/build_ebird_codes.py` で学名→SPECIES_CODE（CATEGORY=species、11,167種）を `apps/sidecar/data/ebird-codes.json` に変換（2026-09-24） | 種ページへのリンク作成にのみ使用。引用：Clements, J. F., et al. 2025. The eBird/Clements checklist of Birds of the World: v2025. 再配布条件は未確認のため、配布前に確認する |

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
また、現在はPerchの分類にもBirdNETの地域モデルを使うため、Perchの分類だけを
表示すればBirdNETの条件と無関係になる、という説明はしない。
研究展示の実装を継続し、一般向け配布の案内へ「業務利用が無条件に可能」と記載しない。
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

種名JSONは `scripts/build_species_names.py`、モデル準備は `scripts/setup_models.py`。
モデルをインストーラーへ含めない現在の構成でも、利用条件の確認は別途必要。
26種のクイズ素材の音声・写真は[クイズの出典記録](quiz-implementation.md#素材の由来)、
ロゴ・配色の流用元は[種名と外観](species-and-branding.md#echoroo-full-から流用したもの)を参照。
外部配布用の帰属表示・ライセンス本文一式は、個別素材と上記表記差を確認して整える。
