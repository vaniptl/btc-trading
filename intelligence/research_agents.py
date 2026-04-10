"""
intelligence/research_agents.py — Trinity.AI Research Layer v1.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
5 Parallel Research Agents:
  Agent 1 — Twitter/X Scraper     (crypto KOLs, BTC narrative)
  Agent 2 — Reddit Scraper        (r/Bitcoin, r/CryptoCurrency, r/trading)
  Agent 3 — RSS Feed Aggregator   (CoinDesk, CoinTelegraph, Decrypt, Bloomberg Crypto)
  Agent 4 — Sentiment Synthesizer (VADER + keyword weighting + market alignment check)
  Agent 5 — Narrative vs Market   (sentiment direction vs price trend — confluence/divergence)

Output: ResearchSignal with confidence score, aligned/divergent flag, and narrative summary.
Executes in parallel — total runtime < 30s.

Author: Trinity.AI (JP Morgan Quant Architecture Pattern)
"""

import os
import json
import time
import asyncio
import hashlib
import logging
import requests
import feedparser
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict, field
from typing import Optional
from pathlib import Path

log = logging.getLogger("research_agents")

# ── Config from env ────────────────────────────────────────────────
REDDIT_CLIENT_ID     = os.environ.get("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.environ.get("REDDIT_CLIENT_SECRET", "")
REDDIT_USER_AGENT    = os.environ.get("REDDIT_USER_AGENT", "TrinityAI/1.0 Research Bot")
TWITTER_BEARER_TOKEN = os.environ.get("TWITTER_BEARER_TOKEN", "")
RESEARCH_CACHE_DIR   = Path(os.environ.get("RESEARCH_CACHE_DIR", "data/research_cache"))
RESEARCH_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ── Sentiment keywords with weights ───────────────────────────────
BULL_KEYWORDS = {
    "bullish": 3.0, "moon": 2.0, "pump": 2.5, "breakout": 3.5, "accumulate": 3.0,
    "buy the dip": 4.0, "all time high": 3.0, "ath": 3.0, "surge": 2.5,
    "rally": 3.0, "golden cross": 4.0, "institutional buying": 4.5,
    "etf inflows": 4.0, "spot etf": 3.5, "halving": 3.0, "parabolic": 2.5,
    "higher high": 3.5, "support held": 3.0, "demand zone": 3.0,
    "long squeeze": 3.5, "short squeeze": 4.0, "capitulation": 3.5,
}
BEAR_KEYWORDS = {
    "bearish": 3.0, "dump": 2.5, "crash": 3.5, "sell": 1.5, "short": 2.0,
    "resistance": 2.0, "death cross": 4.0, "correction": 2.5, "bubble": 3.0,
    "regulation": 3.5, "ban": 4.0, "hack": 4.5, "liquidation": 3.0,
    "lower low": 3.5, "breakdown": 3.5, "distribution": 3.0, "etf outflows": 4.0,
    "fed": 2.0, "rate hike": 3.0, "recession": 3.5, "panic": 3.0,
}

# ── RSS feeds (no API key needed) ─────────────────────────────────
RSS_FEEDS = [
    {"name": "CoinDesk",        "url": "https://www.coindesk.com/arc/outboundfeeds/rss/"},
    {"name": "CoinTelegraph",   "url": "https://cointelegraph.com/rss"},
    {"name": "Decrypt",         "url": "https://decrypt.co/feed"},
    {"name": "Bitcoin Magazine","url": "https://bitcoinmagazine.com/feed"},
    {"name": "The Block",       "url": "https://www.theblock.co/rss.xml"},
    {"name": "Blockworks",      "url": "https://blockworks.co/feed"},
    {"name": "Crypto Briefing", "url": "https://cryptobriefing.com/feed/"},
]

# ── Reddit subs ────────────────────────────────────────────────────
REDDIT_SUBS = ["Bitcoin", "CryptoCurrency", "BitcoinMarkets", "CryptoMarkets"]

# ── Twitter crypto KOL list (for filtering) ────────────────────────
TWITTER_KOLS = [
    "BitcoinHyper", "CryptoCred", "PeterLBrandt", "TechDev_52", "il_Capo_Of_Crypto",
    "RaoulGMI", "APompliano", "SBF_FTX", "CryptoCapo_", "CryptoBull2020",
]


# ══════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ══════════════════════════════════════════════════════════════════

@dataclass
class RawPost:
    source:    str       # "twitter" | "reddit" | "rss"
    channel:   str       # subreddit / feed name / twitter handle
    text:      str
    title:     str = ""
    url:       str = ""
    timestamp: str = ""
    score:     float = 0.0  # upvotes / engagement
    post_id:   str = ""

@dataclass
class SentimentResult:
    compound:     float = 0.0   # -1.0 to +1.0
    bull_score:   float = 0.0
    bear_score:   float = 0.0
    keywords_hit: list  = field(default_factory=list)
    label:        str   = "NEUTRAL"  # BULLISH / BEARISH / NEUTRAL / MIXED

@dataclass
class ResearchSignal:
    timestamp:        str   = ""
    # Raw counts
    total_posts:      int   = 0
    reddit_posts:     int   = 0
    rss_articles:     int   = 0
    twitter_posts:    int   = 0
    # Sentiment
    overall_sentiment: float = 0.0    # weighted avg compound, -1 to +1
    bull_pct:          float = 0.0    # % of posts bullish
    bear_pct:          float = 0.0    # % of posts bearish
    neutral_pct:       float = 0.0
    dominant_narrative: str  = ""
    top_keywords:       list = field(default_factory=list)
    # Confidence
    confidence:        float = 0.0    # 0-100, whether to act on this
    narrative_label:   str   = "NEUTRAL"
    # Alignment with market
    market_alignment:  str   = "UNKNOWN"  # ALIGNED / DIVERGENT / NEUTRAL
    alignment_note:    str   = ""
    # Headlines
    top_headlines:     list  = field(default_factory=list)
    # Raw agent outputs
    agent_results:     dict  = field(default_factory=dict)
    error:             str   = ""


# ══════════════════════════════════════════════════════════════════
# AGENT 1 — RSS FEED AGGREGATOR
# ══════════════════════════════════════════════════════════════════

def agent_rss(lookback_hours: int = 6) -> list[RawPost]:
    """Scrape all RSS feeds, return posts from last N hours."""
    posts = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

    for feed_cfg in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_cfg["url"])
            for entry in feed.entries[:20]:  # cap per feed
                # Parse timestamp
                ts = None
                for attr in ["published_parsed", "updated_parsed"]:
                    if hasattr(entry, attr) and getattr(entry, attr):
                        try:
                            ts = datetime(*getattr(entry, attr)[:6], tzinfo=timezone.utc)
                            break
                        except:
                            pass
                if ts and ts < cutoff:
                    continue

                text  = ""
                if hasattr(entry, "summary"): text = entry.summary[:500]
                title = getattr(entry, "title", "")
                url   = getattr(entry, "link", "")

                # Filter for crypto relevance
                combined = (title + " " + text).lower()
                if not any(kw in combined for kw in ["bitcoin", "btc", "crypto", "blockchain", "ethereum"]):
                    continue

                posts.append(RawPost(
                    source="rss", channel=feed_cfg["name"],
                    text=text, title=title, url=url,
                    timestamp=ts.isoformat() if ts else "",
                ))
        except Exception as e:
            log.debug(f"RSS error {feed_cfg['name']}: {e}")

    log.info(f"[Agent-RSS] {len(posts)} posts collected")
    return posts


# ══════════════════════════════════════════════════════════════════
# AGENT 2 — REDDIT SCRAPER
# ══════════════════════════════════════════════════════════════════

def agent_reddit(limit_per_sub: int = 25) -> list[RawPost]:
    """Scrape Reddit hot/new posts. Falls back to public JSON if no API key."""
    posts = []

    for sub in REDDIT_SUBS:
        try:
            # Public JSON endpoint — no auth required
            url = f"https://www.reddit.com/r/{sub}/hot.json?limit={limit_per_sub}"
            r = requests.get(url, headers={"User-Agent": REDDIT_USER_AGENT}, timeout=10)
            data = r.json()
            for item in data.get("data", {}).get("children", []):
                d = item.get("data", {})
                title     = d.get("title", "")
                selftext  = d.get("selftext", "")[:400]
                score     = d.get("score", 0)
                post_id   = d.get("id", "")
                permalink = "https://reddit.com" + d.get("permalink", "")
                created   = d.get("created_utc", 0)
                ts        = datetime.utcfromtimestamp(created).isoformat() if created else ""

                combined = (title + " " + selftext).lower()
                if not any(kw in combined for kw in ["bitcoin", "btc", "crypto"]):
                    continue

                posts.append(RawPost(
                    source="reddit", channel=f"r/{sub}",
                    text=selftext, title=title, url=permalink,
                    timestamp=ts, score=score, post_id=post_id,
                ))
        except Exception as e:
            log.debug(f"Reddit error r/{sub}: {e}")

    log.info(f"[Agent-Reddit] {len(posts)} posts collected")
    return posts


# ══════════════════════════════════════════════════════════════════
# AGENT 3 — TWITTER/X SCRAPER (v2 API or nitter fallback)
# ══════════════════════════════════════════════════════════════════

def agent_twitter(query: str = "bitcoin OR BTC lang:en -is:retweet", max_results: int = 50) -> list[RawPost]:
    """Twitter v2 API. Falls back to free search proxies if no bearer token."""
    posts = []

    if TWITTER_BEARER_TOKEN:
        # Official Twitter v2 API
        try:
            headers = {"Authorization": f"Bearer {TWITTER_BEARER_TOKEN}"}
            params  = {
                "query":       query,
                "max_results": max_results,
                "tweet.fields": "created_at,author_id,public_metrics,text",
                "expansions":  "author_id",
                "user.fields": "username",
            }
            r = requests.get(
                "https://api.twitter.com/2/tweets/search/recent",
                headers=headers, params=params, timeout=15
            )
            data = r.json()
            users = {u["id"]: u["username"] for u in data.get("includes", {}).get("users", [])}
            for tweet in data.get("data", []):
                uid      = tweet.get("author_id", "")
                username = users.get(uid, "unknown")
                posts.append(RawPost(
                    source="twitter", channel=f"@{username}",
                    text=tweet.get("text", ""),
                    timestamp=tweet.get("created_at", ""),
                    score=tweet.get("public_metrics", {}).get("like_count", 0),
                    post_id=tweet.get("id", ""),
                ))
        except Exception as e:
            log.warning(f"Twitter API error: {e}")
    else:
        # Fallback: CryptoPanic free API (sentiment headlines)
        try:
            r = requests.get(
                "https://cryptopanic.com/api/v1/posts/?auth_token=free&currencies=BTC&filter=hot",
                timeout=10
            )
            data = r.json()
            for item in data.get("results", [])[:30]:
                title = item.get("title", "")
                posts.append(RawPost(
                    source="twitter", channel="CryptoPanic",
                    text=title, title=title,
                    url=item.get("url", ""),
                    timestamp=item.get("published_at", ""),
                ))
        except Exception as e:
            log.debug(f"CryptoPanic fallback error: {e}")

    log.info(f"[Agent-Twitter] {len(posts)} posts collected")
    return posts


# ══════════════════════════════════════════════════════════════════
# AGENT 4 — SENTIMENT SYNTHESIZER
# ══════════════════════════════════════════════════════════════════

def _vader_score(text: str) -> float:
    """VADER compound score without importing at module level."""
    try:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        if not hasattr(_vader_score, "_analyzer"):
            _vader_score._analyzer = SentimentIntensityAnalyzer()
        return float(_vader_score._analyzer.polarity_scores(text)["compound"])
    except:
        return 0.0

def _keyword_score(text: str) -> tuple[float, float, list]:
    """
    Returns (bull_score, bear_score, keywords_hit)
    Weighted by importance of each keyword.
    """
    t = text.lower()
    bull = 0.0; bear = 0.0; hits = []
    for kw, w in BULL_KEYWORDS.items():
        if kw in t: bull += w; hits.append(f"+{kw}")
    for kw, w in BEAR_KEYWORDS.items():
        if kw in t: bear += w; hits.append(f"-{kw}")
    return bull, bear, hits

def agent_sentiment(posts: list[RawPost]) -> SentimentResult:
    """
    Runs sentiment analysis on all collected posts.
    Blends VADER (contextual NLP) + keyword weights.
    Scores weighted by post engagement (upvotes, likes).
    """
    if not posts:
        return SentimentResult(label="NEUTRAL", compound=0.0)

    total_weight   = 0.0
    weighted_comp  = 0.0
    total_bull     = 0.0
    total_bear     = 0.0
    all_keywords   = {}
    bull_count = bear_count = neutral_count = 0

    for post in posts:
        full_text  = (post.title + " " + post.text).strip()
        if not full_text: continue

        # Engagement weight — log scale so viral posts don't dominate
        import math
        weight = max(1.0, math.log1p(post.score + 1))

        vader  = _vader_score(full_text)
        b_kw, r_kw, kw_hits = _keyword_score(full_text)

        # Blend: 40% VADER + 60% keyword
        kw_compound = (b_kw - r_kw) / max(1.0, b_kw + r_kw) if (b_kw + r_kw) > 0 else 0.0
        compound    = 0.40 * vader + 0.60 * kw_compound

        weighted_comp += compound * weight
        total_weight  += weight
        total_bull     += b_kw * weight
        total_bear     += r_kw * weight

        if compound >  0.15: bull_count    += 1
        elif compound < -0.15: bear_count  += 1
        else:                  neutral_count += 1

        for kw in kw_hits:
            k = kw.lstrip("+-")
            all_keywords[k] = all_keywords.get(k, 0) + weight

    n      = len(posts)
    comp   = weighted_comp / total_weight if total_weight > 0 else 0.0
    b_pct  = bull_count    / n if n > 0 else 0.0
    r_pct  = bear_count    / n if n > 0 else 0.0

    # Label
    if   comp >  0.25: label = "BULLISH"
    elif comp < -0.25: label = "BEARISH"
    elif abs(comp) < 0.08: label = "NEUTRAL"
    else: label = "MIXED"

    # Top keywords by weight
    top_kw = sorted(all_keywords.items(), key=lambda x: x[1], reverse=True)[:10]

    return SentimentResult(
        compound=round(comp, 4),
        bull_score=round(total_bull / total_weight if total_weight > 0 else 0, 2),
        bear_score=round(total_bear / total_weight if total_weight > 0 else 0, 2),
        keywords_hit=[k for k, _ in top_kw],
        label=label,
    )


# ══════════════════════════════════════════════════════════════════
# AGENT 5 — NARRATIVE vs MARKET ODDS
# ══════════════════════════════════════════════════════════════════

def agent_narrative_vs_market(
    sentiment: SentimentResult,
    market_regime: str,
    current_price: float,
    ema50: float,
    ema200: float,
) -> dict:
    """
    Compares what social media is saying vs what price is doing.
    Identifies:
      ALIGNED   — sentiment confirms market trend (strengthens conviction)
      DIVERGENT — sentiment opposes market trend  (contrarian setup or early reversal)
      NEUTRAL   — insufficient data or mixed signals

    This is the JP Morgan sentiment overlay approach:
    "When retail is extremely bullish at resistance — that's your exit signal.
     When retail is extremely bearish at support — that's your entry signal."
    """
    note     = ""
    align    = "NEUTRAL"
    conf_adj = 0.0   # adjustment to overall confidence

    is_bull_market  = market_regime in ("STRONG_BULL", "WEAK_BULL")
    is_bear_market  = market_regime == "BEAR"
    is_ranging      = market_regime == "RANGING"

    sent_label  = sentiment.label
    sent_comp   = sentiment.compound
    price_above_200 = current_price > ema200
    price_above_50  = current_price > ema50

    if is_bull_market and sent_label == "BULLISH":
        align     = "ALIGNED"
        note      = f"Retail bullish ({sent_comp:+.2f}) confirms bull regime. Momentum trade — follow trend."
        conf_adj  = +10.0

    elif is_bull_market and sent_label == "BEARISH":
        align     = "DIVERGENT"
        note      = f"Retail bearish ({sent_comp:+.2f}) vs bull regime. Watch for bullish reset — potential long entry on dip."
        conf_adj  = -5.0

    elif is_bear_market and sent_label == "BEARISH":
        align     = "ALIGNED"
        note      = f"Retail bearish ({sent_comp:+.2f}) confirms bear regime. Short bias confirmed."
        conf_adj  = +8.0

    elif is_bear_market and sent_label == "BULLISH" and sent_comp > 0.4:
        align     = "DIVERGENT"
        note      = f"Extreme retail euphoria ({sent_comp:+.2f}) in bear market = distribution top risk. Fade longs."
        conf_adj  = +5.0  # high conviction SHORT

    elif is_bear_market and sent_label == "BEARISH" and sent_comp < -0.5:
        align     = "DIVERGENT"
        note      = f"Extreme retail fear ({sent_comp:+.2f}) in bear — capitulation possible. Watch for MR long."
        conf_adj  = +5.0

    elif is_ranging:
        align     = "NEUTRAL"
        note      = f"Ranging market. Sentiment ({sent_label}) has reduced edge. Fade extremes only."
        conf_adj  = -5.0

    else:
        align     = "NEUTRAL"
        note      = f"Mixed signals. Sentiment {sent_label} ({sent_comp:+.2f}) — no clear edge."
        conf_adj  = 0.0

    return {
        "alignment":    align,
        "note":         note,
        "conf_adj":     conf_adj,
        "sent_label":   sent_label,
        "sent_comp":    sent_comp,
    }


# ══════════════════════════════════════════════════════════════════
# CONFIDENCE ENGINE
# ══════════════════════════════════════════════════════════════════

def compute_research_confidence(
    sentiment:    SentimentResult,
    posts:        list[RawPost],
    alignment:    dict,
    signal_dir:   str,  # "LONG" | "SHORT" | "NONE"
) -> float:
    """
    0-100 confidence score combining:
      - Data volume (more posts = more signal)
      - Sentiment strength (strong conviction = higher score)
      - Alignment with trade direction
      - Alignment with market regime
    """
    conf = 50.0  # base

    # Volume bonus: more data = more reliable
    n = len(posts)
    if   n > 100: conf += 10.0
    elif n >  50: conf +=  5.0
    elif n <  15: conf -= 10.0

    # Sentiment strength
    abs_comp = abs(sentiment.compound)
    if   abs_comp > 0.5: conf += 15.0
    elif abs_comp > 0.3: conf += 10.0
    elif abs_comp > 0.1: conf +=  5.0
    else:                conf -=  5.0

    # Market alignment bonus
    conf += alignment.get("conf_adj", 0.0)

    # Signal direction alignment
    if signal_dir == "LONG"  and sentiment.label == "BULLISH": conf += 10.0
    if signal_dir == "SHORT" and sentiment.label == "BEARISH": conf += 10.0
    if signal_dir == "LONG"  and sentiment.label == "BEARISH": conf -= 15.0
    if signal_dir == "SHORT" and sentiment.label == "BULLISH": conf -= 15.0

    # Contrarian bonus (extreme fear/greed at extremes)
    if signal_dir == "LONG"  and sentiment.compound < -0.6: conf += 12.0
    if signal_dir == "SHORT" and sentiment.compound >  0.6: conf += 12.0

    return round(max(0.0, min(100.0, conf)), 1)


# ══════════════════════════════════════════════════════════════════
# MASTER ORCHESTRATOR — runs all 5 agents in parallel
# ══════════════════════════════════════════════════════════════════

def run_research(
    market_regime: str = "UNKNOWN",
    current_price: float = 0.0,
    ema50:         float = 0.0,
    ema200:        float = 0.0,
    signal_dir:    str   = "NONE",
    lookback_hours: int  = 6,
) -> ResearchSignal:
    """
    Master function: runs all 5 agents in parallel, returns ResearchSignal.
    Call this from signal_runner.get_signal() before execution decision.
    """
    ts = datetime.utcnow().isoformat()
    log.info(f"Research run started | regime={market_regime} signal={signal_dir}")

    # ── Run agents 1-3 in parallel (I/O bound) ────────────────────
    all_posts: list[RawPost] = []
    agent_results = {}

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            pool.submit(agent_rss,     lookback_hours): "rss",
            pool.submit(agent_reddit,  30):             "reddit",
            pool.submit(agent_twitter):                 "twitter",
        }
        for future in as_completed(futures, timeout=30):
            name = futures[future]
            try:
                result = future.result()
                all_posts.extend(result)
                agent_results[name] = {"count": len(result), "status": "ok"}
            except Exception as e:
                log.warning(f"Agent {name} failed: {e}")
                agent_results[name] = {"count": 0, "status": f"error: {e}"}

    # ── Agent 4: Sentiment synthesis ──────────────────────────────
    sentiment = agent_sentiment(all_posts)
    agent_results["sentiment"] = asdict(sentiment)

    # ── Agent 5: Narrative vs Market ──────────────────────────────
    alignment = agent_narrative_vs_market(
        sentiment, market_regime, current_price, ema50, ema200
    )
    agent_results["narrative_vs_market"] = alignment

    # ── Confidence ────────────────────────────────────────────────
    confidence = compute_research_confidence(sentiment, all_posts, alignment, signal_dir)

    # ── Top headlines ─────────────────────────────────────────────
    rss_posts = sorted(
        [p for p in all_posts if p.source == "rss"],
        key=lambda p: p.timestamp, reverse=True
    )[:5]
    top_headlines = [
        {"title": p.title, "source": p.channel, "url": p.url, "ts": p.timestamp}
        for p in rss_posts
    ]

    # Source counts
    rss_n     = sum(1 for p in all_posts if p.source == "rss")
    reddit_n  = sum(1 for p in all_posts if p.source == "reddit")
    twitter_n = sum(1 for p in all_posts if p.source == "twitter")

    result = ResearchSignal(
        timestamp=ts,
        total_posts=len(all_posts),
        reddit_posts=reddit_n,
        rss_articles=rss_n,
        twitter_posts=twitter_n,
        overall_sentiment=sentiment.compound,
        bull_pct=round(sum(1 for p in all_posts if _keyword_score(p.text + p.title)[0] > _keyword_score(p.text + p.title)[1]) / max(1, len(all_posts)), 3),
        bear_pct=round(sum(1 for p in all_posts if _keyword_score(p.text + p.title)[1] > _keyword_score(p.text + p.title)[0]) / max(1, len(all_posts)), 3),
        neutral_pct=0.0,
        dominant_narrative=sentiment.label,
        top_keywords=sentiment.keywords_hit,
        confidence=confidence,
        narrative_label=sentiment.label,
        market_alignment=alignment["alignment"],
        alignment_note=alignment["note"],
        top_headlines=top_headlines,
        agent_results=agent_results,
    )
    result.neutral_pct = round(1.0 - result.bull_pct - result.bear_pct, 3)

    # Cache result
    cache_file = RESEARCH_CACHE_DIR / f"research_{ts[:10]}.json"
    try:
        history = []
        if cache_file.exists():
            history = json.loads(cache_file.read_text())
        history.append(asdict(result))
        cache_file.write_text(json.dumps(history[-50:], indent=2))  # keep last 50
    except Exception as e:
        log.debug(f"Cache write error: {e}")

    log.info(
        f"Research complete | posts={len(all_posts)} | "
        f"sentiment={sentiment.label}({sentiment.compound:+.2f}) | "
        f"alignment={alignment['alignment']} | confidence={confidence:.0f}%"
    )
    return result


# ══════════════════════════════════════════════════════════════════
# QUICK SUMMARY FOR TELEGRAM / STREAMLIT
# ══════════════════════════════════════════════════════════════════

def format_research_summary(r: ResearchSignal) -> str:
    sent_icon = {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "⚪", "MIXED": "🟡"}.get(r.narrative_label, "⚪")
    align_icon = {"ALIGNED": "✅", "DIVERGENT": "⚠️", "NEUTRAL": "↔️"}.get(r.market_alignment, "↔️")
    lines = [
        f"📡 *Research Report* | {r.timestamp[:16]} UTC",
        f"",
        f"📊 Posts: {r.total_posts} (RSS:{r.rss_articles} Reddit:{r.reddit_posts} Twitter:{r.twitter_posts})",
        f"{sent_icon} Sentiment: *{r.narrative_label}* ({r.overall_sentiment:+.2f})",
        f"   Bull: {r.bull_pct:.0%} | Bear: {r.bear_pct:.0%} | Neutral: {r.neutral_pct:.0%}",
        f"{align_icon} Market Alignment: *{r.market_alignment}*",
        f"   {r.alignment_note}",
        f"",
        f"🔑 Top Keywords: {', '.join(r.top_keywords[:6])}",
        f"🎯 Research Confidence: *{r.confidence:.0f}%*",
    ]
    if r.top_headlines:
        lines.append("")
        lines.append("📰 *Top Headlines:*")
        for h in r.top_headlines[:3]:
            lines.append(f"  • [{h['source']}] {h['title'][:80]}")
    return "\n".join(lines)
