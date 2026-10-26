# Windows展示版の準備と確認

対象日は2026-11-28。以下は現在の実装に対応する準備手順であり、Windows実機での
受け入れ完了記録ではない。PCは未定。M4 Proの実測からWindowsの速度を保証しない。

## UI再設計後の最新確認

`0e21166` の [Windows CI](https://github.com/0kam/echoroo-lite/actions/runs/34332806730)で全工程が成功した。
新しい画面構成で、NSIS導入、日英起動と設定保持、録音・両モデル候補、保存検索・判定、
24時間の報告図とセル選択、クイズの回答後表示、正常終了を確認。
この実行の `windows-preview` が再設計後のEXE/MSI、`windows-startup` が画面記録にあたる。
[取り込みレビュー記録](ui-redesign-review.md)にMacとブラウザの確認もまとめた。
下記の過去CI記録は、解析基盤と配布構成の検証経過として残す。

## 配布物を作る

`.github/workflows/desktop.yml` はWindows x64・Python 3.11で次を実行する。

1. 固定版の BirdNET 2.4・Perch V2 を準備し、Pythonの全APIテスト、Svelteの型検査・静的ビルド。
2. Pythonサービスを凍結する。アプリにクイズ用の録音は同梱しない（2026-10-26決定）。
3. CI専用の `scripts/fixtures/horornis-diphone-xc1177906.mp3` を繰り返して30秒のWAVを2日分作り、
   凍結サービスで BirdNET／Perch推論、埋め込み検索、保存検索、抽出・不明保存、
   報告CSV／根拠音ZIP、認証、通常の終了通知による正常終了を確認する。
   出題には2種が必要なため、1種のfixtureからクイズは作らない。
4. Tauriの単一版インストーラーを `Echoroo-Lite-0.9.0-windows-x86_64` 成果物へ保存する。
   NSISをサイレントインストールし、インストール先のサービスとデスクトップ起動を検査する。
   英語から日本語への切り替えと、再起動後の日本語設定の保持も検査する。
   実解析データの地点・種ページを開き、「鳴き声クイズ」から空のクイズページを開いて
   「クイズに使う鳴き声を追加」の表示を待つ。各時点の画面とアクセシビリティ情報を
   `windows-startup` に保存する。新しい構成のWindows実行結果は未確認。

2026-09-09、[PR #1](https://github.com/0kam/echoroo-lite/pull/1)の `f5db87e` を検査した
[Windows CI](https://github.com/0kam/echoroo-lite/actions/runs/34309839504)で、次が通過した。

- 全8件のPythonテスト、Svelteのエラー・警告0件、静的ビルド、PyInstaller凍結、EXE/MSI作成。
- NSISのサイレントインストール。導入先サービスで認証・SQLite/vec・26種クイズ・
  MP3取得・回答保存・正常終了。
- 導入したデスクトップの起動、1024×768画面での `Local service connected` 表示、正常終了。
  日本語への切り替えと、再起動後の日本語設定・接続表示の保持も通過。
  `windows-startup` の画像とアクセシビリティ記録を取得して確認した。
- 公式APIからのモデル取得と、BirdNET・Perchのモデル／日英ラベルのハッシュ検査。

**Windowsの実推論は通過した。** `ffacd4a` の
[CI](https://github.com/0kam/echoroo-lite/actions/runs/34316310190)で、30秒実音声の両モデル解析、
埋め込み検索、複数参照検索、無作為抽出と判定保存、検出／検索報告、CSV／根拠音ZIP、
26種クイズ、正常終了まで通過。通知なしの親パイプ切断でも正常終了した。
両モデル解析22.73秒、参照登録・描画取得・類似検索9.55秒、保存検索・抽出・判定5.72秒、
報告とCSV／根拠音ZIP1.30秒。EXE/MSI作成、NSIS導入、日英の画面起動、
日本語設定の再起動後の保持まで通過し、CI全体が合格した。起動画像も取得して日本語表示を確認した。
実解析データを使う追加の画面検査は `6f22ac4` の
[CI](https://github.com/0kam/echoroo-lite/actions/runs/34317928504)で、両モデルの結果、
保存検索、抽出した3区間と「不明」の保存済み判定まで画面表示を確認した。
CIはその表示の照合で失敗。Windowsのアクセシビリティ情報が判定セルを
`対象種 対象外 不明 保存済み` という一つの名前として返すため、`保存済み` 単独との一致にはならなかった。
取得画像でも判定の復元を確認し、検査を実際のセル名との照合へ修正した。
修正後の `3bdc543` の [CI・2回目](https://github.com/0kam/echoroo-lite/actions/runs/34319774637/attempts/2)は
全工程が合格した。保存済み判定に続き、検索報告の24時間セル、朝6時セルのキーボード選択、
クイズの出題、「わからない」の回答・正解名・写真・次の問題の表示、正常終了が通過した。
取得した報告セルとクイズ回答後の1024×768画像も目視確認した。
証拠は `.local-data/windows-preview-3bdc543/startup/`、全ログは
`.local-data/windows-preview-3bdc543/complete-attempt2.log`。
この実行の両モデル解析は13.73秒、参照検索3.24秒、保存検索・判定2.95秒、報告出力0.40秒。
CIの成功は、実際のスピーカー出力や展示PCの年間速度の保証ではない。

修正前の `fbef286` は `loading_birdnet` で240秒の制限に達し、毎分のスタックが
`multiprocessing/popen_spawn_win32.py:72` のproducer起動を示していた。
終了監視の標準入力読み込みが、継承された入力を初期化するWindowsのPython起動を妨げる
（[CPython #78961](https://github.com/python/cpython/issues/78961)）構成だったため、
Windowsでは `PeekNamedPipe` で終了通知または親の切断を待つよう変更して解消した。
検査の制限時間は延ばしていない。
Macでは従来の標準入力監視を維持する。修正後の凍結版は両モデル解析36.58秒、検索・報告・クイズ・正常終了まで通過。
通知なしの親パイプ切断でも正常終了を確認した。既存のPythonテスト全8件も通過。

画面検査用の `check_frozen.py --data NEW_DIRECTORY` は、検査用音源・解析・検索・報告を
同じ新規フォルダに保持する。既存のフォルダは上書きせず起動前にエラーにする。
Macでこの経路の実解析36.39秒、検索・報告・クイズ・正常終了、元音源と履歴の保持を確認した。
GUI用の保持データは検査専用で、ユーザーの本番カタログへ混ぜない。

モデル取得先を、公式メタデータに記載されたファイル本体APIへ変更し、Windowsで取得に成功した。
ただし `3bdc543` の初回CIでは同APIがHTTP 504を返した。モデル取得で停止した実行を、
推論や画面操作の不具合とは扱わない。同じコミットの2回目では取得・ハッシュ照合・実推論が通過した。
Macでも空のフォルダから準備し、モデル・ラベル全58ファイルが既存キャッシュと同一と確認した。
新規取得したモデルを使うMac凍結版では、30秒実音声の両モデル解析36.42秒、
検索・報告・26種クイズ・正常終了まで通過した。

`3bdc543` の配布成果物 `windows-preview` は284,347,998 bytes、GitHub記録のSHA-256は
`da1df317c44edbba5c4a71e8ebed97dff4753c7e3ecd4561f022ff9bb149c809`。
取得したインストーラーはローカルの `.local-data/windows-preview-3bdc543/installers/` にあり、
取得完了後に次のサイズ・SHA-256を確認した。

| ファイル | bytes | SHA-256 |
|---|---:|---|
| `nsis/Echoroo Lite_0.1.0_x64-setup.exe` | 141,893,271 | `64fbaf4cacb3ddbc71b63ff5b97f1aea09f43d0e1d214ee9380f1f6c51d3211b` |
| `msi/Echoroo Lite_0.1.0_x64_en-US.msi` | 142,630,912 | `f0ff458f91ed40e3464852a2661b903efa96138c6725016bc605e4966ef17342` |

以前はモデル取得が504で失敗したため、インストーラー作成をモデル準備より先に検査した。`7824d89` の
[CI](https://github.com/0kam/echoroo-lite/actions/runs/34305306420)は、幅1100px以下で接続状態を隠す
CSSによって画面起動検査が失敗した。非表示指定を削除し、`fc72332` で合格を確認した。

手元のWindows環境で同じ確認を行う場合は、READMEの開発環境を用意して次を実行する。

```powershell
python scripts/setup_models.py
python scripts/freeze_sidecar.py
python scripts/check_frozen.py apps/desktop/binaries/echoroo-sidecar-x86_64-pc-windows-msvc.exe --models poc/models --sample-audio scripts/fixtures/horornis-diphone-xc1177906.mp3
npm run desktop:build
```

## 展示PCを事前に準備する

配布物はWindows x64用で、両モデルのCPU実行を確認済み。WindowsのCPU・メモリの
最低仕様はまだ確定していない。M4 Pro / 24 GiBの年間実測を、そのまま別CPUの保証値にしない。
候補PCで下記の48本試験を通し、年間試験で12時間以内を確認する。

年間のディスク容量は、解析データに加えて元音源も見込む。292時間のモノラル16 bit PCMでは
`1,051,200秒 × サンプルレート × 2 bytes` が音声本体の容量となる。

| 録音形式 | 元音源の計算値 | 実測解析データ19.05 GBを加えた目安 |
|---|---:|---:|
| 24 kHz / 16 bit / mono | 50.46 GB | 69.51 GB |
| 48 kHz / 16 bit / mono | 100.92 GB | 119.96 GB |

GBは10億bytes。WAVヘッダ、モデル、アプリ、書き出し、バックアップと空き容量は別に必要。
解析データ容量は試験音声での実測であり、新しい録音で一定とは限らない。
再解析は履歴を保持するため、その分も増える。反復試験のハードリンクによる省容量を、
実際の年間録音の容量として使わない。

初回導入とモデル準備は、イベント前にインターネットがある環境で済ませる。
インストーラーはモデルを含まず、現段階では展示担当者がモデルを配置する。
初回の自動ダウンロード画面は未実装。
モデル・種名と個別クイズ素材の出典・利用条件は [model-sources.md](model-sources.md) を参照。
BirdNETの非商用条件と企業での実務利用については確認中で、業務利用の許諾を取得済みとは扱わない。

アプリを一度起動し、終了してから、準備済みの `poc/models` の中身を
`%APPDATA%\org.echoroo.lite\models` へコピーする。必要な構造は次のとおり。

2026-09-09には、Macで使用したモデル・ラベル58ファイルだけをまとめた
`.local-data/windows-preview/echoroo-models-2026-09-09.zip`（462,812,532 bytes）も用意した。
ローカルの準備物でありGitHubには含めない。中の `models` を上記へ配置する。
全ファイルを元データのSHA-256と照合済み。ZIPのSHA-256は
`b3574c2e52d2aa7d4ad66df51a962dda9958e9b27a43f65a284cec06cf7d7797`。
取得元・利用条件の記録と、ファイルごとのハッシュ一覧を同梱する。

```text
org.echoroo.lite/
  models/
    birdnet/                 # setup_models.py が準備したキャッシュ一式
    perch/perch_v2_no_dft.onnx
    perch-labels/assets/labels.csv
```

展示音源は、そのWindows PCで継続して使う固定フォルダへ置き、アプリから取り込む。
MacのSQLiteだけをコピーする方法では、保存されている絶対パスが一致しない。
音源、解析、保存検索、報告、クイズの履歴はWindows側で事前に用意する。
バックアップはアプリ終了後にデータフォルダ全体と参照元音源を保存する。

## 当日の操作案と受け入れ確認

以下は実装に基づく展示手順案。デモ対象種と展示PCの確定後、実機で一巡して記録する。

画面は、地点と録音群 → 録音一覧 → 録音と候補 → 類似検索 → 保存検索の判定 → 報告一覧 →
報告表示という順に独立したURLを移動する。クイズと設定は左側の「学習と設定」から開く。
Windowsの自動画面検査も、録音一覧の最初の録音、保存検索の最初のリンク、報告履歴の
最初のリンクを順に開く。ボタンとリンクはどちらのUI Automation型でも扱う。
開発ブラウザから確認する場合は、sidecarの接続情報を
`http://127.0.0.1:1420/?url=…&token=…` のクエリで渡す（URLフラグメントは使用しない）。

| 操作 | 確認する結果 |
|---|---|
| 起動、日本語／英語の切り替え | 接続完了、一般名＋学名、地点の時刻が変わらない |
| 地点と録音群の表示、音源の再生 | 元の録音とスペクトログラム、選択区間末尾の停止 |
| 事前解析したBirdNET／Perchの比較 | 両モデル共通の地域条件、0〜1スコアと未較正の説明 |
| 検出報告を開く | 種一覧、根拠音源、spiral plot、解析済み範囲、品質未評価 |
| 手元音源から複数参照の保存検索 | 類似度順、対象録音数、参照区間を保持 |
| 類似度帯から抽出して判別 | 対象種／対象外／不明を保存、検索報告へ反映 |
| 報告を書き出す | 保存先選択、SVG・PNG・CSV・選んだ根拠音がZIPに入る |
| クイズを実施・音源区間を追加 | 26種の音声と写真、初回答の保存、途中再開、オリジナル問題の再生 |
| 終了・再起動 | 解析、検索、判定、報告、問題セットと履歴を保持 |
| ネットワークを切って同じ流れを再実行 | 取り込み・両モデル・保存済み参照での検索・報告・クイズが利用可能 |

XCを当日オンラインで紹介する場合は、APIキー設定後に実サービスを事前確認する。
オフライン展示では取得済み音源を使う。XCキーは設定済みで、Mac配布版の実接続・取得・
選択区間からの検索を確認済み。Windowsでも別途設定する。個別素材の出典情報は確認中。
CIで通るAPI検査と、Windows実機の音声出力・ファイル選択・描画は分けて記録する。
`scripts/check_windows_install.ps1` の導入・サービス・画面起動・接続表示・正常終了は通過済み。
CIの英語環境から日本語への切り替えと再起動後の保持も通過した。
展示PCでの全操作・実際の音声出力・ネット切断時の確認は残る。

### Macで済ませた通信遮断の確認

2026-09-08、Macの `sandbox-exec` で外部へのTCP接続がEPERMになることと、
loopbackへの接続が成功することを事前確認し、同じ制限を次の検査プロセスと子プロセスに適用した。
モデルは事前配置済み。PC全体のネットワーク設定は変更していない。

```sh
/usr/bin/sandbox-exec -p '(version 1) (allow default) (deny network-outbound) (allow network-outbound (remote ip "localhost:*"))' .venv/bin/python scripts/check_frozen.py apps/desktop/binaries/echoroo-sidecar-aarch64-apple-darwin --models poc/models --sample-audio scripts/fixtures/horornis-diphone-xc1177906.mp3
```

30秒の実音声で両モデル解析33.37秒、参照登録・描画取得・類似検索1.14秒、
保存検索・抽出・不明保存0.46秒、検出／検索報告とCSV・音声ZIP0.20秒。
26種のクイズ・同梱MP3取得・回答保存も通過し、プロセスは終了コード0。
ログは `/tmp/echoroo-offline-frozen.log`。GUIの音声出力、Windowsのネット切断時の
動作、XCのオンライン接続を証明する試験ではない。

同じ制限で配布 `.app` 内の実行ファイルを直接起動する検査も試したが、
GUI操作ツールが実行中のアプリを認識できず、画面操作は未確認。
検査用プロセスと子プロセスの終了を確認して通常起動へ戻した。
この経路をオフラインGUIの合格記録には含めない。

## 年間規模の計測

`scripts/benchmark_protocol.py` は実音源から1分区間を作り、30分おきのファイル配置で
取り込み→両モデル→全埋め込み保存→検索→抽出→報告CSV／根拠音ZIPまで測る。
元の録音と本番DBを変更せず、新規の出力ディレクトリだけを使う。
まず48本、その後17,520本で実行する。既存の出力先は上書きしない。

```powershell
python scripts/benchmark_protocol.py --source C:\audio\test --output .local-data\protocol-48 --binary apps\desktop\binaries\echoroo-sidecar-x86_64-pc-windows-msvc.exe --count 48
python scripts/benchmark_protocol.py --source C:\audio\test --output .local-data\protocol-year --binary apps\desktop\binaries\echoroo-sidecar-x86_64-pc-windows-msvc.exe --count 17520
```

`inputs.json` に元音源・区間・ハッシュと反復規則、`result.json` に工程時間、
サンプル取得したプロセス群の最大RSS、DB等の保存容量、モデルハッシュを残す。
音声のハードリンクで入力の容量を節約するが、17,520本すべてでデコードと推論を行う。
繰り返し音源・人工的な日時を使う負荷試験であり、実際の年間の生態データではない。
12時間の比較はサービス処理時間で、初回モデル取得とGUI描画・手操作は別に測る。

Macでの年間負荷試験は検索・報告出力まで4時間8分25秒で完走し、
配布アプリで年間図とPNG／SVG出力も確認した。条件と詳細は
[年間負荷試験](annual-benchmark-2026-09.md) を参照。Windowsの実測は引き続き必要。
