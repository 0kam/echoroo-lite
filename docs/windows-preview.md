# Windows展示版の準備と確認

対象日は2026-11-28。以下は現在の実装に対応する準備手順であり、Windows実機での
受け入れ完了記録ではない。PCは未定。M4 Proの実測からWindowsの速度を保証しない。

## 配布物を作る

`.github/workflows/desktop.yml` はWindows x64・Python 3.11で次を実行する。

1. Pythonの全APIテスト、Svelteの型検査・静的ビルド。
2. Pythonサービスの凍結、固定版モデルの準備。
3. 同梱したウグイスの実音声を最大30秒のWAVに変換し、凍結サービスで
   BirdNET／Perch推論、埋め込み検索、保存検索、抽出・不明保存、報告CSV／根拠音ZIP、
   26種のクイズ、認証、正常終了を確認する。
4. Tauriのインストーラーを作り、`windows-preview` 成果物へ保存する。

最初のworkflow実行には作業ブランチのGitHubへの反映が必要。
依存49パッケージはWindows x64・Python 3.11のバイナリ配布だけで解決できたが、
これはWindows上のインストール・DLL読み込み・推論成功の証明ではない。
上の3に使う `--sample-audio` 経路はMac凍結版で実際に通した。

手元のWindows環境で同じ確認を行う場合は、READMEの開発環境を用意して次を実行する。

```powershell
python scripts/setup_models.py
python scripts/freeze_sidecar.py
python scripts/check_frozen.py apps/desktop/binaries/echoroo-sidecar-x86_64-pc-windows-msvc.exe --models poc/models --sample-audio apps/sidecar/data/quiz/mp3/uguisu_song_01.mp3
npm run desktop:build
```

## 展示PCを事前に準備する

初回導入とモデル準備は、イベント前にインターネットがある環境で済ませる。
インストーラーはモデルを含まず、現段階では展示担当者がモデルを配置する。
初回の自動ダウンロード画面は未実装。
モデル・種名と個別クイズ素材の出典・利用条件は [model-sources.md](model-sources.md) を参照。
BirdNETの非商用条件と企業での実務利用については確認中で、業務利用の許諾を取得済みとは扱わない。

アプリを一度起動し、終了してから、準備済みの `poc/models` の中身を
`%APPDATA%\org.echoroo.lite\models` へコピーする。必要な構造は次のとおり。

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
オフライン展示では取得済み音源を使う。XCキーと個別素材の出典情報は別途確認中。
CIで通るAPI検査と、Windows実機の音声出力・ファイル選択・描画は分けて記録する。

### Macで済ませた通信遮断の確認

2026-09-08、Macの `sandbox-exec` で外部へのTCP接続がEPERMになることと、
loopbackへの接続が成功することを事前確認し、同じ制限を次の検査プロセスと子プロセスに適用した。
モデルは事前配置済み。PC全体のネットワーク設定は変更していない。

```sh
/usr/bin/sandbox-exec -p '(version 1) (allow default) (deny network-outbound) (allow network-outbound (remote ip "localhost:*"))' .venv/bin/python scripts/check_frozen.py apps/desktop/binaries/echoroo-sidecar-aarch64-apple-darwin --models poc/models --sample-audio apps/sidecar/data/quiz/mp3/uguisu_song_01.mp3
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
