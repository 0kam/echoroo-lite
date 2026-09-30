# 標準版と BirdNET 付き版（2026-09-30 設計案）

背景と了承事項は [requirements.md](requirements.md) の「初回導入」「検出・埋め込みモデルの構成」。
ライセンス調査の要点は [model-sources.md](model-sources.md) に記録する。

## 了承済みの方針

- 埋め込みは Perch V2 に固定する。
- 標準版は Perch のみ（商用利用可）。BirdNET 2.4 のファイルを一切含めない。
- BirdNET 付き版は BirdNET 2.4 ＋ Perch。非営利向けとして利用条件を明示し、別に配布する。
- Perch の候補は、両版とも日本鳥学会「日本鳥類目録 改訂第8版」掲載種で絞る（Perch の学名へ名寄せ）。
- 和名・英名は BirdNET のラベルから作らない。
- eBird の種コードとリンクは、問い合わせずに現状のまま残す（2026-09-30 訂正）。

## 1. 2つの版の作り方

- `apps/desktop/tauri.bundle.conf.json`：標準版。Perch モデル・ラベルだけを `models/` に同梱。
- `apps/desktop/tauri.birdnet.conf.json`：BirdNET 付き版の差分。BirdNET 2.4 の音響・地域モデルを追加し、
  製品名を「Echoroo Lite BirdNET」、identifier を `org.echoroo.lite.birdnet` にする。
  identifier を分けるので、保存先（データ）も標準版と別になる。
- `npm run desktop:build`（標準版）と `npm run desktop:build:birdnet`（BirdNET 付き版）。
  Tauri CLI の複数 `--config` の結合で作れるか確認する。
- 版の判定はフラグを設けず、モデル置き場に BirdNET のモデルがあるかで決める。
  Perch がなければ従来どおり `models_missing`。BirdNET がなければ BirdNET の解析を行わない。
- 開発時の `poc/models` は BirdNET を含むため BirdNET 付き版として動く。
  標準版の確認は Perch だけを置いたフォルダを `ECHOROO_MODELS` に指定して行う。

## 2. Perch の候補の絞り込み（地域モデルの置き換え）

- `scripts/build_japan_species.py` で日本鳥学会の公開リスト（`jpbirdlist8ed_ver1.xlsx`）を読み、
  Part A と Part B の種（亜種は除く）を Perch の学名へ対応させて
  `apps/sidecar/data/japan-birds.json` を作る。xlsx はコミットしない。入手元 URL・版・SHA-256 を JSON に残す。
- 名寄せの順番：学名の完全一致 → Wikidata（CC0）の学名・シノニムによる対応 → スクリプト内の手動対応表
  （例：*Accipiter gentilis* → *Astur gentilis*）。Perch にない種（例：アホウドリ）は `unmatched` として JSON に残す。
- Perch の解析メタデータの `geo_filter` を `candidate_filter`（出典・SHA-256・種の一覧）に置き換え、
  両版とも Perch はこの一覧で絞る。BirdNET 付き版の BirdNET 自体の候補は従来どおり地域モデルで絞る。
- この一覧は日本国内の鳥だけなので、国外の地点は対象外とする（今回は国外向けの対応をしない）。

## 3. 和名・英名

- `scripts/build_species_names.py` を書き換え、Perch のラベルの学名（BirdNET 付き版で表示しうる BirdNET の学名も含める）に対して
  - 和名：日本鳥類目録の和名を優先し、なければ Wikidata の日本語ラベル。
  - 英名：Wikidata の英語ラベル。
  を取得して `species.json` を作る。取得日・出典・件数を JSON の `sources` に残す。名前のない種は学名で表示する（現状どおり）。

## 4. eBird

- 変更しない（当初は削除案だったが、2026-09-30 に残すと訂正）。

## 5. 帰属表示と利用条件

- 両版：設定画面に「ライセンス」欄を設け、Perch（Apache-2.0、Google）、日本鳥類目録（日本鳥学会）、
  Wikidata（CC0）を表示する。Apache-2.0 の全文をアプリに同梱する。
- BirdNET 付き版：上記に加えて BirdNET 2.4（CC BY-NC-SA 4.0、Cornell Lab of Ornithology・TU Chemnitz）と、
  非営利目的専用であることをライセンス欄に表示する。ホーム画面などへの注記は BirdNET チームの回答後に決める。
- 文言は日英のメッセージリソースに置き、大学1年生が読んで分かる表現にする。

## 6. 検査と文書

- 単体テスト：BirdNET なしで解析が完了すること、Perch の候補が `japan-birds.json` で絞られること。
- `check_frozen.py` を Perch だけのモデル置き場でも実行する。Windows CI は両版を作る。
- README、model-sources.md、implementation-status.md を更新する。

## 未決

- BirdNET 付き版の注記の出し方（ホーム画面に常時表示か、初回起動時か）。BirdNET チームの回答を見て決める。
- BirdNET チームへの問い合わせ結果（利用条件の食い違い、用途の解釈）。回答は model-sources.md に記録する。
