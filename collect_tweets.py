#!/usr/bin/env python3
"""
リベシティのつぶやきからClaudeオフ会関連の投稿を収集する。
Firestore tweets コレクションを直接クエリして Claude×オフ会 ツイートを抽出。

Usage:
  python3 collect_tweets.py [start_date] [end_date] [keyword]
  例: python3 collect_tweets.py 2026-04-01 2026-05-10 claude

  引数省略時: 今日から1ヶ月先、キーワード=claude
"""

import asyncio
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

OUTPUT_DIR = Path.home() / "Documents" / "libecity-offkai"

# ─────────────────────────────────────────────────────────
# ブラウザ内で実行するJavaScript
# ─────────────────────────────────────────────────────────
def make_js(start_iso: str, end_iso: str, keyword: str) -> str:
    return f"""
(async () => {{
  // ── 1. Firebase認証トークン取得 ───────────────────────
  const db = await new Promise((res, rej) => {{
    const req = indexedDB.open('firebaseLocalStorageDb');
    req.onsuccess = e => res(e.target.result);
    req.onerror  = rej;
  }});
  const allItems = await new Promise((res, rej) => {{
    const items = [];
    const store = db.transaction('firebaseLocalStorage')
                    .objectStore('firebaseLocalStorage');
    store.openCursor().onsuccess = e => {{
      const c = e.target.result;
      if (c) {{ items.push(c.value); c.continue(); }} else res(items);
    }};
  }});
  const token = allItems
    .find(i => i.value?.stsTokenManager?.accessToken)
    ?.value?.stsTokenManager?.accessToken;
  if (!token) return {{ error: 'Firebase認証トークンが取得できませんでした。Chromeでリベシティにログインしてください。' }};

  // ── 2. Firestore クエリ（日付範囲フィルタ） ──────────
  const BASE = 'https://firestore.googleapis.com/v1/projects/production-b8884/databases/(default)/documents';
  const startDate = '{start_iso}';
  const endDate   = '{end_iso}';
  const keyword   = '{keyword}'.toLowerCase();

  const query = {{
    structuredQuery: {{
      from: [{{ collectionId: 'tweets' }}],
      where: {{
        compositeFilter: {{
          op: 'AND',
          filters: [
            {{ fieldFilter: {{
              field: {{ fieldPath: 'created_at' }},
              op: 'GREATER_THAN_OR_EQUAL',
              value: {{ timestampValue: startDate }}
            }} }},
            {{ fieldFilter: {{
              field: {{ fieldPath: 'created_at' }},
              op: 'LESS_THAN_OR_EQUAL',
              value: {{ timestampValue: endDate }}
            }} }}
          ]
        }}
      }},
      orderBy: [{{ field: {{ fieldPath: 'created_at' }}, direction: 'DESCENDING' }}],
      limit: 2000
    }}
  }};

  const resp = await fetch(`${{BASE}}:runQuery`, {{
    method: 'POST',
    headers: {{ Authorization: `Bearer ${{token}}`, 'Content-Type': 'application/json' }},
    body: JSON.stringify(query)
  }});
  if (!resp.ok) return {{ error: `Firestore query failed: ${{resp.status}}` }};
  const rawData = await resp.json();

  // ── 3. クライアントサイドフィルタリング ──────────────
  const DAYS_JA = ['日','月','火','水','木','金','土'];

  const events = [];
  for (const item of rawData) {{
    if (!item.document) continue;
    const f = item.document.fields;
    const contents = (f.contents?.stringValue || '').toLowerCase();

    // キーワードフィルタ（大文字小文字不問）
    if (!contents.includes(keyword)) continue;

    // トップレベルのつぶやきのみ（リプライ除外）
    if (f.parent_path?.stringValue) continue;

    const docId     = item.document.name.split('/').pop();
    const createdAt = new Date(f.created_at?.timestampValue);
    const dow       = DAYS_JA[createdAt.getDay()];
    const month     = String(createdAt.getMonth() + 1).padStart(2, '0');
    const day       = String(createdAt.getDate()).padStart(2, '0');
    const dateStr   = `${{month}}/${{day}}(${{dow}})`;
    const timeStr   = `${{String(createdAt.getHours()).padStart(2,'0')}}:${{String(createdAt.getMinutes()).padStart(2,'0')}}`;

    // 開催形式を本文から推定
    const lower = (f.contents?.stringValue || '').toLowerCase();
    let format = '—';
    if (lower.includes('オンライン') || lower.includes('zoom') ||
        lower.includes('ovice') || lower.includes('meet') ||
        lower.includes('discord') || lower.includes('online')) {{
      format = 'オンライン';
    }} else if (lower.includes('オフライン') || lower.includes('オフ会') ||
               lower.includes('現地') || lower.includes('会場') || lower.includes('会場')) {{
      format = 'オフライン';
    }}

    // タイトル: 1行目（空なら本文先頭50文字）
    const rawContents = f.contents?.stringValue || '';
    const firstLine   = rawContents.split('\\n')[0].trim();
    const title       = (firstLine.length > 4 ? firstLine : rawContents).substring(0, 60);

    events.push({{
      date:      dateStr,
      time:      timeStr,
      title:     title,
      status:    '—',
      place:     '—',
      format:    format,
      url:       `https://libecity.com/mypage/tsubuyaki?id=${{docId}}`,
      organizer: f.name?.stringValue || '—',
      source:    'つぶやき',
      tweetBody: rawContents.substring(0, 300)
    }});
  }}

  return {{
    total_scanned: rawData.filter(i => i.document).length,
    matched:       events.length,
    events
  }};
}})()
"""


async def collect(start_iso: str, end_iso: str, keyword: str = "claude") -> list:
    from playwright.async_api import async_playwright

    print(f"  📡 Firestore tweets クエリ中... ({start_iso[:10]} ～ {end_iso[:10]}, keyword={keyword})")

    # Chrome のプロファイルパス（Mac）
    chrome_profile = Path.home() / "Library/Application Support/Google/Chrome/Default"

    async with async_playwright() as p:
        try:
            # まず Chrome プロファイルで起動を試みる（ログイン済みセッションを再利用）
            ctx = await p.chromium.launch_persistent_context(
                user_data_dir=str(chrome_profile),
                headless=True,
                args=["--no-sandbox"],
                channel="chrome",
            )
        except Exception:
            # Chrome が起動中などで失敗した場合は headful で再試行
            ctx = await p.chromium.launch_persistent_context(
                user_data_dir=str(chrome_profile),
                headless=False,
                args=["--no-sandbox"],
                channel="chrome",
            )
        page = await ctx.new_page()
        await page.goto(
            "https://libecity.com/mypage/tsubuyaki",
            wait_until="domcontentloaded",
            timeout=30_000,
        )
        await page.wait_for_timeout(3000)

        js = make_js(start_iso, end_iso, keyword)
        result = await page.evaluate(js)
        await ctx.close()

    if isinstance(result, dict) and result.get("error"):
        print(f"  ❌ エラー: {result['error']}")
        return []

    total    = result.get("total_scanned", 0)
    matched  = result.get("matched", 0)
    events   = result.get("events", [])
    print(f"  ✅ スキャン: {total}件 → キーワードマッチ: {matched}件")
    return events


def merge_events(calendar_events: list, tweet_events: list) -> list:
    """カレンダーイベントとつぶやきイベントをマージ（重複排除）"""
    merged = list(calendar_events)
    existing_titles = {e.get("title", "").lower() for e in calendar_events}

    added = 0
    for ev in tweet_events:
        # タイトルが既存イベントと類似していれば重複とみなしスキップ
        t = ev.get("title", "").lower()
        if any(t[:20] in existing for existing in existing_titles):
            continue
        merged.append(ev)
        existing_titles.add(t)
        added += 1

    print(f"  📎 つぶやき追加: {added}件 (重複スキップ: {len(tweet_events) - added}件)")
    return merged


def main():
    # ── 引数解析 ──────────────────────────────────────────
    start_date = datetime.now()
    end_date   = start_date + timedelta(days=31)
    keyword    = "claude"

    if len(sys.argv) >= 3:
        try:
            start_date = datetime.fromisoformat(sys.argv[1])
            end_date   = datetime.fromisoformat(sys.argv[2])
        except ValueError:
            print("日付形式エラー。YYYY-MM-DD で指定してください。")
            sys.exit(1)
    if len(sys.argv) >= 4:
        keyword = sys.argv[3]

    start_iso = start_date.strftime("%Y-%m-%dT00:00:00Z")
    end_iso   = end_date.strftime("%Y-%m-%dT23:59:59Z")

    # ── つぶやき収集 ──────────────────────────────────────
    print(f"\n🐦 つぶやき収集開始")
    tweet_events = asyncio.run(collect(start_iso, end_iso, keyword))

    # ── 既存 events.json があればマージ ──────────────────
    events_path = OUTPUT_DIR / "events.json"
    if events_path.exists():
        print(f"\n📂 既存 events.json をロード中...")
        with open(events_path, encoding="utf-8") as f:
            existing = json.load(f)
        print(f"  カレンダーイベント: {len(existing)}件")
        merged = merge_events(existing, tweet_events)
    else:
        merged = tweet_events

    # ── 保存 ──────────────────────────────────────────────
    with open(events_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    print(f"\n💾 保存完了: {events_path}  (合計: {len(merged)}件)")

    # ── サマリー ──────────────────────────────────────────
    tweet_only = [e for e in merged if e.get("source") == "つぶやき"]
    cal_only   = [e for e in merged if e.get("source") != "つぶやき"]
    print(f"\n📊 内訳")
    print(f"  カレンダー: {len(cal_only)}件")
    print(f"  つぶやき:   {len(tweet_only)}件")
    print(f"  合計:       {len(merged)}件")


if __name__ == "__main__":
    main()
