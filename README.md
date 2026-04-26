# 経理代行システム MVP

会計事務所の経理代行業務を半自動化するMVP。
領収書 → AI読取 → 仕訳生成 → カード明細突合 → 銀行引落突合 → マネーフォワード登録 までを一気通貫で処理する。

---

## 機能

- 📤 領収書アップロード(画像/PDF)→ Claude Vision でOCR
- 📝 AI読取結果の確認・編集
- 💳 カード利用明細CSVインポート
- 🔗 領収書とカード明細の自動突合(現金 → 未払金へ書換)
- 🏦 銀行明細CSVインポート
- 💸 銀行引落と未払金の突合(取り崩し仕訳生成)
- 📚 全仕訳の台帳・状態フィルタ
- 🏠 ダッシュボードで状況可視化
- 🔑 パスワード認証(共有時のアクセス制限)

---

## ローカル起動

### 初回セットアップ

```bash
cd C:\dev\keiri-daiko

# 仮想環境作成と有効化
python -m venv .venv
.venv\Scripts\activate

# 依存パッケージインストール
pip install -r requirements.txt

# 環境変数ファイル作成
copy .env.example .env
# .env を編集して必要なキーを設定
```

### 起動

```bash
streamlit run app.py
```

ブラウザで `http://localhost:8501` を開く。

---

## デモ共有(Streamlit Community Cloud デプロイ)

友人・パートナーに **公開URL** で触ってもらうための手順。

### 前提
- GitHub アカウント
- Streamlit Community Cloud アカウント(無料、`https://share.streamlit.io/`)

### デプロイ手順

#### 1. GitHub にリポジトリを準備

```bash
cd C:\dev\keiri-daiko
git init
git add .
git status   # ⚠ .env / .streamlit/secrets.toml が含まれていないことを必ず確認
git commit -m "Initial commit: 経理代行システム MVP"
gh repo create keiri-daiko --private --source=. --push
```

#### 2. Streamlit Community Cloud で接続

1. https://share.streamlit.io/ にログイン(GitHubアカウント連携)
2. 「New app」→ リポジトリ `keiri-daiko` 選択
3. ブランチ: `main`、メインファイル: `app.py`
4. 「Advanced settings」→ Secrets タブ
5. `secrets.toml.example` を参考に、以下を貼り付け:
   ```toml
   APP_PASSWORD = "demo2026-xxxxx"
   ```
6. 「Deploy」ボタンクリック

数分後、`https://keiri-daiko-XXXX.streamlit.app/` のような公開URLが発行される。

#### 3. 友人・パートナーに共有

URLとパスワードをDM / メールで送る。SNS公開は厳禁。

```
🔗 URL: https://keiri-daiko-XXXX.streamlit.app/
🔑 パスワード: demo2026-xxxxx
```

#### 4. デモ後の停止(必要なら)

Streamlit Cloud ダッシュボードでアプリを「Pause」または「Delete」。

---

## 🚨 セキュリティ運用ルール(必読)

### 絶対にやってはいけないこと

| ❌ NG | 理由 |
|---|---|
| `.env` をGitにコミット | APIキーが流出する |
| `.streamlit/secrets.toml` をGitにコミット | パスワードが流出する |
| 実クライアントの領収書をデモ環境にアップ | 個人情報が外部サーバーに保存される |
| URL/パスワードをSNS・公開チャットに投稿 | 誰でもアクセスできてしまう |
| パスワード変更しないまま長期運用 | 漏洩リスクが累積する |

### 守るべきこと

- ✅ デモ環境で扱うのは `samples/` 内のダミーデータのみ
- ✅ パスワードは英数字8文字以上、推測困難なものに
- ✅ デモ終了後はStreamlit Cloudでアプリを停止
- ✅ コミット前に必ず `git status` で機密ファイルが含まれていないか確認
- ✅ 万一APIキーを誤ってコミット/push した場合、即座に該当キーを失効・再発行

### `.gitignore` で除外しているもの

```
.env / .env.*
.streamlit/secrets.toml
*.key / *.pem / credentials.json
data/                   ← 仕訳履歴・カード/銀行明細
samples/receipts/*      ← 領収書画像
*.log
```

---

## ディレクトリ構成

```
C:\dev\keiri-daiko\
├── .env                       # ローカル環境変数(コミット禁止)
├── .env.example               # テンプレート(コミットOK)
├── .streamlit/
│   ├── config.toml            # テーマ設定(コミットOK)
│   └── secrets.toml.example   # シークレットテンプレ(コミットOK)
├── .gitignore
├── README.md
├── requirements.txt
├── app.py                     # Streamlit エントリポイント
├── config.yaml                # クライアント・突合設定
├── core/
│   ├── auth.py                # パスワード認証
│   ├── ocr.py                 # Claude Vision OCR
│   ├── journal.py             # 仕訳生成
│   ├── mf_client.py           # マネフォAPI(Mock/Real切替)
│   ├── pipeline.py            # 一気通貫パイプライン
│   ├── storage.py             # JSON永続化
│   ├── card_statement.py      # カード明細CSVインポート
│   ├── bank_statement.py      # 銀行明細CSVインポート
│   └── matcher.py             # 突合エンジン
├── data/                      # ローカルデータ(コミット禁止)
└── samples/                   # ダミーサンプル
    ├── receipts/              # ダミー領収書(コミット禁止)
    ├── card_statements/       # ダミーカード明細
    └── bank_statements/       # ダミー銀行明細
```

---

## 動作モード

| モード | 設定方法 | 挙動 |
|---|---|---|
| OCRスタブ | `OCR_STUB_MODE=1` | APIキー不要、ファイル名から推定したダミーJSON |
| OCR本物 | `OCR_STUB_MODE=0` + `ANTHROPIC_API_KEY` | Claude Visionで画像を実際に読取 |
| マネフォモック | `MF_MODE=mock` | ローカルJSONに保存(マネフォには登録されない) |
| マネフォ実API | `MF_MODE=real` + 認証情報 | マネーフォワードに実際に仕訳登録 |

---

## 開発ステータス

- [x] 環境構築 + 依存パッケージインストール
- [x] OCR モジュール(Claude Vision + スタブモード)
- [x] 仕訳生成エンジン
- [x] マネフォAPIクライアント(Mock/Real切替)
- [x] 一気通貫パイプライン
- [x] Streamlit UI(ダッシュボード + 7タブ)
- [x] カード明細CSVインポート + 領収書突合
- [x] 銀行明細CSVインポート + 引落突合(取り崩し仕訳)
- [x] パスワード認証
- [x] Streamlit Community Cloud デプロイ対応
- [ ] 編集・削除機能
- [ ] 重複検出
- [ ] マネフォCSVエクスポート
- [ ] 領収書プレビュー画像表示
