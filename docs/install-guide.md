# インストールの手順（Echoroo Lite 0.9.0）

[English](#installation-echoroo-lite-090)

## 利用条件

配布するのは、BirdNET 2.4 と Perch V2 を含む **Echoroo Lite の1種類だけ**です。
このアプリの利用条件は **非商用・研究用**です。BirdNET 2.4 のモデルが
CC BY-NC-SA 4.0 で提供されているためです。鳥の鳴き声の録音は同梱しません。
クイズには、自分の録音や、自分のAPIキーで取得した Xeno-Canto の音源を追加して使います。

このアプリには開発元の電子署名がありません。そのため、初めて開くときに OS が警告を出します。下の手順で開いてください。

## Windows 10 / 11（64ビット）

1. `Echoroo Lite_0.9.0_x64-setup.exe` をダウンロードして開きます。
2. 「WindowsによってPCが保護されました」と出たら、**「詳細情報」**を押し、**「実行」**を押します。
3. 案内に沿ってインストールします。必要なら Microsoft Edge WebView2 も自動で入ります（インターネット接続が必要です）。
4. スタートメニューの「Echoroo Lite」から起動します。

## macOS 12 以降（Apple シリコン：M1〜M4 など）

Intel の Mac には対応していません。

1. `Echoroo Lite_0.9.0_aarch64.dmg` をダウンロードして開き、「Echoroo Lite」を「アプリケーション」フォルダへドラッグします。
2. 「アプリケーション」フォルダの「Echoroo Lite」を開きます。
   「“Echoroo Lite”は開いていません」「開発元を検証できません」と出たら、**「完了」**（または「キャンセル」）を押します。
3. **システム設定 → プライバシーとセキュリティ** を開き、下の方の「“Echoroo Lite”は使用をブロックされました」の横にある **「このまま開く」** を押します。
   パスワード（または Touch ID）を求められたら入力し、もう一度「開く」を押します。
4. 2回目からは普通に開けます。

「壊れているため開けません」と出る場合は、ダウンロードが途中で壊れています。もう一度ダウンロードしてください。

## Linux（64ビット、Ubuntu 22.04 以降など）

### .deb（Ubuntu・Debian 系）

```bash
sudo apt install ./echoroo-lite_0.9.0_amd64.deb
```

音声の再生に必要な GStreamer のプラグインも一緒に入ります。

### AppImage（その他の配布物）

```bash
chmod +x Echoroo.Lite_0.9.0_amd64.AppImage
./Echoroo.Lite_0.9.0_amd64.AppImage
```

音が出ない場合は、GStreamer のプラグインを入れてください（Ubuntu の例）。

```bash
sudo apt install gstreamer1.0-plugins-good gstreamer1.0-libav
```

## はじめて使うとき

- 録音した場所を地図で選び、音声のフォルダを指定すると、録音が追加されます。そのあと「録音を解析する」を押します。
- 解析にはパソコンの性能に応じて時間がかかります（目安：12時間分の録音で数十分）。
- 元の音声ファイルは移動・変更されません。

## データの保存場所とアンインストール

| OS | 保存場所 |
|---|---|
| Windows | `%APPDATA%\org.echoroo.lite` |
| macOS | `~/Library/Application Support/org.echoroo.lite` |
| Linux | `~/.local/share/org.echoroo.lite` |

アプリを削除しても、このフォルダは残ります。完全に消すときは、このフォルダも削除してください。

---

# Installation (Echoroo Lite 0.9.0)

## Terms of use

There is **one Echoroo Lite edition**, containing BirdNET 2.4 and Perch V2. It is
for **non-commercial / research use** because the BirdNET 2.4 models are
CC BY-NC-SA 4.0. No bird-call recordings are bundled in the app. Add clips from
your own recordings or Xeno-Canto with your own API key to use the quiz.

The app is not code-signed, so the operating system warns on first launch. Open it as follows.

## Windows 10 / 11 (64-bit)

1. Download and open `Echoroo Lite_0.9.0_x64-setup.exe`.
2. If "Windows protected your PC" appears, click **More info**, then **Run anyway**.
3. Follow the installer. Microsoft Edge WebView2 is installed if missing (internet required).

## macOS 12 or later (Apple silicon)

Intel Macs are not supported.

1. Open `Echoroo Lite_0.9.0_aarch64.dmg` and drag Echoroo Lite to Applications.
2. Open it from Applications. If macOS says it cannot verify the developer, click **Done** (or Cancel).
3. Open **System Settings → Privacy & Security**, find "Echoroo Lite was blocked", click **Open Anyway**, authenticate, and click **Open**.
4. Later launches open normally.

If macOS says the app "is damaged", the download was corrupted; download it again.

## Linux (64-bit, e.g. Ubuntu 22.04 or later)

- `.deb`: `sudo apt install ./echoroo-lite_0.9.0_amd64.deb` (installs the GStreamer plugins needed for audio).
- AppImage: `chmod +x` the file and run it. If there is no sound, install `gstreamer1.0-plugins-good` and `gstreamer1.0-libav`.

## Data and uninstalling

Data stays in `org.echoroo.lite` under the app-data folder
(Windows `%APPDATA%`, macOS `~/Library/Application Support`, Linux `~/.local/share`). Delete that folder to remove all data.
