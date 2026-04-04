# リベシティ つぶやきオフ会収集システム

リベシティのつぶやきから「カレンダー未掲載のオフ会情報」のみを自動収集し、PDF/PNGで出力するシステムです。

---

## ユーザーからの依頼パターンと実行手順

ユーザーが以下のような指示を出したら、**下記の4ステップを順番に実行すること**。
Chromeは開いたままで動く。ユーザーは何もしなくてよい。

### 対象となる依頼の例
- 「つぶやきからカレンダー未掲載のClaudeオフ会を収集してPDFとPNGを作って」
- 「つぶやきのオフ会一覧を作って」
- 「tweet_offkai を実行して」
- 「オフ会収集して」

---

## 実行手順（Claude が自動でやること）

### Step 1: 日付範囲を決定する

```python
from datetime import datetime, timedelta
now = datetime.now()
start_iso = now.strftime("%Y-%m-%dT00:00:00Z")
end_iso   = (now + timedelta(days=31)).strftime("%Y-%m-%dT23:59:59Z")
start_str = now.strftime("%Y/%m/%d")
end_str   = (now + timedelta(days=31)).strftime("%Y/%m/%d")
```

ユーザーが期間を指定した場合はその期間を使う。

---

### Step 2: Firestoreクエリ用のJSを生成する

以下のBashコマンドで `/tmp/tweet_query.js` を生成する：

```bash
python3 -c "
import sys, json
sys.path.insert(0, '/Users/hiro/Documents/libecity-offkai')
from tweet_offkai import make_js
js = make_js('START_ISO', 'END_ISO', 'claude')
open('/tmp/tweet_query.js', 'w').write(js)
print('JS生成完了')
"
```

`START_ISO` と `END_ISO` は Step 1 の実際の値に置換すること。

---

### Step 3: MCP Chrome でJSを実行してデータを取得する

**この方法ならChromeを閉じる必要がない（ログイン状態をそのまま利用する）**

#### 3-1: libecity.com を開く

`mcp__Claude_in_Chrome__navigate` ツールで以下のURLを開く：
```
https://libecity.com/mypage/tsubuyaki
```

#### 3-2: 3秒待つ（ページ読み込み完了のため）

`mcp__Claude_in_Chrome__javascript_tool` で以下を実行：
```javascript
await new Promise(r => setTimeout(r, 3000)); return "待機完了";
```

#### 3-3: Firestoreクエリを実行する

`mcp__Claude_in_Chrome__javascript_tool` で `/tmp/tweet_query.js` の内容をそのまま実行する。
結果（JSONオブジェクト）が返ってくる。

#### 3-4: 結果を `/tmp/tweet_offkai_data.json` に保存する

返ってきた結果をPythonで `/tmp/tweet_offkai_data.json` に書き出す：

```python
import json
result = <Step 3-3 の返り値>
with open('/tmp/tweet_offkai_data.json', 'w', encoding='utf-8') as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
```

---

### Step 4: Pythonスクリプトで PDF / PNG を生成する

```bash
python3 /Users/hiro/Documents/libecity-offkai/tweet_offkai.py \
  --from-json /tmp/tweet_offkai_data.json \
  START_DATE END_DATE
```

`START_DATE`, `END_DATE` は `YYYY-MM-DD` 形式（例: `2026-04-05 2026-05-05`）。

---

### Step 5: 完了をユーザーに報告する

生成されたファイルのパスをユーザーに伝える：
- `~/Documents/libecity-offkai/YYMMDD_claude_つぶやきオフ会（カレンダー未掲載）.pdf`
- `~/Documents/libecity-offkai/YYMMDD_claude_つぶやきオフ会（カレンダー未掲載）.png`

---

## エラー時の対処

### 「Firebase認証トークンが取得できませんでした」
→ Chromeでリベシティ（https://libecity.com）にログインしていない。
→ ユーザーに「Chromeでリベシティにログインしてください」と伝えて再実行。

### JSの実行結果が `{error: ...}` になった
→ エラーメッセージを確認し、上記に従い対処。

### Step 3 で MCP Chrome が使えない場合のフォールバック
直接スクリプトを実行する（Chromeが起動中でも自動でプロファイルコピーを試みる）：
```bash
python3 /Users/hiro/Documents/libecity-offkai/tweet_offkai.py START_DATE END_DATE
```

---

## 事前準備（初回のみ・ユーザーがやること）

```bash
pip install playwright openpyxl
playwright install chromium
```

---

## 技術メモ

### Firestoreコレクション: `tweets`

| フィールド | 型 | 内容 |
|---|---|---|
| `contents` | string | つぶやき本文（キーワード検索対象） |
| `name` | string | 投稿者名 |
| `uid` | string | 投稿者UID |
| `created_at` | timestamp | 投稿日時 |
| `parent_path` | string/null | nullならトップレベル（リプライ除外に使用） |

- Firebase プロジェクトID: `production-b8884`
- 認証トークン: IndexedDB `firebaseLocalStorageDb` → `stsTokenManager.accessToken`

### オフ会キーワード（いずれか1つを含む投稿のみ対象）

```
オフ会, ミートアップ, meetup, 勉強会, ワークショップ, workshop,
開催, 会場, 現地, 日時, 募集, 申込, 申し込み, 定員,
参加費, 参加無料, 無料参加, 参加者募集, 参加受付,
オフライン, zoom, ovice, discord, google meet,
集合, 場所, 開場, 開始時間, 締切, 先着, 交流会, 懇親会, もくもく会
```

### つぶやきURL形式

```
https://libecity.com/mypage/tsubuyaki?id={docId}
```

`docId` は Firestore ドキュメント名の末尾セグメント（`item.document.name.split('/').pop()`）

### tweet_offkai.py の使い方

```bash
# 通常実行（Chromeプロファイルから自動取得）
python3 tweet_offkai.py [start_YYYY-MM-DD] [end_YYYY-MM-DD] [keyword]

# 事前取得済みJSONを使う（Claude Code 推奨モード）
python3 tweet_offkai.py --from-json /tmp/tweet_offkai_data.json [start] [end]
```
