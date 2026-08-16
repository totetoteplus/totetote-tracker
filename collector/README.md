# とてとてtracker 収集システム（collector/）

Manus等のAIエージェントを常時稼働させず、通常のPythonプログラムで低コスト・
安定的に抽選/入荷情報を収集し、Supabaseへ保存するバッチシステム。

このディレクトリはリポジトリ内の他の資産（ルート直下の静的HTML版、
`totetote-tracker-manus-source-for-claude-code-2026-08-15 (1)/` のManus版）
とは完全に独立している。GitHub Pagesは`index.html`のみを参照するため、
このディレクトリの追加は既存の公開サイトに影響しない。

## 進行フェーズ

| Phase | 内容 | 状態 |
|---|---|---|
| 1 | プロジェクト構成とDB設計 | 完了 |
| 2 | 1サイトだけCollectorを作成（タカラトミー公式RSS） | 未着手 |
| 3 | Supabaseへの保存を実装 | 未着手 |
| 4 | 差分検知を実装 | 未着手 |
| 5 | スケジューラーを実装 | 未着手 |
| 6 | ログ・エラー処理を実装 | 未着手 |
| 7 | 2〜3サイトに拡張 | 未着手 |
| 8 | サイトごとの取得頻度を最適化 | 未着手 |

## セットアップ手順

### 1. Pythonのインストール（このマシンには未導入）

[python.org](https://www.python.org/downloads/) から Python 3.12 系のインストーラーを
ダウンロードして実行する。インストーラー画面で **「Add python.exe to PATH」に必ずチェック**
を入れること。インストール後、新しいPowerShellを開いて確認する。

```powershell
python --version
```

### 2. 仮想環境の作成と依存インストール

```powershell
cd collector
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium   # ブラウザ自動操作を使うCollector向け（Phase2時点では不要）
```

### 3. Supabaseプロジェクトの作成

1. https://supabase.com にログイン（未登録ならアカウント作成）
2. 「New project」でプロジェクトを作成（リージョンは`Northeast Asia (Tokyo)`推奨）
3. 作成後、左メニュー「SQL Editor」を開き、`collector/db/schema.sql` の内容を貼り付けて実行
4. 左メニュー「Project Settings > API」から以下を控える
   - `Project URL`
   - `service_role` キー（**anon keyではない**。ブラウザに公開しないこと）

### 4. 環境変数の設定

```powershell
cd collector
copy .env.example .env
```

`.env` を開き、`SUPABASE_URL` と `SUPABASE_SERVICE_ROLE_KEY` に手順3で控えた値を設定する。
`.env` は `.gitignore` 済みでコミットされない。

## ディレクトリ構成

```
collector/
├─ collectors/       # サイトごとのCollector（BaseCollectorを継承）
├─ core/              # 共通処理: models / db / dedupe / diff / ai_assist / errors / logging
├─ db/schema.sql       # Supabase (PostgreSQL) DDL
├─ scheduler/          # 頻度設定(config.yaml)と実行エントリポイント(run_due.py, Phase5)
├─ logs/                # Collector実行ログ（.gitignore対象）
├─ requirements.txt
└─ .env.example
```

## 設計メモ

- 取得方式の優先順位: 公式API > RSS > 静的HTML取得 > requests(HTTP) > Playwright
- 重複排除: JANコード優先、なければ商品名等から類似度判定。閾値未満は
  `product_match_candidates` に候補として保存し自動統合しない
- 変化がなければDB更新を行わない（`source_pages.content_hash`で早期判定）
- AI利用は`core/ai_assist.py`に隔離し、通常のCollector実行フローには影響しない
- 各サイトの利用規約・robots.txtを事前確認し、許可されない自動取得は行わない
  （例: ポケモンセンターオンラインは利用規約で自動取得を明示的に禁止しているため対象外）
