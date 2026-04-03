# リベシティ オフ会リスト自動生成システム

Claude Coworkユーザー向けのリベシティオフ会収集・一覧化ツールです。
**カレンダー**と**つぶやき**の両方からClaudeキーワードのオフ会を自動収集し、PDF/PNG/Excelで出力します。

## できること

- リベシティのイベントカレンダーからオフ会を自動収集（オフライン・オンライン両対応）
- リベシティのつぶやきから「claude」を含む投稿を自動収集（カレンダー未掲載のイベントも拾える）
- 主催者名をFirestoreから自動取得
- カレンダーとつぶやきの結果をマージして重複排除
- PDF・PNG・Excelファイルを自動生成（フィルター機能付き）

## 事前準備（初回のみ）

```bash
pip install playwright openpyxl
playwright install chromium
```

## 使い方

### ステップ1：Chromeでリベシティにログインする

ブラウザでリベシティにログインした状態にしておいてください。

### ステップ2：Claudeに依頼する

**カレンダー＋つぶやき両方を収集する場合（推奨）：**
```
リベシティのカレンダーとつぶやきから今日から1ヶ月のClaudeオフ会を収集して
```

**カレンダーのみ：**
```
リベシティのカレンダーから今日から1ヶ月のClaudeオフ会を収集して
```

**つぶやきのみを追加収集する場合（既存 events.json にマージ）：**
```bash
python3 collect_tweets.py 2026-04-01 2026-05-10 claude
```

### ステップ3：出力ファイルを確認する

`~/Documents/libecity-offkai/` フォルダに以下が生成されます：
- `YYMMDD_claude_libecityオフ会リスト.pdf`
- `YYMMDD_claude_libecityオフ会リスト.png`
- `YYMMDD_claude_libecityオフ会リスト.xlsx`

---

## 技術メモ（Claudeが作業するときの参考情報）

### 収集対象

| ソース | URL | 収集方法 |
|--------|-----|----------|
| カレンダー | `https://libecity.com/mypage/event_calendar` | Playwright DOM スクレイピング |
| つぶやき | `https://libecity.com/mypage/tsubuyaki` | Firestore REST API (`tweets` コレクション) |

- キーワード: タイトル/本文に「claude」（大文字小文字問わず）を含むもの
- カレンダー: オフラインイベント・オンラインイベントの**両方**を収集すること

### ─── カレンダー ───

#### DOM構造
```
.calendar_popover
  .popover-header        → 日付（例: "04/03(金)✕"）
  .popover-body
    .event_list
      li.event_item#meet_up_ROOMID  → RoomIDはid属性から取得
        .event_time      → "10:00〜12:00 募集中宮城オフィス"
        .event_title     → タイトル文字列
```

### ─── つぶやき ───

#### Firestore コレクション: `tweets`

主要フィールド:
```
contents    : string  ← つぶやき本文（キーワード検索対象）
name        : string  ← 投稿者名
uid         : string  ← 投稿者UID
created_at  : timestamp
parent_path : string|null  ← nullならトップレベル投稿（リプライ除外に使用）
type_id     : string  ← "normal"など
```

#### クエリ方法（Firestore REST API）
```javascript
POST https://firestore.googleapis.com/v1/projects/production-b8884/databases/(default)/documents:runQuery

// 日付範囲フィルタ → クライアント側で contents に "claude" を含むか確認
// parent_path が null/空のもの（リプライ除外）
// limit: 2000
```

#### つぶやきを収集するスクリプト
```bash
python3 collect_tweets.py [start_YYYY-MM-DD] [end_YYYY-MM-DD] [keyword]
```
- 既存の events.json があれば自動マージ・重複排除する

### ─── 主催者名の取得（カレンダーのみ）───

- Firebase プロジェクトID: `production-b8884`
- Firestore REST API: `https://firestore.googleapis.com/v1/projects/production-b8884/databases/(default)/documents`
- 認証トークン: IndexedDB `firebaseLocalStorageDb` → `stsTokenManager.accessToken`
- `rooms/{roomId}` → `author` (UID) → `users/{uid}` → `name`

### ─── events.json のフォーマット ───
```json
[
  {
    "date": "04/03(金)",
    "time": "10:00〜12:00",
    "title": "Claude Coworkを導入しようオフ会",
    "status": "募集中",
    "place": "オンライン",
    "roomId": "...",
    "format": "オンライン",
    "url": "https://libecity.com/room_list?room_id=...",
    "organizer": "主催者名",
    "source": "カレンダー"
  },
  {
    "date": "04/03(金)",
    "time": "20:15",
    "title": "Claude Codeの会でした！学びが多かった",
    "status": "—",
    "place": "—",
    "format": "オンライン",
    "url": "https://libecity.com/mypage/tsubuyaki?id=XXXXXXXX",
    "organizer": "投稿者名",
    "source": "つぶやき",
    "tweetBody": "今日はOviceでClaude Codeの会でした！..."
  }
]
```

### ─── 出力ファイル生成 ───
```bash
python3 generate_output.py events.json
```
