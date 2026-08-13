# とてとてtracker — プロジェクト引き継ぎメモ

日本語の抽選販売・先着販売・受注販売トラッカーアプリ。コレクター向け商品（ポケモンカード／遊戯王カード／ワンピースカード／ドラゴンボールカード／ベイブレード／腕時計／Nike／一番くじ／ちいかわ／LivePocket店舗抽選）の抽選・先着・受注販売情報を一覧化し、抽選価格と転売相場を比較できるようにするツール。

GitHub: https://github.com/totetoteplus/totetote-tracker
公開ページ: https://totetoteplus.github.io/totetote-tracker/

## ファイル構成（2ファイル構成に注意）

- `profit_lottery_tracker.html` — オリジナル版。作者本人が使う用。X（Twitter）投稿用文章の生成ボタン（📝𝕏文章ボタン、`.tweet-panel`、`buildTweetText`/`buildBackorderTweetText`/`hookLine`/`typeTag`/`toggleTweet`/`copyTweet`/`postToX`関連のCSS・JS）を含むフル機能版。広告は設置しない。
- `index.html` — GitHub Pagesで一般公開している版。X投稿機能を意図的に除去し、代わりにA8.netアフィリエイト広告バナーを設置した公開用バージョン。**ITEMS配列・CATEGORIES配列などデータ内容は必ずprofit_lottery_tracker.htmlと同一にする**（UI機能面だけが異なる）。

2つのファイルは単一の自己完結型HTML（インラインCSS/JS、外部ビルドステップなし）。`ITEMS`/`CATEGORIES`配列がハードコードされたデータソース。

## データ設計・運用ルール（両ファイル冒頭の`<script>`内コメントにも記載）

- `dataStatus`: `"confirmed"`（実際の転売相場データあり）／`"reference"`（過去シリーズ実績・不確実な情報源）／`"insufficient"`（相場データなし）
- `status`/`statusText`: `"open"`（受付中）／`"soon"`（受付前）／`"closed"`（受付終了）／`"unknown"`（要確認・不定期）の4状態
- `saleType`: `"lottery"`（抽選販売）／`"firstcome"`（先着販売・くじ販売）／`"backorder"`（受注販売）の3分類
- `periodStart`/`deadline`/`closedOn`: いずれもISO形式、判明分のみ。`closedOn`から`STALE_DAYS=7`（1週間）経過した`closed`項目は`isStale()`により一覧から自動非表示（無理に削除する必要はない）
- `sortRank()`: 1=受付中(締切近い順) / 2=受付中あり / 3=受付前(開始日近い順) / 4=受付終了 / 5=要確認
- カテゴリは`CATEGORIES`配列で管理。アイコンは**色付き丸絵文字のみ**という運用ルール。現在10カテゴリ：pokemon🔴, yugioh🟣, onepiece🟡, dragonball🟠, beyblade🔵, watch⚫, nike⚪, ichibankuji🟢, chiikawa🟤, livepocket🔘

## 最重要ルール：捏造厳禁

- 価格・URL・日付など、実在するソース（公式サイト、価格比較サイト、相場サイト等）で裏付けが取れない情報は絶対に創作・推測しない。不明な場合は該当フィールドを`null`にするか「不明」「情報不足」と明記する。
- 日付データを扱う際は必ず西暦を再確認する。同じ商品名でも年をまたいで実施される催事があるため、応募期間に記載の年が調査基準日と同じ年であることを必ず確認する。
- まとめサイトへの一般的なリンクではなく、可能な限り具体的な公式応募ページのURLを優先する。
- `category:"onepiece"`と`category:"dragonball"`の情報収集時は、プレミアムバンダイ（p-bandai.jp）の抽選販売も必ず検索対象に含める（過去に独自枠の見落としがあったため恒久ルール化。詳細は memory: `lottery-tracker-research-sources.md`）。

## データ更新後の検証手順

1. `<script>`タグ内のJSを抽出し`node --check`で構文エラーがないか確認（PowerShellで抽出する際は`-Encoding UTF8`必須。日本語の文字化けで偽陽性のシンタックスエラーが出るのを防ぐため）
2. `id:"..."`の重複がないか確認
3. `profit_lottery_tracker.html`と`index.html`両方で確認する
4. 問題なければ`git add -A`、`git commit`、`git push origin main`

PowerShellツール呼び出しは呼び出しごとに独立プロセスの場合があり、PATH変更が引き継がれないことがある。`git`/`node`等を使う全PowerShellブロックの先頭で以下を実行する：
```powershell
$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
```

## 自動化：クラウドルーチン

`RemoteTrigger`（claude.aiの「routines」機能）で毎日6:00 JST（cron `0 21 * * *` UTC）に自動リサーチ・更新・pushを実行するよう設定済み。

- routine ID: `trig_01MEMmJo6LoHH7H3LAjGNY9o`
- 管理画面: https://claude.ai/code/routines/trig_01MEMmJo6LoHH7H3LAjGNY9o
- 環境: `env_0158ZnbmCHhUupzJQMREh8Yw`、モデル: `claude-sonnet-5`
- プロンプト内容：両ファイルの二重構造・データ捏造厳禁ルール・作業手順（研究→両ファイル更新→構文チェック→push）を含む。**index.htmlの広告バナー(`.ad-section`)とアフィリエイト表記は変更・削除禁止と明記済み**

### 2026-08-13朝のpush 403問題は解消済み

2026年8月13日朝の自動実行では、GitHub Appの権限不足によりGitHubへのpushが403エラーで失敗する問題が発生していた（詳細はコミット履歴・過去のセッション記録を参照）。同日昼のクラウドルーチン実行（本セッション）で`git push --dry-run`による権限確認および実際のpush（コミット`027d56f`）が成功したため、**GitHub App側の書き込み権限は復旧済み**と判断できる。以後の自動実行でpushが再び403で失敗するようであれば、https://github.com/settings/installations でtotetoteplusアカウントのClaude GitHub Appの`totetote-tracker`リポジトリへの権限（`Contents: Read and write`）を再確認すること。

なお8/13朝分に埋め合わせが必要だったデータ更新（pokemon/yugioh, onepiece/dragonball, beyblade/watch/nike, ichibankuji/chiikawa, livepocketの5グループ）は本セッションで完了しコミット・push済み。

## ローカルフォールバック自動化（現在無効化・保険として温存）

クラウドルーチンが機能しない場合の代替として、Windows Task Scheduler + Claude Code CLIによるローカル自動化一式を構築済みだが、現在は無効化（disabled）状態。

- `run_daily_update.ps1` — タスクスケジューラから呼ばれるラッパースクリプト
- `.claude/daily_update_prompt.txt` — ローカル実行用のリサーチ・更新プロンプト（クラウドルーチンと同内容を反映済み、広告バナー保護の注意書きも追加済み）
- `.claude/logs/` — 実行ログ出力先
- 再有効化する場合は`Enable-ScheduledTask`でタスクを有効化する

## 収益化（アフィリエイト広告）

`index.html`にのみ、A8.net経由のあみあみ（AmiAmi）アフィリエイトバナーを設置（`.ad-section`ブロック、商品一覧下部、PRラベル付き）。`.notice`欄末尾に「当サイトはアフィリエイト広告を利用しています。」の表記あり。

- ASP: A8.net、提携先: あみあみ（承認済み）
- 現在のバナーリンク: `a8mat=4BA419+3FU6LU+4AHY+5ZEMP`（1回リンク切れがあり差し替え済み。動作確認はユーザー自身が目視で行う運用。自動化されたクリック確認は行わない — ASP規約上の不正クリックとみなされるリスクがあるため）
- 今後の検討事項（未実装・ユーザーへの提案段階）: バナーは下部で視認性が低いため、「抽選ページを開く」リンクの横に個別配置する案を提案中。ただしあみあみはフィギュア・プラモデル中心の店舗のため、トレカ系カテゴリとの関連性は薄い。まずは一番くじ・ちいかわカテゴリのチケットへの個別設置から試すことで合意しかけていた（未実装）。
- 「必ず1クリックさせる」ような強制的な広告誘導（不正クリック・ダークパターン）は実装しない方針（ASP規約違反・アカウント停止リスクのため）。

## GitHubアカウントについて

意図的にユーザーの他アカウントとは別の**totetoteplus**アカウントで運用（ユーザーの希望により分離）。ローカルの`gh auth login`もこのアカウントで認証済み。
