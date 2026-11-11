# インストールの手順（Echoroo Lite 0.2.0）

[English](#installation-echoroo-lite-010)

## 利用条件

**現時点では日本国内の録音での利用に限ります**（判定の対象を日本の鳥の一覧に絞っているため）。

配布するのは、BirdNET 2.4 と Perch V2 を含む **Echoroo Lite の1種類だけ**です。
Kinglet-B・Kinglet-S・Kinglet-XS v0.2.0 も同梱します。
3モデルは公開データを使って Perch 2.0 から蒸留した日本の鳥317種向けのモデル（Apache-2.0）です。スコアは背景を含む318列で softmax を計算後、背景列を除いた確率で、検出の既定閾値は0.1です。
BirdNET+ V3.0 (preview) も比較用に同梱します（CC BY-SA 4.0、研究・評価用、密猟・軍事目的は禁止）。
Powered by BirdNET。詳しくは[第三者通知](../THIRD_PARTY_NOTICES.md)をご覧ください。
初回にメインAIを1つ選びます。既定はPerch V2で、追加の比較AIは任意です。
このアプリの利用条件は **非商用・研究用**です。BirdNET 2.4 のモデルが
CC BY-NC-SA 4.0 で提供されているためです。鳥の鳴き声の録音は同梱しません。
クイズには、自分の録音や、自分のAPIキーで取得した Xeno-Canto の音源を追加して使います。

このアプリには開発元の電子署名がありません。そのため、初めて開くときに OS が警告を出します。下の手順で開いてください。

## Windows 10 / 11（64ビット）

1. [Releases](https://github.com/0kam/echoroo-lite/releases) から `Echoroo.Lite_0.2.0_x64-setup.exe` をダウンロードして開きます。
2. 「WindowsによってPCが保護されました」と出たら、**「詳細情報」**を押し、**「実行」**を押します。
3. 案内に沿ってインストールします。必要なら Microsoft Edge WebView2 も自動で入ります（インターネット接続が必要です）。
4. スタートメニューの「Echoroo Lite」から起動します。

## macOS 12 以降（Apple シリコン：M1〜M4 など）

Intel の Mac には対応していません。

1. [Releases](https://github.com/0kam/echoroo-lite/releases) から `Echoroo.Lite_0.2.0_aarch64.dmg` をダウンロードして開き、「Echoroo Lite」を「アプリケーション」フォルダへドラッグします。
2. 「アプリケーション」フォルダの「Echoroo Lite」を開きます。
   「“Echoroo Lite”は開いていません」「開発元を検証できません」と出たら、**「完了」**（または「キャンセル」）を押します。
3. **システム設定 → プライバシーとセキュリティ** を開き、下の方の「“Echoroo Lite”は使用をブロックされました」の横にある **「このまま開く」** を押します。
   パスワード（または Touch ID）を求められたら入力し、もう一度「開く」を押します。
4. 2回目からは普通に開けます。

「壊れているため開けません」と出る場合は、ダウンロードが途中で壊れています。もう一度ダウンロードしてください。

## Linux（64ビット、Ubuntu 22.04 以降など）

### .deb（Ubuntu・Debian 系）

```bash
sudo apt install ./Echoroo.Lite_0.2.0_amd64.deb
```

音声の再生に必要な GStreamer のプラグインも一緒に入ります。

### AppImage（その他の配布物）

```bash
chmod +x Echoroo.Lite_0.2.0_amd64.AppImage
./Echoroo.Lite_0.2.0_amd64.AppImage
```

音が出ない場合は、GStreamer のプラグインを入れてください（Ubuntu の例）。

```bash
sudo apt install gstreamer1.0-plugins-good gstreamer1.0-libav
```

## はじめて使うとき

- 録音した場所を地図で選び、音声のフォルダを指定すると、録音が追加されます。そのあと「録音を解析する」を押します。
- 初回に、解析に使うAIを選び、このパソコンでの解析の速さを測ります（十数秒）。録音を追加するときと解析の前に、かかる時間の目安が出ます。
- 元の音声ファイルは移動・変更されません。

## 新しい版への更新

0.2.0 からは、新しい版が出るとアプリの上のほうにお知らせが出ます。「更新する」を押すと、アプリの中で新しい版に入れ替えられます。

0.1.3 までの版にはこの機能がありません。その場合は一度だけ、上の手順で新しい版を入れてください。入れ直しても、下の保存場所のデータは残ります。

0.2.0 で一度開いたデータは、0.1.3 以前の版では開けなくなります（解析結果や確認の答えはそのまま残ります）。0.1.3 以前に戻して開くと「データフォルダを移動するか削除して…」という案内が出ますが、**データは削除せず**、0.2.0 以降を入れ直してください。

Releases のページにある `.sig`、`-update.app.tar.gz`、`latest.json` は、アプリが更新に使うファイルです。自分でダウンロードする必要はありません。

## データの保存場所とアンインストール

| OS | 保存場所 |
|---|---|
| Windows | `%APPDATA%\org.echoroo.lite` |
| macOS | `~/Library/Application Support/org.echoroo.lite` |
| Linux | `~/.local/share/org.echoroo.lite` |

アプリを削除しても、このフォルダは残ります。完全に消すときは、このフォルダも削除してください。

---

# Installation (Echoroo Lite 0.2.0)

## Terms of use

**For recordings made in Japan only, for now** (species are limited to the list of birds in Japan).

There is **one Echoroo Lite edition**, containing BirdNET 2.4 and Perch V2. It is
for **non-commercial / research use** because the BirdNET 2.4 models are
CC BY-NC-SA 4.0. No bird-call recordings are bundled in the app. Add clips from
your own recordings or Xeno-Canto with your own API key to use the quiz.
The app also bundles Kinglet-B, Kinglet-S and Kinglet-XS v0.2.0, Apache-2.0
models distilled from Perch 2.0 on public data for 317 Japanese bird species.
Scores are softmax probabilities over 318 classes with the background channel
dropped, with a default detection threshold of 0.1.
The app's non-commercial / research terms remain.
BirdNET+ V3.0 (preview) is also bundled for comparison (CC BY-SA 4.0, research
and evaluation; poaching and military use prohibited). **Powered by BirdNET**.
See [third-party notices](../THIRD_PARTY_NOTICES.md). On first run choose one
main AI (Perch V2 by default); comparison AIs are optional.

The app is not code-signed, so the operating system warns on first launch. Open it as follows.

## Windows 10 / 11 (64-bit)

1. Download and open `Echoroo.Lite_0.2.0_x64-setup.exe` from [Releases](https://github.com/0kam/echoroo-lite/releases).
2. If "Windows protected your PC" appears, click **More info**, then **Run anyway**.
3. Follow the installer. Microsoft Edge WebView2 is installed if missing (internet required).

## macOS 12 or later (Apple silicon)

Intel Macs are not supported.

1. Download `Echoroo.Lite_0.2.0_aarch64.dmg` from [Releases](https://github.com/0kam/echoroo-lite/releases), open it and drag Echoroo Lite to Applications.
2. Open it from Applications. If macOS says it cannot verify the developer, click **Done** (or Cancel).
3. Open **System Settings → Privacy & Security**, find "Echoroo Lite was blocked", click **Open Anyway**, authenticate, and click **Open**.
4. Later launches open normally.

If macOS says the app "is damaged", the download was corrupted; download it again.

## Linux (64-bit, e.g. Ubuntu 22.04 or later)

- `.deb`: `sudo apt install ./Echoroo.Lite_0.2.0_amd64.deb` (installs the GStreamer plugins needed for audio).
- AppImage: `chmod +x` the file and run it. If there is no sound, install `gstreamer1.0-plugins-good` and `gstreamer1.0-libav`.

## Updating

From version 0.2.0, the app shows a notice at the top when a new version is out. Press **Update** to switch to it inside the app.
Version 0.1.3 and earlier cannot do this: install the new version once by hand, as described above. Your data stays where it is.
Data opened once with 0.2.0 can no longer be opened by 0.1.3 or earlier (analysis results and answers are kept). If an older version tells you to move or delete the data folder, **do not delete it**: install 0.2.0 or later again.
The `.sig`, `-update.app.tar.gz` and `latest.json` files on the Releases page are used by the app for updates; you do not need to download them.

## Data and uninstalling

Data stays in `org.echoroo.lite` under the app-data folder
(Windows `%APPDATA%`, macOS `~/Library/Application Support`, Linux `~/.local/share`). Delete that folder to remove all data.
