# アプリ内の更新（設計案）

2026-11-10。状態：**合意**（見本を見たユーザーが同日「この案で進める」と了承。鍵の作成と Secrets への登録も同日済み）。
見本は [prototypes/app-update-mock.html](prototypes/app-update-mock.html)。ブランチ `feat/app-update`（main の 0.1.3 から）。
下の「確かめていないこと・気がかり」は未決のまま。

## 決まっていること（ユーザー、2026-11-10）

- アプリの中に「更新する」ボタンを置く（お知らせを出してダウンロードのページを開くだけの方式は採らない）。
- 下書きのリリースを手で公開する今の流れは変えない。公開した時点で利用者に届く。

## 提案

### 利用者から見た流れ

1. 起動のたびに 1 回だけ、新しい版があるかを確かめる。オフラインや確認の失敗では何も出さない。
2. 新しい版があれば、どのページでも上の帯に「新しい版（0.2.0）があります。」と［更新する（約 750 MB）］［あとで］を出す。
   自動ではダウンロードしない。［あとで］は次の起動まで帯を消す（設定のページには残る）。
3. ［更新する］でダウンロードが始まり、帯に進み具合（312 / 748 MB）を出す。その間もアプリは使える。
4. 終わると帯が「更新の準備ができました。」と［再起動して更新］に変わる。勝手には再起動しない
   （聞いている途中・入力の途中で画面が消えないように）。
5. ［再起動して更新］で sidecar を止め、入れ替えて、新しい版で開き直す。
6. 解析や地図作りなど時間のかかる処理の間は、［更新する］も［再起動して更新］も押せず、
   横に「解析が終わると更新できます。」と出す。
7. 失敗したら帯に理由と［もう一度］［ダウンロードのページを開く］を出す。アプリはそのまま使える。
8. 設定のページに「アプリの更新」の欄を足す。いまの版の番号、［新しい版があるか確かめる］、
   結果（「最新の版です。」／新しい版があるときは帯と同じボタン）。
   起動したまま何日も使う人と、［あとで］を押した人の戻り先。

置かないもの：自動ダウンロード、更新の間隔や通知の設定、ダウンロードの中止ボタン
（アプリを閉じれば止まる）、版を飛ばす機能、変更点のアプリ内表示（帯の「変更点を見る」から
リリースのページを開く）。

### 互換性（データの形式）

- 今の版は、データの形式が合わないと理由を問わず「データフォルダを移動するか削除してから」と案内する。
  新しい版で作ったデータを古い版で開いたときにこの案内が出ると、利用者がデータを消しかねない。
  → データのほうが新しいときは別の案内にする：「このデータは、いまより新しい版のアプリで作られています。
  アプリを更新すると開けます。」と更新のボタン（帯と同じ部品）。データには触れない。
- 地図の版でデータの形式が v2 → v6 に上がる。コードを確かめたところ、v2 からの変更は表を足すだけで、
  既存の表と解析結果は書き換えない（消すのは地図の表だけで、v2 にはまだない）。
  → **自動バックアップは作らない**ことを提案する。データベースは解析結果を含み数 GB になりうるので、
  起動時の丸ごとコピーは時間とディスクを使い、守る対象（既存の表）は変更されないため。
  「更新すると前の版では開けなくなる」ことは、その版のリリースの説明（帯の「変更点を見る」の先）に書く。
- 「BirdNET の版との互換」は課題から外す。メインの AI に選べるのは Perch と Kinglet だけで
  （`models.py` の `main_capable`）、どちらも 1536 次元の特徴量を保存時に検査している。BirdNET は比較用のみ。

### 仕組み

- Tauri 公式の updater プラグイン（`tauri-plugin-updater`）。実装では、確認・ダウンロード・入れ替えを Rust 側で行い、
  再起動用の `tauri-plugin-process` は入れなかった。Tauri 本体は 2.12 に上げた（[実装状況](implementation-status.md)の 2026-11-10 の節）。
- 確認先：`https://github.com/0kam/echoroo-lite/releases/latest/download/latest.json`。
  下書きとプレリリースは `latest` にならないので、公開するまで誰にも届かない。
- ダウンロードしたファイルは、アプリに埋め込んだ公開鍵で署名を確かめてから入れる（OS の署名とは別）。
- Windows は NSIS インストーラーを静かに実行して入れ替える。実行ファイルを上書きするので、
  その前に sidecar を止めて終了を待つ（今の終了時の処理を関数にして共用する）。
  Mac は `.app` を入れ替えて再起動、Linux は AppImage を入れ替える。
- 送るもの：GitHub への確認とダウンロードの要求だけ。利用者のデータや識別子は送らない。
  README の「インターネットを使う所」に書き足す。

### リリースの手順への追加（`release.yml`）

- 更新用のファイル（Windows：インストーラーの `.sig`、Mac：`.app.tar.gz` と `.sig`、Linux：AppImage の `.sig`）を
  作り、`latest.json`（版・日付・各 OS の URL・署名・大きさ）を組み立てて下書きに付ける。
- 鍵がないと更新用のファイルは作れない。鍵のない環境（手元のビルド、PR の検査）は今のまま通るようにする。
- 版番号・タグ・公開の操作は今までどおり、リリースを頼まれたときだけ。

### 岡本さんの作業（実装の前に 1 回）

1. 鍵を作る：`npx tauri signer generate -w ~/.tauri/echoroo-lite-updater.key`（パスワードを付ける）。
2. GitHub の Secrets に `TAURI_SIGNING_PRIVATE_KEY`（鍵ファイルの中身）と
   `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` を登録する。
3. 公開鍵（`.key.pub` の中身）を知らせる。アプリに埋め込む。
4. 秘密の鍵とパスワードを別の場所にも保管する。なくすと、入っているアプリに更新を配れなくなる
   （全員に手動で入れ直してもらうことになる）。

## 確かめていないこと・気がかり

- ダウンロードは約 750 MB で差分更新はできない。プラグインは全体をメモリに読んでから署名を確かめるので、
  更新の間はメモリを 1 GB 前後使う見込み（8 GB の PC での実測が要る）。
- アプリを閉じるとダウンロードはやり直しになる（途中から再開はできない）。
- 署名なし（Windows）・ad-hoc 署名（Mac）のまま、アプリ内の更新で OS の警告が出ないか。
  Mac では更新後にフォルダへのアクセス許可を聞き直される可能性がある。実機で確かめる。
- Linux の `.deb` で入れた場合：プラグインのソース（2.14.1）では自分で更新できる（`latest.json` に
  `linux-x86_64-deb` の項目が要り、管理者パスワードを聞く）。実機では未確認。
- 0.1.3 には更新の機能がないので、最初の 1 回は手動で入れてもらう。
- 公開前の通し確認の方法：使い捨ての鍵と手元の確認先で、Mac で「古い版 → 新しい版」を実際に更新する。
  Windows と Linux は、プレリリースを確認先にした試験用のビルドで確かめる案（要相談）。

## 画面の文言

| キー | 日本語 | English |
|---|---|---|
| updAvailable | 新しい版（{version}）があります。 | A new version ({version}) is available. |
| updStart | 更新する（約 {size}） | Update (about {size}) |
| updStartNoSize | 更新する | Update |
| updLater | あとで | Later |
| updNotes | 変更点を見る | See what changed |
| updBusy | 解析が終わると更新できます。 | You can update when the analysis has finished. |
| updDownloading | 新しい版をダウンロードしています… {done} / {total} | Downloading the new version… {done} / {total} |
| updKeepUsing | このままアプリを使えます。アプリを閉じると、ダウンロードはやり直しになります。 | You can keep using the app. If you close it, the download starts over. |
| updReady | 更新の準備ができました。再起動すると新しい版になります。 | The update is ready. Restart to switch to the new version. |
| updRestart | 再起動して更新 | Restart and update |
| updFailed | 更新できませんでした。インターネットの接続を確かめて、もう一度試してください。 | The update did not work. Check your internet connection and try again. |
| updRetry | もう一度 | Try again |
| updOpenPage | ダウンロードのページを開く | Open the download page |
| updTitle | アプリの更新 | App updates |
| updCurrent | いまの版：{version} | This version: {version} |
| updCheck | 新しい版があるか確かめる | Check for a new version |
| updChecking | 確かめています… | Checking… |
| updLatest | 最新の版です。 | You have the latest version. |
| updCheckFailed | 確かめられませんでした。インターネットの接続を確かめてください。 | Could not check. Check your internet connection. |
| data_newer | このデータは、いまより新しい版のアプリで作られています。アプリを更新すると開けます。 | This data was made by a newer version of the app. Update the app to open it. |
