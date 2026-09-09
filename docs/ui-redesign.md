# UI/UX 再設計仕様（2026-09-09、ユーザー承認済み）

対象: `apps/web`（SvelteKit + Svelte 5 runes、adapter-static、Tauri v2）。
バックエンド（`apps/sidecar`）は変更しない。検証画面（validation-design.md）は今回の対象外。
コード・コメントは英語。UI 文言は `src/lib/i18n.ts` のキー経由で日英両方を必ず用意する。

## 1. 現状の問題

- `src/routes/+page.svelte` 1 枚に、取り込み・録音一覧・スペクトログラム・参照音・Xeno-Canto・
  保存検索・両モデル候補が縦に積み上がり、目的の操作が見つからない。ルートが無く戻る／リロードが効かない。
- ボタンを押しても押したボタン自体は変化しない（`busy` は内部状態のみ）。通信中の再クリックで二重送信が起きる。
  候補の再生は 30 分 WAV のメタデータ読み込み＋シークで数秒かかるが表示が無い（サーバーの Range 対応は確認済み）。
- 解析の進捗はサイドバー下部にしか出ない。種名管理・XC キーの置き場所が無い。

## 2. ルート構成（ハッシュルーティング）

`svelte.config.js` は `kit.router.type = 'hash'`（Tauri の asset protocol 内でも戻る／リロードが効く）。
開発時の sidecar 接続は `?url=…&token=…` のクエリで受け取る（旧 `#url=` は廃止）。

```
/                                 接続後、記憶している録音群があれば /datasets/[id]、無ければ /datasets へ
/datasets                         1. 地点と録音群: 地点ごとの録音群一覧・「録音群を追加」・解析状況（ジョブ一覧＋停止）
/datasets/new                        録音群の追加: ①地点（既存 or 新規: 名前・TZ・緯度経度）②録音群名とフォルダ ③確認→登録
/datasets/[id]                    2. 録音一覧: 日付で絞り込み・100 件ページング・行クリックで録音ページ・「まとめて解析」・進捗
/datasets/[id]/recordings/[rid]      録音を聴く: スペクトログラム＋区間再生、前後の録音へ移動、「この録音を解析」、
                                     「この録音から類似検索」、BirdNET | Perch の候補（2 列、狭い画面は 1 列）。行クリックで区間再生
/datasets/[id]/search             3. 類似検索: ①対象種 ②参照音と区間（この録音群の録音／PC のファイル／Xeno-Canto）
                                     ③検索する録音の日付 ④参照音セット→「保存して検索」。「この区間だけで試す」の結果一覧。
                                     右側に保存した検索の一覧（各行→判定ページ）
/datasets/[id]/search/[run]          保存した検索の判定: 類似度分布、無作為抽出フォーム、判定する区間の選択、
                                     ヒット表（再生・対象種／対象外／不明）。再生用プレーヤーは右側に固定
/datasets/[id]/reports            4. 報告: 作成フォーム（検出／検索）＋履歴一覧（各行→報告ページ）
/datasets/[id]/reports/[report]      報告の表示: 種一覧・spiral plot・セル選択・根拠音・書き出し。検索報告は分布と推奨閾値
/quiz                                クイズ: 問題セット・問題数・「学習を始める」、素材と問題セット（追加・有効/無効）、履歴
/quiz/[session]                      出題・回答・フィードバック・結果
/settings                            種名の管理、Xeno-Canto キー
```

## 3. アプリシェル

- 上部バー: ロゴ、「選択中の録音群: 地点 › 録音群 [切り替え]」、解析中チップ（録音群名と done/total、常時表示）、
  接続状態テキスト（既存キー `ready` / `connecting` / エラー）、言語セレクト（`aria-label` = `t('language')`）、配色ボタン。
  ※ Windows 確認スクリプトが `Language` という名前のコンボボックスと `Local service connected` /
  `ローカルで接続済み` のテキストを探すため、これらは上部バーに残す。
- 左サイドバー: 「作業の流れ」①地点と録音群 ②録音と候補 ③類似検索 ④報告、「学習と設定」♪クイズ ⚙設定。
  ②〜④は選択中の録音群への相対リンク。未選択なら無効表示＋ツールチップ。
- 選択中の録音群は `localStorage` に記憶し、`/datasets/[id]/...` を開いたら更新する。

## 4. 反応の見える化（連打対策）

- 共通 `Button` 部品: `pending` でスピナー表示＋`disabled`＋`aria-busy`。`href` があれば `<a class="button">`。
- `createTask(fn, { toast })` ヘルパー: ボタン単位の pending/error。pending 中の再実行は無視。
- API 層に進行中リクエスト数カウンタを置き、150 ms 以上続く通信で画面上端に細い進行バーを出す。
- トースト（成功／失敗／情報）: 「解析を開始しました」「録音を登録しました」「検索を保存しました」「報告を作成しました」等。
- 解析ジョブは上部バーのチップと、録音群ページ・録音ページのカードに進捗バー＋停止ボタン。ジョブが動作中は
  解析ボタンを「解析中」表示で無効化（同時に 1 ジョブのみ。API は 409 `job_busy`）。
- `AudioRegion` は `status: 'idle' | 'loading' | 'playing'` を bindable で公開。候補行・ヒット行・根拠行は
  読み込み中→再生中を行のハイライトとボタン内表示（スピナー／❚❚／▶）で示す。再生中の行を再度押すと停止。
- 判定ボタン・XC 取得ボタン・クイズの選択肢は行（ボタン）単位の pending。画面全体を無効化しない。
- 種名検索は入力しながら自動検索（250 ms デバウンス、Enter でも可）。
- 候補ページの注意書き（未較正スコア等）は `details` にまとめ、一覧を先に見せる。

## 5. スタイル

- `src/lib/theme.css` の Rose Pine 系トークンは維持。`src/routes/app.css` を全面改稿:
  ボタン最小高さ 36px（sm 30 / lg 44）、フォーカスリング、`.panel`、`.page-header`（見出し＋一言説明＋主操作）、
  `.steps/.step-card`（番号付き手順）、`.two-col`、`.table-wrap`、`.row-link`、`.empty`（空状態＋CTA）、`.notice/.info/.error`。
- ライト／ダーク両対応。1280×900（Tauri 既定）と 1024×768 で横スクロールが出ないこと。

## 6. 既存部品の扱い

- 維持（props から `locale`/`t` を外し、`$lib/app.svelte` の `t`/`app.locale` を import）:
  `AudioRegion`, `SpiralPlot`, `SimilarityDistribution`, `SpeciesPicker`, `SpeciesNames`, `ReportView`, `QuizEditor`。
- 分割: `XenoCanto` → `XenoCantoKey`（設定）と `XenoCantoSearch`（検索・クイズ素材）。
- 廃止（ページへ移す）: `AudioWorkbench`, `SearchReview`, `ReportsPanel`, `QuizPanel`、旧 `+page.svelte`。
- `QuizEditor` の「選択中の録音」入力は無くし、PC のファイル／登録済み参照音／Xeno-Canto から選ぶ。
- Windows 確認スクリプト `scripts/check_windows_install.ps1` は新しい画面遷移に合わせて更新する
  （録音一覧の最初の行を開く、保存検索・報告履歴は一覧の最初のリンクを押す等）。Mac では実行できないので CI で検証。

## 7. 受け入れ確認

- `npm run check` がエラー 0・警告 0、`npm run build` 成功。
- 開発ブラウザ（`npm run dev` ＋ `python apps/sidecar/server.py --data … --models …`、`?url=&token=` で接続）で
  取り込み → 解析 → 候補再生 → 類似検索 → 判定 → 報告 → クイズの一連を日英両方で通す。
- 各操作でボタンの pending 表示・進行バー・トーストが出ること。連打しても二重送信されないこと。
- `docs/implementation-status.md` と `docs/windows-preview.md` の該当箇所を更新する。
