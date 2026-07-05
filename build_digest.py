#!/usr/bin/env python3
"""
The Morning / הבוקר — daily personalized news agent.

Pipeline: Fetch -> Dedup -> Rank -> (optional AI summaries) -> Render -> index.html

Free mode  : keyword + recency + source-quality ranking (no keys needed).
AI mode    : if the environment variable ANTHROPIC_API_KEY is set, Claude
             writes 1-2 sentence summaries per story (in the story's own language).

Usage:
    python build_digest.py            # normal daily run
    python build_digest.py --demo     # build with bundled sample data (no network)
"""

import json
import html
import os
import re
import sys
import time
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import feedparser

ROOT = Path(__file__).parent
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
TZ = ZoneInfo(CONFIG.get("timezone", "Asia/Jerusalem"))
UA = {"User-Agent": "Mozilla/5.0 (personal-news-digest; +https://github.com)"}

HEBREW_RE = re.compile(r"[\u0590-\u05FF]")


# ----------------------------------------------------------------------------
# Fetch
# ----------------------------------------------------------------------------
def fetch_feed(feed_cfg):
    """Fetch one RSS feed; return a list of raw article dicts. Never raises."""
    articles = []
    try:
        resp = requests.get(feed_cfg["url"], headers=UA, timeout=20)
        resp.raise_for_status()
        parsed = feedparser.parse(resp.content)
        for entry in parsed.entries[:40]:
            title = html.unescape(getattr(entry, "title", "") or "").strip()
            link = getattr(entry, "link", "") or ""
            if not title or not link:
                continue
            summary = html.unescape(re.sub(r"<[^>]+>", " ", getattr(entry, "summary", "") or "")).strip()
            summary = re.sub(r"\s+", " ", summary)
            # Strip common feed boilerplate that pollutes summaries.
            summary = re.sub(r"The post .{0,160}? appeared first on .{0,80}?\s*\.?\s*$", "", summary)
            summary = re.sub(r"(Continue reading|Read more|Read the full article).{0,80}$", "", summary, flags=re.I)
            summary = summary.strip()[:400]
            ts = None
            for key in ("published_parsed", "updated_parsed"):
                t = getattr(entry, key, None)
                if t:
                    ts = datetime.fromtimestamp(time.mktime(t), tz=timezone.utc)
                    break
            articles.append({
                "title": title,
                "link": link,
                "summary": summary,
                "published": ts,
                "source": feed_cfg["name"],
                "lang": feed_cfg["lang"],
                "topics": feed_cfg["topics"],
                "source_weight": feed_cfg.get("source_weight", 0.8),
            })
        print(f"  ok   {feed_cfg['name']}: {len(articles)} items")
    except Exception as exc:  # a dead feed must never kill the daily run
        print(f"  SKIP {feed_cfg['name']}: {exc}")
    return articles


# ----------------------------------------------------------------------------
# Dedup (same-language title clustering)
# ----------------------------------------------------------------------------
def _tokens(title):
    return set(re.findall(r"[\w\u0590-\u05FF]{3,}", title.lower()))


def dedup(articles):
    kept = []
    for art in sorted(articles, key=lambda a: -a["source_weight"]):
        toks = _tokens(art["title"])
        duplicate = False
        for other in kept:
            if other["lang"] != art["lang"]:
                continue
            o = _tokens(other["title"])
            if toks and o:
                jaccard = len(toks & o) / len(toks | o)
                if jaccard >= 0.55:
                    duplicate = True
                    break
        if not duplicate:
            kept.append(art)
    return kept


# ----------------------------------------------------------------------------
# Rank
# ----------------------------------------------------------------------------
def score(article, now):
    topics = CONFIG["topics"]
    best_topic = max(article["topics"], key=lambda t: topics.get(t, {}).get("weight", 0.5))
    s = topics.get(best_topic, {}).get("weight", 0.5) * article["source_weight"]

    text = (article["title"] + " " + article["summary"]).lower()
    for topic, words in CONFIG.get("boost_keywords", {}).items():
        hits = sum(1 for w in words if w.lower() in text)
        if hits:
            s += min(hits, 3) * 0.08 * (1.4 if topic in article["topics"] else 1.0)

    if article["published"]:
        age_h = max(0.0, (now - article["published"]).total_seconds() / 3600)
        s *= max(0.25, 1.0 - age_h / (CONFIG["freshness_hours"] * 1.6))
    else:
        s *= 0.6

    article["topic"] = best_topic
    return s


def select(articles, now):
    fresh_cutoff = now - timedelta(hours=CONFIG["freshness_hours"])
    pool = [a for a in articles if (a["published"] is None or a["published"] >= fresh_cutoff)]

    muted = [m.lower() for m in CONFIG.get("muted_keywords", [])]
    if muted:
        pool = [a for a in pool if not any(m in (a["title"] + a["summary"]).lower() for m in muted)]

    for a in pool:
        a["score"] = score(a, now)
    pool.sort(key=lambda a: -a["score"])

    chosen, per_topic, per_source = [], {}, {}

    def take(a):
        chosen.append(a)
        per_topic[a["topic"]] = per_topic.get(a["topic"], 0) + 1
        per_source[a["source"]] = per_source.get(a["source"], 0) + 1

    # Pass 1 — topic guarantee: every topic gets at least min_per_topic
    # stories (best-ranked first), source cap respected.
    min_pt = CONFIG.get("min_per_topic", 0)
    for topic in CONFIG["topics"]:
        for a in pool:
            if per_topic.get(topic, 0) >= min_pt:
                break
            if a["topic"] != topic or a in chosen:
                continue
            if per_source.get(a["source"], 0) >= CONFIG["max_per_source"]:
                continue
            take(a)

    # Pass 2 — fill remaining slots globally by score, all caps respected.
    for a in pool:
        if len(chosen) >= CONFIG["max_stories"]:
            break
        if a in chosen:
            continue
        if per_topic.get(a["topic"], 0) >= CONFIG["max_per_topic"]:
            continue
        if per_source.get(a["source"], 0) >= CONFIG["max_per_source"]:
            continue
        take(a)

    chosen.sort(key=lambda a: -a["score"])
    return chosen


# ----------------------------------------------------------------------------
# Optional AI summaries (activates automatically when ANTHROPIC_API_KEY is set)
# ----------------------------------------------------------------------------
def ai_summaries(articles):
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key or not CONFIG.get("ai", {}).get("enabled_when_key_present", True):
        return
    print("AI mode: generating summaries with Claude...")
    instruction = CONFIG["ai"]["summary_instruction"]
    items = [{"i": i, "title": a["title"], "text": a["summary"][:300]} for i, a in enumerate(articles)]
    prompt = (
        f"{instruction}\n\nHere are news items as JSON. Respond ONLY with a JSON object "
        f'mapping each "i" to its summary string, no markdown fences.\n\n{json.dumps(items, ensure_ascii=False)}'
    )
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": CONFIG["ai"]["model"], "max_tokens": 4000,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=120,
        )
        resp.raise_for_status()
        text = "".join(b.get("text", "") for b in resp.json()["content"])
        mapping = json.loads(re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.M))
        for i, a in enumerate(articles):
            better = mapping.get(str(i))
            if better:
                a["summary"] = better.strip()
        print(f"  Claude summarized {len(mapping)} stories.")
    except Exception as exc:
        print(f"  AI step skipped ({exc}) — falling back to feed descriptions.")


# ----------------------------------------------------------------------------
# Render
# ----------------------------------------------------------------------------
def render(articles, now, demo=False):
    digest = {
        "generated_at": now.isoformat(),
        "generated_display": now.astimezone(TZ).strftime("%A, %d %B %Y · %H:%M"),
        "site_title": CONFIG["site_title"],
        "demo": demo,
        "topics": {
            k: {"en": v["label_en"], "he": v["label_he"]} for k, v in CONFIG["topics"].items()
        },
        "stories": [
            {
                "title": a["title"],
                "link": a["link"],
                "summary": a["summary"],
                "source": a["source"],
                "lang": a["lang"],
                "topic": a["topic"],
                "rtl": bool(HEBREW_RE.search(a["title"])),
                "published": a["published"].isoformat() if a["published"] else None,
            }
            for a in articles
        ],
    }
    (ROOT / "digest.json").write_text(json.dumps(digest, ensure_ascii=False, indent=1), encoding="utf-8")

    template = (ROOT / "template.html").read_text(encoding="utf-8")
    page = template.replace("/*__DIGEST__*/null", json.dumps(digest, ensure_ascii=False))
    (ROOT / "index.html").write_text(page, encoding="utf-8")
    print(f"Rendered index.html with {len(articles)} stories.")


# ----------------------------------------------------------------------------
# Demo data (used only with --demo; lets you preview the design without network)
# ----------------------------------------------------------------------------
HOMES = {'Ynet חדשות': 'https://www.ynet.co.il', 'BBC World': 'https://www.bbc.com/news/world', 'TechCrunch': 'https://techcrunch.com', 'כלכליסט': 'https://www.calcalist.co.il', 'Times of Israel': 'https://www.timesofisrael.com', 'Ynet טכנולוגיה': 'https://www.ynet.co.il/digital', 'The Guardian': 'https://www.theguardian.com/world', 'גלובס': 'https://www.globes.co.il', 'The Verge': 'https://www.theverge.com'}


def demo_articles(now):
    sample = [
        ("he", "Ynet חדשות", ["israel"], "ראש הממשלה יכריז הערב על מתווה חדש: כל הפרטים",
         "בכירים בירושלים אישרו כי ההצהרה צפויה בשעות הערב, לאחר סבב התייעצויות שנמשך כל הלילה.", 2),
        ("en", "BBC World", ["world"], "EU leaders reach landmark climate financing deal after marathon summit",
         "The agreement commits member states to a joint fund, ending months of deadlock between northern and southern blocs.", 3),
        ("en", "TechCrunch", ["tech"], "Anthropic and OpenAI race to ship agentic browsers as AI moves past chat",
         "Both labs previewed agents that navigate the web on a user's behalf, marking the biggest interface shift since the chatbot era began.", 4),
        ("he", "כלכליסט", ["business"], "השקל מתחזק לשיא של שנתיים; הבורסה בת\"א ננעלה בעליות חדות",
         "מדד ת\"א 35 עלה ב-1.4% על רקע נתוני אינפלציה נמוכים מהצפוי והערכות להורדת ריבית.", 5),
        ("en", "Times of Israel", ["israel"], "Nazareth tech hub doubles in size as Galilee startups draw record funding",
         "Investment in northern Israel's tech corridor hit an all-time high this quarter, led by AI and medical-device firms.", 6),
        ("he", "Ynet טכנולוגיה", ["tech"], "מהפכת ה-AI מגיעה לבתי החולים בישראל: כך זה ייראה",
         "מערכת חדשה מבוססת בינה מלאכותית תסייע לרופאים באבחון מוקדם, בפיילוט שיחל בשלושה מרכזים רפואיים.", 8),
        ("en", "The Guardian", ["world"], "Global markets steady as central banks signal coordinated rate path",
         "Investors welcomed rare alignment between the Fed, ECB and Bank of England on the timing of cuts.", 9),
        ("he", "גלובס", ["business"], "אקזיט ענק בהייטק: חברה ישראלית נמכרת ב-2.1 מיליארד דולר",
         "העסקה, הגדולה השנה, צפויה להזרים מאות מיליוני שקלים לקופת המדינה ממסים.", 11),
        ("en", "The Verge", ["tech"], "The best laptops of 2026 so far — and what to wait for",
         "Our mid-year roundup covers the machines that actually earned a recommendation, from ultralights to workstations.", 13),
        ("he", "Ynet חדשות", ["world"], "פסגה היסטורית: מנהיגי המזרח התיכון ייפגשו בקהיר בשבוע הבא",
         "גורמים דיפלומטיים העריכו כי הפגישה עשויה להוביל לפריצת דרך אזורית.", 14),
    ]
    arts = []
    for lang, source, topics, title, summary, hours_ago in sample:
        arts.append({
            "title": title, "link": HOMES.get(source, "#"), "summary": summary,
            "published": now - timedelta(hours=hours_ago),
            "source": source, "lang": lang, "topics": topics, "source_weight": 0.95,
        })
    return arts


# ----------------------------------------------------------------------------
def main():
    demo = "--demo" in sys.argv
    now = datetime.now(timezone.utc)
    print(f"Daily run — {now.astimezone(TZ):%Y-%m-%d %H:%M %Z}" + ("  [DEMO]" if demo else ""))

    if demo:
        raw = demo_articles(now)
    else:
        raw = []
        for feed in CONFIG["feeds"]:
            raw += fetch_feed(feed)

    if len(raw) < 5 and not demo:
        # Failure fallback: keep yesterday's site rather than publishing an empty page.
        print("Too few articles fetched — keeping the previous digest (stale banner will show).")
        sys.exit(0)

    articles = select(dedup(raw), now)
    ai_summaries(articles)
    render(articles, now, demo=demo)


if __name__ == "__main__":
    main()
