#!/usr/bin/env python3
"""
リベシティのつぶやきから「カレンダー未掲載のオフ会情報」のみを収集し
PDF / PNG リストを生成するスタンドアロンスクリプト。

特徴:
  - Firestoreのtweetsコレクションを直接クエリ（DOM不要）
  - オフ会関連キーワードでフィルタリング（告知投稿に絞り込み）
  - カレンダーイベントと重複するものを除外
  - 参照元つぶやきのURLリンク付き一覧を出力
  - 初回セットアップ後はClaudeデスクトップアプリから一行で実行可能

Usage:
  python3 tweet_offkai.py [start_date] [end_date] [keyword]
  例: python3 tweet_offkai.py 2026-04-01 2026-05-31 claude

  引数省略時: 今日から1ヶ月先、キーワード=claude
"""

import asyncio
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import List

OUTPUT_DIR = Path.home() / "Documents" / "libecity-offkai"

# ── オフ会告知とみなすキーワード（いずれか1つを含む投稿のみ抽出）──
OFFKAI_KEYWORDS = [
    "オフ会", "ミートアップ", "meetup", "勉強会", "ワークショップ", "workshop",
    "開催", "会場", "現地", "日時", "募集", "申込", "申し込み", "定員",
    "参加費", "参加無料", "無料参加", "参加者募集", "参加受付",
    "オフライン", "zoom", "ovice", "discord", "google meet",
    "集合", "場所", "開場", "開始時間", "締切", "先着",
    "交流会", "懇親会", "もくもく会"
]


# ─────────────────────────────────────────────────────────
# ブラウザ内で実行するJavaScript（Firestore クエリ）
# ─────────────────────────────────────────────────────────
def make_js(start_iso: str, end_iso: str, keyword: str) -> str:
    offkai_kw_json = json.dumps(OFFKAI_KEYWORDS, ensure_ascii=False)
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
  const BASE     = 'https://firestore.googleapis.com/v1/projects/production-b8884/databases/(default)/documents';
  const startDate = '{start_iso}';
  const endDate   = '{end_iso}';
  const keyword   = '{keyword}'.toLowerCase();
  const offkaiKw  = {offkai_kw_json};

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
  if (!resp.ok) return {{ error: `Firestore query failed: ${{resp.status}} ${{await resp.text()}}` }};
  const rawData = await resp.json();

  // ── 3. フィルタリング ────────────────────────────────
  // 条件: ①キーワード含む ②トップレベルのみ ③オフ会告知キーワードを含む
  const DAYS_JA = ['日','月','火','水','木','金','土'];

  const events = [];
  for (const item of rawData) {{
    if (!item.document) continue;
    const f        = item.document.fields;
    const rawBody  = f.contents?.stringValue || '';
    const lower    = rawBody.toLowerCase();

    // ① キーワードフィルタ
    if (!lower.includes(keyword)) continue;

    // ② リプライ除外（トップレベルのみ）
    if (f.parent_path?.stringValue) continue;

    // ③ オフ会告知キーワードを少なくとも1つ含む
    const isOffkai = offkaiKw.some(kw => lower.includes(kw.toLowerCase()));
    if (!isOffkai) continue;

    // ── ドキュメントID → つぶやきURL ──
    const docId     = item.document.name.split('/').pop();
    const tweetUrl  = `https://libecity.com/mypage/tsubuyaki?id=${{docId}}`;

    // ── 日時 ──
    const createdAt = new Date(f.created_at?.timestampValue);
    const dow       = DAYS_JA[createdAt.getDay()];
    const month     = String(createdAt.getMonth() + 1).padStart(2, '0');
    const day       = String(createdAt.getDate()).padStart(2, '0');
    const dateStr   = `${{month}}/${{day}}(${{dow}})`;
    const timeStr   = `${{String(createdAt.getHours()).padStart(2,'0')}}:${{String(createdAt.getMinutes()).padStart(2,'0')}}`;

    // ── 形式推定 ──
    let format = '—';
    if (lower.includes('オンライン') || lower.includes('zoom') ||
        lower.includes('ovice')     || lower.includes('google meet') ||
        lower.includes('discord')   || lower.includes('online')) {{
      format = 'オンライン';
    }} else if (lower.includes('オフライン') || lower.includes('オフ会') ||
               lower.includes('現地') || lower.includes('会場')) {{
      format = 'オフライン';
    }}

    // ── タイトル（1行目）──
    const firstLine = rawBody.split('\\n')[0].trim();
    const title     = (firstLine.length > 4 ? firstLine : rawBody).substring(0, 60);

    events.push({{
      docId,
      date:      dateStr,
      time:      timeStr,
      title,
      format,
      url:       tweetUrl,
      organizer: f.name?.stringValue || '—',
      source:    'つぶやき',
      tweetBody: rawBody.substring(0, 400)
    }});
  }}

  return {{
    total_scanned: rawData.filter(i => i.document).length,
    matched:       events.length,
    events
  }};
}})()
"""


# ─────────────────────────────────────────────────────────
# ブラウザ経由でFirestoreからつぶやきを収集
# ─────────────────────────────────────────────────────────
async def collect_tweets(start_iso: str, end_iso: str, keyword: str = "claude") -> list:
    from playwright.async_api import async_playwright
    import shutil
    import tempfile

    print(f"  📡 Firestore tweets クエリ中... ({start_iso[:10]} ～ {end_iso[:10]}, keyword={keyword})")

    chrome_profile = Path.home() / "Library/Application Support/Google/Chrome/Default"
    js = make_js(start_iso, end_iso, keyword)

    async def _run_in_context(ctx) -> dict:
        """コンテキストを受け取ってJSを実行し結果を返す"""
        page = await ctx.new_page()
        await page.goto("https://libecity.com/mypage/tsubuyaki",
                        wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(3000)
        result = await page.evaluate(js)
        await ctx.close()
        return result

    result = None
    tmp_dir = None

    async with async_playwright() as p:

        # ── 方法1: システムChromeプロファイルで直接起動 ──────
        # （Chromeが閉じている場合はこれで動く）
        try:
            ctx = await p.chromium.launch_persistent_context(
                user_data_dir=str(chrome_profile),
                headless=True,
                args=["--no-sandbox"],
                channel="chrome",
            )
            result = await _run_in_context(ctx)

        except Exception as e1:
            print(f"  ⚠️ 直接起動失敗: {type(e1).__name__}")
            print("  🔄 Chrome起動中のためプロファイルをコピーして再試行中...")

            # ── 方法2: 認証に必要なファイルだけコピーして起動 ──
            # Chrome起動中でもプロファイルロックを回避できる
            tmp_dir = tempfile.mkdtemp(prefix="pw_chrome_")
            try:
                dst_profile = Path(tmp_dir) / "Default"
                dst_profile.mkdir(parents=True)

                # Firebase認証トークンの格納先（IndexedDB）+ Cookie をコピー
                for item_name in ["IndexedDB", "Cookies", "Local Storage", "Session Storage"]:
                    src = chrome_profile / item_name
                    dst = dst_profile / item_name
                    if not src.exists():
                        continue
                    if src.is_dir():
                        shutil.copytree(
                            str(src), str(dst),
                            ignore=shutil.ignore_patterns("*.lock", "LOCK", "lockfile"),
                        )
                    else:
                        shutil.copy2(str(src), str(dst))

                # Playwright内蔵Chromium（channel指定なし）でコピー先を使用
                ctx = await p.chromium.launch_persistent_context(
                    user_data_dir=tmp_dir,
                    headless=True,
                    args=["--no-sandbox", "--disable-extensions"],
                )
                result = await _run_in_context(ctx)

            except Exception as e2:
                print(f"  ❌ 再試行も失敗: {e2}")
            finally:
                if tmp_dir:
                    shutil.rmtree(tmp_dir, ignore_errors=True)

    if result is None:
        print("  ❌ ブラウザの起動に失敗しました。")
        print("  💡 Chromeを一旦閉じてから再実行するか、以下を確認してください:")
        print("     - Chromeで https://libecity.com にログイン済みか")
        print("     - playwright install chromium を実行済みか")
        return []

    if isinstance(result, dict) and result.get("error"):
        print(f"  ❌ {result['error']}")
        print("  💡 Chromeで https://libecity.com にログインしてから再実行してください。")
        return []

    total   = result.get("total_scanned", 0)
    matched = result.get("matched", 0)
    events  = result.get("events", [])
    print(f"  ✅ スキャン: {total}件 → オフ会告知マッチ: {matched}件")
    return events


# ─────────────────────────────────────────────────────────
# カレンダーイベントとの重複除外
# ─────────────────────────────────────────────────────────
def exclude_calendar_duplicates(tweet_events: list, calendar_events: list) -> list:
    """カレンダーに既に掲載されているものを除外する"""
    def normalize(t: str) -> str:
        return t.lower().strip()[:20]

    cal_titles = {normalize(e.get("title", "")) for e in calendar_events}

    unique = []
    skipped = 0
    for ev in tweet_events:
        t = normalize(ev.get("title", ""))
        # タイトル前20文字での重複チェック
        if t and t in cal_titles:
            skipped += 1
            continue
        unique.append(ev)

    print(f"  🔍 カレンダー重複除外: {skipped}件 → カレンダー未掲載: {len(unique)}件")
    return unique


# ─────────────────────────────────────────────────────────
# つぶやき本文からオフ会情報を推測抽出
# ─────────────────────────────────────────────────────────
def extract_event_info(body: str) -> dict:
    """つぶやき本文から オフ会名・開催日時・場所 を正規表現で推測抽出する"""

    # ── オフ会名 ─────────────────────────────────────────
    # 優先順: 【】『』「」内 → 最初の行
    event_name = "不明"
    bracket = re.search(r'[【〔「『]([^】〕」』\n]{2,40})[】〕」』]', body)
    if bracket:
        event_name = bracket.group(1).strip()
    else:
        first = body.split('\n')[0].strip()
        if len(first) >= 3:
            event_name = first[:50]

    # ── 開催日時 ──────────────────────────────────────────
    # パターン例: 4月5日（土）10:00 / 4/5(土)10時 / 2026年4月5日 など
    event_datetime = "不明"
    dt_patterns = [
        # 〇月〇日（曜）〇時〇〇分 / 〇:〇〇
        r'\d{1,2}月\d{1,2}日[（(][月火水木金土日][）)][\s　]*\d{1,2}[時:：]\d{2}',
        r'\d{1,2}月\d{1,2}日[（(][月火水木金土日][）)][\s　]*\d{1,2}時',
        r'\d{1,2}月\d{1,2}日[（(][月火水木金土日][）)]',
        # 〇/〇（曜）〇:〇〇
        r'\d{1,2}/\d{1,2}[（(][月火水木金土日][）)][\s　]*\d{1,2}:\d{2}',
        r'\d{1,2}/\d{1,2}[（(][月火水木金土日][）)]',
        # 〇月〇日 〇:〇〇
        r'\d{1,2}月\d{1,2}日[\s　]+\d{1,2}:\d{2}',
        r'\d{1,2}月\d{1,2}日',
        # YYYY年M月D日
        r'\d{4}年\d{1,2}月\d{1,2}日',
    ]
    for pat in dt_patterns:
        m = re.search(pat, body)
        if m:
            event_datetime = m.group(0).strip()
            break

    # ── 場所 ──────────────────────────────────────────────
    # 「会場：〇〇」「場所：〇〇」パターン → なければツールから推定
    venue = "不明"
    venue_label = re.search(
        r'(?:会場|場所|開催地|開催場所|開催地)[：:・]\s*([^\n　]{2,30})', body
    )
    if venue_label:
        venue = venue_label.group(1).strip()[:30]
    else:
        lower = body.lower()
        if 'zoom' in lower:
            venue = 'Zoom'
        elif 'ovice' in lower:
            venue = 'OVice'
        elif 'discord' in lower:
            venue = 'Discord'
        elif 'google meet' in lower:
            venue = 'Google Meet'
        elif 'teams' in lower:
            venue = 'Teams'
        elif 'オンライン' in lower or 'online' in lower:
            venue = 'オンライン'
        elif 'オフライン' in lower or '現地' in lower:
            # 「〇〇会場」「〇〇ビル」「〇〇カフェ」などを探す
            loc = re.search(r'[\u30A0-\u30FF\u3040-\u309F\u4E00-\u9FFF]{2,12}(?:ビル|タワー|センター|ホール|カフェ|オフィス|スペース|会館|施設)', body)
            if loc:
                venue = loc.group(0)
            else:
                venue = 'オフライン'

    return {
        "event_name":     event_name,
        "event_datetime": event_datetime,
        "venue":          venue,
    }


# ─────────────────────────────────────────────────────────
# HTML 生成（つぶやきURL付き）
# ─────────────────────────────────────────────────────────
def build_html(events: List[dict], generated_at: datetime,
               start_date: str, end_date: str) -> str:
    today_str = generated_at.strftime("%Y年%m月%d日 %H:%M")

    rows = ""
    for i, ev in enumerate(events, 1):
        bg        = "#f8f9ff" if i % 2 == 0 else "#ffffff"
        url       = ev.get("url") or ""
        body_raw  = ev.get("tweetBody") or ""
        body      = body_raw.replace("\n", "<br>")
        date_str  = ev.get("date") or "—"
        time_str  = ev.get("time") or ""
        organizer = ev.get("organizer") or "—"

        # つぶやき本文からオフ会情報を抽出
        info = extract_event_info(body_raw)
        event_name = info["event_name"]
        event_dt   = info["event_datetime"]
        venue      = info["venue"]

        tweet_link  = (f'<a href="{url}" class="tweet-link" target="_blank">🔗 つぶやきを見る</a>'
                       if url else "—")
        name_link   = (f'<a href="{url}" class="ev-link" target="_blank">{event_name}</a>'
                       if url else event_name)

        rows += f"""
        <tr style="background:{bg}">
          <td class="num">{i}</td>
          <td class="dt">{date_str} {time_str}</td>
          <td class="org">{organizer}</td>
          <td class="evname">{name_link}</td>
          <td class="evdt">{event_dt}</td>
          <td class="venue">{venue}</td>
          <td class="body">{body}</td>
          <td class="link">{tweet_link}</td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  font-family: 'Hiragino Kaku Gothic ProN','Hiragino Sans','Yu Gothic UI','Meiryo','Noto Sans CJK JP',sans-serif;
  font-size: 12px; color: #222; background:#fff; padding:24px 28px; min-width:1300px;
}}
.header h1 {{ font-size:20px; font-weight:700; color:#1a1a2e; margin-bottom:8px; }}
.meta {{ display:flex; gap:10px; flex-wrap:wrap; margin-bottom:14px; }}
.meta span {{
  font-size:11px; background:#fff3e0; color:#7c4700;
  border:1px solid #ffcc80; border-radius:20px; padding:3px 12px;
}}
.notice {{
  background:#fff8e1; border:2px solid #ffd54f; border-radius:8px;
  padding:10px 16px; margin-bottom:16px; font-size:12px; color:#5d4037; line-height:1.7;
}}
.summary {{
  background:linear-gradient(120deg,#e65100 0%,#ff6f00 100%);
  color:#fff; padding:10px 18px; border-radius:8px;
  font-size:13px; font-weight:700; margin-bottom:18px;
}}
.section-header {{
  color:#fff; font-weight:700; font-size:13px; background:#e65100;
  padding:8px 16px; border-radius:6px 6px 0 0; margin-top:24px;
}}
table {{ width:100%; border-collapse:collapse; box-shadow:0 2px 12px rgba(0,0,0,0.07); }}
thead tr {{ background:#1a1a2e; color:#fff; }}
thead th {{ padding:9px 8px; text-align:left; font-size:11px; font-weight:700; white-space:nowrap; }}
th.num, td.num {{ text-align:center; width:28px; }}
td.dt    {{ white-space:nowrap; width:100px; color:#777; font-size:10px; }}
td.org   {{ width:100px; color:#333; font-size:11px; word-break:break-all; }}
td.evname {{ font-weight:700; color:#1a1a2e; width:180px; word-break:break-all; font-size:11px; }}
td.evdt  {{ white-space:nowrap; width:130px; color:#c05000; font-size:11px; font-weight:600; }}
td.venue {{ width:110px; color:#166534; font-size:11px; font-weight:600; word-break:break-all; }}
td.body  {{ font-size:10px; color:#444; line-height:1.6; word-break:break-all; }}
td.link  {{ width:100px; text-align:center; }}
tbody td {{ padding:7px 8px; border-bottom:1px solid #eee; vertical-align:top; }}
tbody tr:last-child td {{ border-bottom:none; }}
tbody tr:hover {{ background:#fff3e0 !important; }}
a.ev-link    {{ color:#1a1a2e; text-decoration:underline; word-break:break-all; }}
a.tweet-link {{ display:inline-block; padding:3px 8px; background:#fff3e0; border:1px solid #ffcc80;
                border-radius:12px; color:#e65100; text-decoration:none; font-size:10px; font-weight:700; white-space:nowrap; }}
a.tweet-link:hover {{ background:#ffe0b2; }}
.footer {{ margin-top:14px; text-align:right; font-size:11px; color:#bbb; }}
</style>
</head>
<body>
<div class="header">
  <h1>🐦 リベシティ　カレンダー未掲載オフ会（つぶやきのみ）</h1>
  <div class="meta">
    <span>生成日時：{today_str}</span>
    <span>検索キーワード：claude</span>
    <span>対象期間：{start_date} ～ {end_date}</span>
    <span>件数：{len(events)}件</span>
  </div>
</div>
<div class="notice">
  ℹ️ <b>この一覧について</b><br>
  リベシティのカレンダーには登録されておらず、<b>つぶやきのみで告知されているオフ会・イベント情報</b>を収集しています。<br>
  右端の「🔗 つぶやきを見る」ボタンから元のつぶやきを直接確認できます。<br>
  ※ キーワード自動判定のため、オフ会以外の投稿が混入する場合があります。
</div>
<div class="summary">
  📋 カレンダー未掲載のオフ会情報：{len(events)} 件
</div>
<div class="section-header">🐦 つぶやきのみで告知されているオフ会・イベント（{len(events)}件）</div>
<table>
  <thead>
    <tr>
      <th class="num">#</th>
      <th>投稿日時</th>
      <th>開催者名</th>
      <th>オフ会名</th>
      <th>開催日時</th>
      <th>場所</th>
      <th>つぶやき本文</th>
      <th>参照元</th>
    </tr>
  </thead>
  <tbody>{rows}
  </tbody>
</table>
<div class="footer">Generated by リベシティ つぶやきオフ会収集システム &nbsp;|&nbsp; {today_str}</div>
</body>
</html>"""


# ─────────────────────────────────────────────────────────
# PDF / PNG レンダリング
# ─────────────────────────────────────────────────────────
def render(html: str, stem: str):
    from playwright.sync_api import sync_playwright

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        # PDF
        pdf_page = browser.new_page(viewport={"width": 1400, "height": 900})
        pdf_page.set_content(html, wait_until="domcontentloaded")
        pdf_page.wait_for_timeout(600)
        pdf_path = OUTPUT_DIR / (stem + ".pdf")
        pdf_page.pdf(
            path=str(pdf_path), format="A4", landscape=True,
            margin={"top": "10mm", "bottom": "10mm", "left": "8mm", "right": "8mm"},
            print_background=True,
        )
        pdf_page.close()
        print(f"  📄 PDF: {pdf_path}")

        # PNG
        png_page = browser.new_page(
            viewport={"width": 1500, "height": 900}, device_scale_factor=2
        )
        png_page.set_content(html, wait_until="domcontentloaded")
        png_page.wait_for_timeout(600)
        png_path = OUTPUT_DIR / (stem + ".png")
        png_page.screenshot(path=str(png_path), full_page=True, type="png")
        png_page.close()
        print(f"  🖼️  PNG: {png_path}")

        browser.close()


# ─────────────────────────────────────────────────────────
# メイン
# ─────────────────────────────────────────────────────────
def main():
    # ── 引数解析 ──────────────────────────────────────────
    now        = datetime.now()
    start_date = now
    end_date   = now + timedelta(days=31)
    keyword    = "claude"
    from_json_path = None   # --from-json <path> で事前収集済みJSONを指定

    # --from-json を先に抜き出す
    raw_args = sys.argv[1:]
    if "--from-json" in raw_args:
        idx = raw_args.index("--from-json")
        from_json_path = raw_args[idx + 1]
        raw_args = raw_args[:idx] + raw_args[idx + 2:]

    if len(raw_args) >= 2:
        try:
            start_date = datetime.fromisoformat(raw_args[0])
            end_date   = datetime.fromisoformat(raw_args[1])
        except ValueError:
            print("日付形式エラー。YYYY-MM-DD で指定してください。")
            sys.exit(1)
    if len(raw_args) >= 3:
        keyword = raw_args[2]

    start_iso    = start_date.strftime("%Y-%m-%dT00:00:00Z")
    end_iso      = end_date.strftime("%Y-%m-%dT23:59:59Z")
    start_str    = start_date.strftime("%Y/%m/%d")
    end_str      = end_date.strftime("%Y/%m/%d")
    date_prefix  = now.strftime("%y%m%d")

    print("=" * 60)
    print("🐦 リベシティ つぶやきオフ会収集システム")
    print(f"   対象期間: {start_str} ～ {end_str}")
    print(f"   キーワード: {keyword}")
    if from_json_path:
        print(f"   モード: JSONファイルから読み込み")
    print("=" * 60)

    # ── Step 1: つぶやき収集 ──────────────────────────────
    print("\n[Step 1] つぶやきを収集中...")
    if from_json_path:
        # Claude が事前に MCP Chrome で取得したJSONを読み込む
        print(f"  📂 JSONから読み込み: {from_json_path}")
        with open(from_json_path, encoding="utf-8") as f:
            data = json.load(f)
        # make_js() の戻り値形式 {"events": [...]} または生リスト両対応
        tweet_events = data.get("events", []) if isinstance(data, dict) else data
        print(f"  ✅ {len(tweet_events)}件読み込み完了")
    else:
        tweet_events = asyncio.run(collect_tweets(start_iso, end_iso, keyword))

    if not tweet_events:
        print("  ⚠️ オフ会告知ツイートが見つかりませんでした。")
        sys.exit(0)

    # ── Step 2: カレンダーイベントと重複除外 ──────────────
    print("\n[Step 2] カレンダーとの重複チェック...")
    events_path = OUTPUT_DIR / "events.json"
    calendar_events = []
    if events_path.exists():
        with open(events_path, encoding="utf-8") as f:
            all_events = json.load(f)
        calendar_events = [e for e in all_events if e.get("source") != "つぶやき"]
        print(f"  📅 カレンダーイベント読み込み: {len(calendar_events)}件")
    else:
        print("  ℹ️ events.json が見つかりません。重複除外をスキップします。")

    unique_tweets = exclude_calendar_duplicates(tweet_events, calendar_events)

    if not unique_tweets:
        print("  ⚠️ カレンダー未掲載のオフ会が見つかりませんでした。")
        sys.exit(0)

    # ── Step 3: 結果を保存 ────────────────────────────────
    print(f"\n[Step 3] 結果を保存中...")
    result_path = OUTPUT_DIR / f"{date_prefix}_tweet_offkai_exclusive.json"
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(unique_tweets, f, ensure_ascii=False, indent=2)
    print(f"  💾 JSON: {result_path}")

    # ── Step 4: PDF / PNG 出力 ────────────────────────────
    print(f"\n[Step 4] PDF / PNG を出力中...")
    html = build_html(unique_tweets, now, start_str, end_str)
    stem = f"{date_prefix}_{keyword}_つぶやきオフ会（カレンダー未掲載）"
    render(html, stem)

    # ── サマリー ──────────────────────────────────────────
    print("\n" + "=" * 60)
    print("✅ 完了！")
    print(f"   収集: {len(tweet_events)}件 → カレンダー未掲載: {len(unique_tweets)}件")
    print(f"   出力ファイル:")
    print(f"     {stem}.pdf")
    print(f"     {stem}.png")
    print("=" * 60)


if __name__ == "__main__":
    main()
