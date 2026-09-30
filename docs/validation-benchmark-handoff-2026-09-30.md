# 依頼：AIの数え方を確かめる方法の比較実験（2026-09-30）

この文書だけで作業できるように書いています（Echoroo Lite のリポジトリを取得する必要はありません）。
必要な方式の定義とコードはすべて本文に含めました。作業するリポジトリ・場所は、このマシンの都合に合わせてください。

## 1. 目的

鳥の声を自動判定するデスクトップアプリ「Echoroo Lite」で、利用者が **種ごと・モデルごとに少数の音（40〜80音）を聞くだけ** で、
AI の次の値を正直な範囲つきで推定する方法を選びたい。

- 正しさ（precision）：AI が「いる」とした区間のうち、本当に声があった割合
- 見つけた割合（recall）：声がある区間のうち、AI が「いる」とした割合
- 声を含む区間の割合（call density）と、その区間数

候補の方式を、**全区間に正解が付いたデータ** で比較し、採用する方式の根拠を作ってください。

利用者は企業・自治体の担当者（専門家ではない）。種ごとに「いつ・どれくらい鳴いていたか」を、
年や種の間で比べることがある。そのため、見落としの程度も評価したい。

## 2. 背景

- 現行の方式（後述の A）では、ある実データ（カケス、BirdNET、15秒区間 3,840個）で次のようになった。
  - 基準以上：5区間すべて正解。
  - 基準未満：30音を聞き、1音だけ「聞こえる」。
  - 見つけた割合：約4%（95%区間 0.7〜71%）。直感的でなく、幅も広すぎる。
  - その1音は、迷った末に「聞こえる」とした音。推定がこの1回答に強く依存していた。
- アプリ側の方針：確かめる区間は各モデルの入力の長さにする（BirdNET 3秒、Perch 5秒）。
  precision と recall を別々に測るより、スコア全体にわたる関係を一度に推定するほうがよい可能性がある。
  ただし、時間帯・月ごとの図を描くには、何らかの基準（閾値）が要る。
- 主要文献：Navine AK, Denton T, Weldy MJ, Hart PJ (2024) "All thresholds barred: direct estimation of call
  density in bioacoustic data", Frontiers in Bird Science 3:1380636（doi:10.3389/fbirs.2024.1380636）。
  実装は google-research/perch の `chirp/inference/call_density.py`（Apache-2.0、公開）。
- 事前の検討：後述 D の案が出ている。ただしその評価は、カケス1例と、その当てはめから作った模擬データによるもの。
  評価が循環しているため、根拠にはならない。また、D の「床」の事前分布（中央値1.8%）は、
  データのない最下層に多数の声を仮定してしまう恐れがある。**今回の比較で、公平に確かめ直してほしい。**

## 3. データ

- **Powdermill**：Chronister LM ほか (2021) "An annotated set of audio recordings of Eastern North American birds
  containing frequency, time, and species information", Ecology 102(6):e03329（doi:10.1002/ecy.3329）。
  6.41時間、48種、全区間に専門家の正解。Navine らも使用。
- **霞ヶ浦2026**：このマシンにある、日本の全区間正解付きデータ。
- **窓ごとの正解**：窓と正解の時間範囲が少しでも重なれば「声あり」とする（Navine と同じ）。
  感度分析として「0.5秒以上重なる」も試す。

## 4. モデルとスコア（アプリと同じ条件にする）

- **BirdNET 2.4**
  - Python パッケージ `birdnet==1.1.1`。モデルは Zenodo record 15050749。
    FP32 の音響モデルの SHA-256 は `55f3e4055b1a13bfa9a2452731d0d34f6a02d6b775a334362665892794165e4c`。
  - `birdnet.load("acoustic", "2.4", "tf", library="litert")` で読み込む。
  - 3秒窓。`predict_session(top_k=None, default_confidence_threshold=0)` でシグモイドのスコアを全種取得する。
- **Perch V2（ONNX）**
  - モデル：`https://huggingface.co/justinchuby/Perch-onnx/resolve/da88bf8c37c0b3068fc706fe509f633b9fbd28c1/perch_v2_no_dft.onnx`
    （SHA-256 `4dcf71c18a147198545944bb5149697e89e3ad2e16637fa8f0edf6d13035a017`）
  - ラベル：`https://huggingface.co/cgeorgiaw/Perch/resolve/c3c3c816976a6c1bb75140b8fbaef7397a6f708f/assets/labels.csv`
    （SHA-256 `e4d5c0397d8fb08bf90c6b13a34810af53504faad927e472fcc567793c9de057`）
  - 入力：32 kHz のモノラル（`scipy.signal.resample_poly` で変換）。5秒＝160,000サンプルの窓を重ねずに並べ、最後の窓は0で埋める。
  - 出力 `label`（ロジット）を全クラスで softmax したものが、アプリのスコア。ロジットのままでも試す。
- 種名は学名で照合する。分類体系の違い（例：*Accipiter gentilis* → *Astur gentilis*）は、手作業で対応表を作ってよい。

## 5. 比較する方式

いずれも、1つの種・1つのモデルについて、利用者が合計 n 音（40・60・80）を聞く想定。
回答は「聞こえる／聞こえない／分からない」の3つ。「分からない」は否定として扱わない。
記号：各窓のスコアを s、logit(s) を x とする。スコアは [1e-9, 1-1e-9] に丸めてから logit を取る。

### A 現行の方式
- 録音を現地の日ごとに2つに分ける（「基準を決める日」と「確かめる日」。1日しかない場合は録音ごと）。
- **① 基準を決める（20音）**：基準を決める日の窓の x の範囲を20等分し、各区切りから1音をランダムに選ぶ（空の区切りの分はほかから補う）。
  回答に Firth 補正つきロジスティック回帰 P(声|x) を当てはめる。「その基準以上の窓について、推定した確率の平均が0.90以上」
  になる最も低い基準を選ぶ。全部「聞こえる」なら、最も低いスコアを基準にする。「聞こえる」がない、または傾きが0以下なら、決められない。
- **② 正しさ**：確かめる日の基準以上の窓から、ランダムに30音（少なければ全部）。
- **③ 見落とし**：確かめる日の基準未満の窓から、ランダムに30音。
- **推定**：
  - 正例の数の区間は、有限母集団の超幾何分布を反転した正確区間で出す。「分からない」は、下限では否、上限では正として扱う。
  - precision = 正/全。合格は、片側95%の下限が0.80以上。
  - recall = TP/(TP+FN)。TP = 基準以上の数 × ②の正の割合、FN = 基準未満の数 × ③の正の割合。
- 聞く数の条件（40〜80）に合わせて、②③の数を調整してよい。

### B Perch の方式（論文どおり）
- 全窓を x の分位で4ビンに分ける：0–50–75–87.5–100%。各ビンに入る窓の割合 w_b は、分け方から正確に決まる。
- 各ビンから n/4 音をランダムに選ぶ。
- P(+|b) ~ Beta(正+0.1, 否+0.1)。call density = Σ_b w_b·P(+|b)。1万回の抽出で90%区間を出す。
- ROC-AUC は、ビン分解で出す（下記 perch 実装を参照）。
- 基準 t がビン境界にあれば、次の式で precision と recall を出せる。
  - precision(t) = Σ_{b≥t} w_b P(+|b) / Σ_{b≥t} w_b
  - recall(t) = Σ_{b≥t} w_b P(+|b) / Σ_b w_b P(+|b)
- 公式実装：https://github.com/google-research/perch/blob/main/chirp/inference/call_density.py
  （`estimate_call_density`、`estimate_roc_auc`）。

### C Perch の方式（上位を細かく）
- 分位を 0–50–75–87.5–93.75–96.875–100% などに増やす。
  珍しい種で、正例が最上位のビンに集まってしまう問題（論文の 'Ua'u の例）への対策。ビンの数は試して決める。

### D 基準を起点に分けて、曲線で推定する方式
- 基準は A の①と同じく、日を分けて決める。
- 確かめる日の窓を、基準の logit x_t を起点に分ける。基準以上を1層、基準未満を幅2（logit）で4層、最下層は下限なし。
- 基準以上は全件（最大15音）。残りを基準未満の層に、各層の区間数の平方根に比例して配分する。
- 曲線 P(声|x) = c + (1−c)·expit(a + b·(x − x_t))。事前分布は次のとおり。
  - a ~ N(0, 2.5²)
  - b > 0 で、half-N(1.5)
  - logit(c) ~ N(−4, 2²)。中央値1.8%。−6（中央値0.25%）も試す。
- 事後分布を格子で計算し、未回答の窓に声の有無を事後予測で割り当てて、指標を出す。
- 層ごとの Beta による推定と照合し、食い違えばフラグを立てる。
- 参考実装（numpy と scipy だけ）を付録に全文載せた。

### E 1段階（基準を後から選ぶ）
- 日を分けず、①も行わない。B か C の抽出を1回だけ行い、曲線（またはビンごとの Beta）を推定する。
- 「推定した正しさが0.90以上になる最も低い基準」を後から選び、その基準での precision と recall を出す。
- 同じデータで基準を選ぶことによる楽観の大きさを測る。

### F（任意）
- 単調回帰や、ベータ較正（Kull, Silva Filho & Flach 2017, Electron. J. Statist.）などを追加してよい。

## 6. 評価の手順

1. 各データ・各モデルで、窓ごとのスコアと正解を作る。
2. 対象の種は、正例の窓が一定数以上ある種すべて。正例の割合で「よくいる／中程度／珍しい」に分けて集計する。
3. 各方式・各 n（40・60・80、および「60音＋必要なら20音追加」）で、乱数を変えて200〜500回、
   正解から利用者の回答を作って推定する。乱数の種は固定し、再現できるようにする。
4. 回答の揺れを調べる。
   - 境界の音（正解の範囲との重なりが短い窓など）の一定割合を「分からない」にする。
   - 1回答だけを反転させたとき、推定がどれだけ動くか（カケスの例と同じ状況）。
5. 時間帯・月ごとの推定を調べる。論文の Strategy 1（Σ P(+|b)·P_slice(b)）などで時間帯ごとの密度を出し、
   その種がいない時間帯にも値が出てしまう偏りを測る。

## 7. 測る指標

各指標について、90%区間が真の値を含む割合（被覆率）、区間の幅、点推定の誤差（RMSE）を出す。

- call density（声を含む窓の割合）と、その窓の数
- 選んだ基準での precision と recall
- AUPRC と ROC-AUC
- 選んだ基準と、真の値で選んだ場合の基準の差（E の楽観）
- 時間帯ごとの密度の偏り（その種がいない時間帯を含む）
- 1回答の反転に対する安定性
- 計算時間（numpy と scipy だけで、1種1モデルあたり1秒程度に収まるか）

## 8. 答えてほしい問い

1. 40〜80音で、年や種の比較に使える幅の recall を出せる方式はあるか。あるなら、どの条件（正例の割合、モデルの良さ）でか。
2. 基準を決める段階（①）と日の分割は必要か。E（1段階）で十分か。
3. Perch の方式（B/C）に、D のような曲線モデルを足す価値はあるか。床の事前分布の影響はどの程度か。
4. BirdNET（3秒）と Perch（5秒）で、結論は変わるか。
5. 時間帯・月ごとの図は、どの方式・どの表示なら正直に描けるか。
6. 推奨する方式、聞く数、画面で見せる値（大学1年生に分かる言葉で）と、レポートに載せる値。

## 9. 成果物と注意

- 報告書（日本語、マークダウン）。表と図、推奨と根拠、限界を含める。事実（実験で確かめたこと）と推測を分けて書く。
- 実験スクリプト。依存は numpy・scipy・pandas 程度。データのパスは引数で渡し、データそのものは公開リポジトリにコミットしない。
- 文献を引用するときは、書誌を確認する。
- 用語はアプリに合わせる：正しさ（precision）、見つけた割合（recall）、声を含む区間の割合（call density）。

## 10. 参考文献

- Navine AK, Denton T, Weldy MJ, Hart PJ (2024) Front. Bird Sci. 3:1380636.
- Dumoulin V, Denton T ほか (2025) "The Search for Squawk: Agile Modeling in Bioacoustics", arXiv:2505.03071.
- Wood CM, Kahl S (2024) "Guidelines for appropriate use of BirdNET scores and other detector outputs", J. Ornithol. 165:777–782.
- Chronister LM ほか (2021) Ecology 102(6):e03329（Powdermill データ）。
- Kull M, Silva Filho T, Flach P (2017) "Beyond sigmoids", Electron. J. Statist.

## 付録：D の参考実装（sketch_estimate.py、全文）

```python
"""Implementation sketch for the sidecar (numpy/scipy only): grid posterior of a
floor+logistic curve on logit(score), posterior predictive over the finite
evaluation-day population, plus a stratum-wise Beta cross-check.

estimate(scores, sampled_index, verdicts, threshold, strata, seed) -> dict
Runtime: ~0.3 s for N=4000 windows, 80 answers (grid 57x30x36, 1000 draws).
"""
import numpy as np
from scipy.special import expit, logit

GRID_A = np.linspace(-7, 7, 57)          # logit P(call) at the threshold
GRID_B = np.linspace(0.02, 4, 30)        # slope per logit-score unit (monotone: b > 0)
GRID_C = np.linspace(-11, -0.5, 36)      # logit of the floor: calls at very low scores
DRAWS = 1000


def curve(a, b, c, x):
    return c + (1 - c) * expit(a + b * x)


def posterior(x, y):
    """Grid weights for (a, b, c). x: logit score minus threshold logit; y: 1 heard / 0 not heard."""
    A, B, C = (m.ravel() for m in np.meshgrid(GRID_A, GRID_B, expit(GRID_C), indexing='ij'))
    p = np.clip(curve(A[:, None], B[:, None], C[:, None], x[None, :]), 1e-12, 1 - 1e-12)
    log_post = (y * np.log(p) + (1 - y) * np.log1p(-p)).sum(1)
    log_post += -0.5 * (A / 2.5) ** 2 - 0.5 * (B / 1.5) ** 2 - 0.5 * ((logit(C) + 4) / 2) ** 2
    w = np.exp(log_post - log_post.max())
    return A, B, C, w / w.sum()


def summarize(Y, x, xt, order):
    above = x >= xt
    tp, total = Y[:, above].sum(1), Y.sum(1)
    cum = np.cumsum(Y[:, order], 1)
    ap = (cum / np.arange(1, Y.shape[1] + 1) * Y[:, order]).sum(1) / np.maximum(total, 1)

    def iv(v):
        return {'median': float(np.median(v)), 'low90': float(np.quantile(v, .05)), 'high90': float(np.quantile(v, .95))}
    return {'precision': iv(tp / max(int(above.sum()), 1)), 'recall': iv(tp / np.maximum(total, 1)),
            'count': iv(total), 'auprc': iv(ap)}


def estimate(scores, sampled, verdicts, threshold, strata, seed=0):
    """scores: all evaluation-day windows; sampled: indices answered; verdicts: True heard / False not.
    Windows answered 'unknown' are left out of `sampled` (they stay unsampled, never negatives)."""
    rng = np.random.default_rng(seed)
    x = logit(np.clip(np.asarray(scores, float), 1e-9, 1 - 1e-9))
    xt = float(logit(threshold))
    y = np.zeros(len(x), bool)
    y[sampled] = verdicts
    order = np.argsort(-x)
    # model-based
    A, B, C, w = posterior(x[sampled] - xt, y[sampled].astype(float))
    g = rng.choice(len(w), DRAWS, p=w)
    P = curve(A[g, None], B[g, None], C[g, None], x[None, :] - xt)
    Y = rng.random(P.shape) < P
    Y[:, sampled] = y[sampled]
    model = summarize(Y, x, xt, order)
    # design-based cross-check: one Beta per stratum, Jeffreys prior
    Yd = np.zeros_like(Y)
    for h in np.unique(strata):
        members = np.flatnonzero(strata == h)
        s = members[np.isin(members, sampled)]
        ph = rng.beta(.5 + y[s].sum(), .5 + len(s) - y[s].sum(), DRAWS)
        Yd[:, members] = rng.random((DRAWS, len(members))) < ph[:, None]
    Yd[:, sampled] = y[sampled]
    design = summarize(Yd, x, xt, order)
    disagree = not (design['recall']['low90'] <= model['recall']['median'] <= design['recall']['high90'])
    return {'model': model, 'design': design, 'disagree': disagree}


if __name__ == '__main__':
    rng = np.random.default_rng(1)
    x = rng.normal(-6, 2, 4000)
    truth = rng.random(4000) < curve(1.5, 0.6, 0.02, x)
    strata = np.digitize(x, [-6, -4, -2, 0])
    sampled = np.concatenate([rng.choice(np.flatnonzero(strata == h), min(15, (strata == h).sum()), replace=False) for h in range(5)])
    out = estimate(expit(x), sampled, truth[sampled], 0.5, strata)
    true_recall = truth[x >= 0].sum() / truth.sum()
    assert out['model']['recall']['low90'] <= true_recall <= out['model']['recall']['high90'], out
    print('ok', true_recall, out['model']['recall'], out['disagree'])
```
