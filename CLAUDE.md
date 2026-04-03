# リベシティ オフ会リスト自動生成システム

Claude Coworkユーザー向けのリベシティオフ会収集・一覧化ツールです。
カレンダーからClaudeキーワードのオフ会を自動収集し、PDF/PNG/Excelで出力します。

## できること

- リベシティのイベントカレンダーからオフ会を自動収集（オフライン・オンライン両対応）
- 主催者名をFirestoreから自動取得
- PDF・PNG・Excelファイルを自動生成（フィルター機能付き）

## 事前準備（初回のみ）

```bash
pip install playwright openpyxl
playwright install chromium
```

## 使い方

### ステップ1：Chromeでリベシティにログインする

ブラウザでリベシティにログインした状態にしておいてください。
Claude Code（この会話）がブラウザを操作してデータを収集します。

### ステップ2：Claudeに依頼する

以下のように話しかけるだけです：

```
リベシティのカレンダーから今日から1ヶ月のClaudeオフ会を収集して
```

### ステップ3：出力ファイルを確認する

`~/Documents/libecity-offkai/` フォルダに以下が生成されます：
- `YYMMDD_claude_libecityオフ会リスト.pdf`
- `YYMMDD_claude_libecityオフ会リスト.png`
- `YYMMDD_claude_libecityオフ会リスト.xlsx`

## 技術メモ（Claudeが作業するときの参考情報）

### 収集対象
- URL: `https://libecity.com/mypage/event_calendar`
- キーワード: タイトルに「claude」（大文字小文字問わず）を含むもの
- タブ: オフラインイベント・オンラインイベントの**両方**を収集すること

### カレンダーのDOM構造
```
.calendar_popover
  .popover-header        → 日付（例: "04/03(金)✕"）
  .popover-body
    .event_list
      li.event_item#meet_up_ROOMID  → RoomIDはid属性から取得
        .event_time      → "10:00〜12:00 募集中宮城オフィス"
        .event_title     → タイトル文字列
```

### 主催者名の取得
- Firebase プロジェクトID: `production-b8884`
- Firestore REST API: `https://firestore.googleapis.com/v1/projects/production-b8884/databases/(default)/documents`
- 認証トークン: IndexedDB `firebaseLocalStorageDb` → `stsTokenManager.accessToken`
- `rooms/{roomId}` → `author` (UID) → `users/{uid}` → `name`

### events.json のフォーマット
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
  }
]
```

### 出力ファイル生成
```bash
python3 generate_output.py events.json
```
