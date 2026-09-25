import math
from datetime import datetime, timezone

CRITERIA = {
    "teaching": "Teaching quality",
    "correctness": "Correctness",
    "coverage": "Covers the lesson",
    "visual": "Visual explanation",
    "comments": "What viewers say",
    "outperformance": "Beats its channel's usual reach",
    "like_rate": "Like rate",
    "velocity": "Views per day",
    "comment_rate": "Comment rate",
    "freshness": "Freshness",
}

PROFILES = {
    "balanced": {"teaching": .22, "correctness": .12, "coverage": .10, "visual": .18, "comments": .18,
                 "outperformance": .07, "like_rate": .05, "velocity": .03, "comment_rate": .02, "freshness": .03},
    "visual": {"teaching": .18, "correctness": .10, "coverage": .09, "visual": .30, "comments": .15,
               "outperformance": .06, "like_rate": .05, "velocity": .03, "comment_rate": .02, "freshness": .02},
    "rigor": {"teaching": .22, "correctness": .22, "coverage": .15, "visual": .10, "comments": .15,
              "outperformance": .06, "like_rate": .04, "velocity": .02, "comment_rate": .02, "freshness": .02},
}

PROFILE_LABELS = {"balanced": "Balanced", "visual": "Visuals first", "rigor": "Rigor first"}


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def age_days(upload_date: str | None) -> float:
    try:
        d = datetime.strptime(upload_date, "%Y%m%d").replace(tzinfo=timezone.utc)
        return max(1.0, (datetime.now(timezone.utc) - d).total_seconds() / 86400)
    except Exception:
        return 365.0


def audience(meta: dict) -> tuple[dict, dict]:
    views = meta.get("view_count") or 0
    subs = meta.get("channel_follower_count") or 0
    likes = meta.get("like_count")
    comments = meta.get("comment_count")
    days = age_days(meta.get("upload_date"))

    ratio = views / max(subs, 1000)
    outperf = clamp((math.log10(max(ratio, 1e-3)) + 1.5) / 3)
    vpd = views / days
    velocity = clamp(math.log10(vpd + 1) / 4)
    like_rate = (likes / views) if (likes and views) else None
    comment_rate = (comments / views) if (comments and views) else None

    signals = {
        "outperformance": outperf,
        "velocity": velocity,
        "like_rate": clamp(like_rate / 0.05) if like_rate is not None else 0.4,
        "comment_rate": clamp(comment_rate / 0.006) if comment_rate is not None else 0.3,
        "freshness": clamp(1 - (days / 365) / 8, 0.2, 1.0),
    }
    raw = {"views": views, "subscribers": subs, "likes": likes, "comments": comments,
           "age_days": round(days), "views_per_day": round(vpd), "views_per_sub": round(ratio, 2),
           "like_rate_pct": round(like_rate * 100, 2) if like_rate is not None else None}
    return signals, raw


def prelim(signals: dict, position: int) -> float:
    return (0.45 * signals["outperformance"] + 0.25 * signals["like_rate"]
            + 0.15 * signals["velocity"] + 0.15 * clamp(1 - position / 12))


def combine(signals: dict, judge: dict, profile: str) -> tuple[float, dict]:
    weights = PROFILES.get(profile, PROFILES["balanced"])

    def j(k, default=5.0):
        try:
            return clamp(float(judge.get(k, default)) / 10)
        except Exception:
            return default / 10

    vals = {
        "teaching": j("teaching"), "correctness": j("correctness"), "coverage": j("coverage"),
        "visual": j("visual"), "comments": j("comment_evidence"),
        **{k: signals[k] for k in ("outperformance", "like_rate", "velocity", "comment_rate", "freshness")},
    }
    contributions = {k: round(weights[k] * vals[k] * 100, 2) for k in weights}
    return round(sum(contributions.values()), 1), contributions
