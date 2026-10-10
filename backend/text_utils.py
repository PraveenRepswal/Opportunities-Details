"""Unified text processing, normalization, sentence boundary detection, and title sanitization."""
from __future__ import annotations

import re
import unicodedata
from typing import List

TITLE_CLICKBAIT_PREFIXES = re.compile(
    r"^(?:applications?\s+(?:are\s+)?(?:now\s+)?open(?:\s+for)?|apply\s+now(?:\s+for)?|call\s+for\s+applications(?:\s+for)?|call\s+for\s+|opportunity\s+alert:?)\s*",
    re.IGNORECASE,
)

KNOWN_ACRONYMS = {
    "who", "unesco", "un", "cern", "mit", "daad", "anz", "eit", "cips",
    "usaid", "fmi", "epfl", "eth", "kaust", "gist", "ucl", "oxford", "cambridge",
    "usa", "stem", "iphyc", "drph", "phd", "ielts", "toefl", "gre", "gmat",
    "eu", "asean", "undp", "unicef", "oas", "au", "wmo", "itu", "iaea"
}

ABBR_PATTERN = re.compile(
    r"\b(?:U\.S\.A|U\.S|U\.K|Dr|Mr|Mrs|Ms|Prof|Sr|Jr|vs|etc|e\.g|i\.e|No|Vol|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec|Ph\.D|B\.Sc|M\.Sc|M\.D|B\.A|M\.A)\.",
    re.IGNORECASE,
)
INITIAL_PATTERN = re.compile(r"\b[A-Z]\.")
DECIMAL_PATTERN = re.compile(r"\b\d+\.\d+\b")


def fix_mojibake(text: str) -> str:
    """Repair encoding artifacts and smart punctuation while preserving line breaks."""
    if not text:
        return ""

    # 1. Normalize smart quotes, dashes, and whitespace artifacts
    text = text.replace("\u2018", "'").replace("\u2019", "'").replace("\u201b", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"').replace("\u201f", '"')
    text = text.replace("\u2013", "-").replace("\u2014", "-").replace("\u2015", "-")
    text = text.replace("\u00a0", " ").replace("\u200b", "").replace("\ufeff", "")

    # 2. Smart handling of unicode replacement character (\ufffd)
    # Between letters (e.g. McDonald\ufffds, Africa\ufffds) -> '
    text = re.sub(r"(?<=[a-zA-Z])\ufffd(?=[a-zA-Z])", "'", text)
    # Between digits (e.g. 2027\ufffd2028) -> -
    text = re.sub(r"(?<=\d)\ufffd(?=\d)", "-", text)
    # At word boundaries -> '
    text = text.replace("\ufffd", "'")

    # 3. Unicode compatibility decomposition/composition
    return unicodedata.normalize("NFKC", text)


def normalize_text(text: str) -> str:
    """Normalize text encoding, strip mojibake, convert smart punctuation and collapse whitespace."""
    return " ".join(fix_mojibake(text).split())


def split_into_sentences(text: str) -> List[str]:
    """Rule-based sentence splitter that safely ignores abbreviations, initials, decimals, and URLs."""
    if not text:
        return []

    # Protect decimals (e.g. $1.5M, 3.8 GPA)
    protected = DECIMAL_PATTERN.sub(lambda m: m.group(0).replace(".", "<DOT>"), text)
    # Protect abbreviations (e.g. U.S., Dr., Ph.D.)
    protected = ABBR_PATTERN.sub(lambda m: m.group(0).replace(".", "<DOT>"), protected)
    # Protect initials (e.g. J. K. Rowling)
    protected = INITIAL_PATTERN.sub(lambda m: m.group(0).replace(".", "<DOT>"), protected)

    # Split on sentence boundaries
    raw_sentences = re.split(r"(?<=[.!?])\s+", protected)
    sentences = []
    for s in raw_sentences:
        s = s.replace("<DOT>", ".").strip()
        if s:
            sentences.append(s)
    return sentences


def clean_opportunity_title(title: str) -> str:
    """Fast, case-aware cleaner for opportunity titles.

    - Strips clickbait prefixes ('Apply Now', 'Call for Applications').
    - Preserves mixed-casing acronyms ('IPhyC', 'USA', 'STEM', 'PhD', 'McDonald\\'s').
    - Only title-cases if the source string is purely UPPERCASE or purely lowercase.
    """
    if not title:
        return "Untitled Opportunity"

    title = normalize_text(title).strip()
    title = TITLE_CLICKBAIT_PREFIXES.sub("", title).strip()
    if not title:
        return "Untitled Opportunity"

    words = title.split()
    is_all_upper = title.isupper()
    is_all_lower = title.islower()

    if is_all_upper or is_all_lower:
        minor_words = {"a", "an", "the", "and", "but", "or", "for", "nor", "on", "at", "to", "from", "by", "over", "of", "in", "with"}
        res = []
        for i, w in enumerate(words):
            w_strip = re.sub(r"[^\w]", "", w)
            w_lower = w_strip.lower()
            if w_lower in KNOWN_ACRONYMS:
                res.append(re.sub(r"(?i)" + re.escape(w_strip), w_lower.upper(), w))
            elif i > 0 and w_lower in minor_words:
                res.append(w_lower)
            else:
                res.append(w.capitalize())
        return " ".join(res).strip()

    # Already has mixed casing: preserve author casing, ensure first char capitalized, uppercase known acronyms
    res = []
    for i, w in enumerate(words):
        w_strip = re.sub(r"[^\w]", "", w)
        w_lower = w_strip.lower()
        if w_lower in KNOWN_ACRONYMS:
            res.append(re.sub(r"(?i)" + re.escape(w_strip), w_lower.upper(), w))
        elif i == 0 and w and w[0].islower():
            res.append(w[0].upper() + w[1:])
        else:
            res.append(w)

    return " ".join(res).strip()


def clean_opportunity_text(text: str) -> str:
    """Clean scraped opportunity text by removing promotional links, app ads, and excessive whitespace."""
    if not text:
        return ""
    # 1. Remove messaging/social channel links (m.me, t.me, chat.whatsapp.com, etc.)
    text = re.sub(r"https?://(?:m\.me|t\.me|chat\.whatsapp\.com|bit\.ly)/\S+", "", text)
    # 2. Remove "Youth Opportunities invites you to join..." or "join our channel..."
    text = re.sub(
        r"(?:Youth Opportunities invites you to join|join (?:your|our) (?:preferred )?channel|get the latest updates directly)[^.\n]*?(?:\.|\n|:|$)",
        "",
        text,
        flags=re.IGNORECASE,
    )
    # 3. Remove "Get the Youth Opportunities iOS App..." or "Download the ... App"
    text = re.sub(
        r"(?:Get|Download)\s+the\s+[^.\n]+?App\s+here[^.\n]*?(?:\.|\n|$)",
        "",
        text,
        flags=re.IGNORECASE,
    )
    # 4. Remove "FOLLOW US..." and promotional sharing boilerplate
    text = re.sub(r"FOLLOW US.*?(?=\bCall\b|\bThe\b|[A-Z])", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"Please share (?:this|these) opportunit(?:y|ies)[^.\n]*?(?:\.|\n|$)", "", text, flags=re.IGNORECASE)
    # 5. Clean repeated punctuation and artifact symbols
    text = re.sub(r"!{2,}", "!", text)
    return normalize_text(text)


def extract_lead_summary(content: str, max_sentences: int = 2, max_chars: int = 420) -> str:
    """Extract a concise, grammatically complete summary from raw opportunity text.

    Guarantees:
    - Never breaks mid-sentence.
    - Always ends on a complete punctuation mark.
    - Clamped to 1-2 sentences within `max_chars` budget (~70-90 tokens).
    """
    if not content:
        return "Details available in the full announcement."

    # Strip promotional boilerplate
    cleaned = clean_opportunity_text(content)
    cleaned = normalize_text(cleaned)

    sentences = split_into_sentences(cleaned)
    if not sentences:
        return "Details available in the full announcement."

    collected = []
    cur_len = 0
    for s in sentences[:max_sentences]:
        if cur_len + len(s) > max_chars and collected:
            break
        collected.append(s)
        cur_len += len(s) + 1

    summary = " ".join(collected).strip()
    if not summary:
        summary = sentences[0][:max_chars].rsplit(" ", 1)[0]
        if not summary.endswith((".", "!", "?")):
            summary += "."

    return summary


def strip_thinking_tags(text: str) -> str:
    """Strip out <think>...</think> XML blocks and [[THINK]] markers from response."""
    if not text:
        return ""
    if "</think>" in text and "<think>" not in text:
        text = text.split("</think>")[-1]
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    if "<think>" in cleaned:
        cleaned = re.sub(r"<think>.*", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"\[\[THINK\]\].*?(?:\n|$)", "", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


try:
    from langdetect import DetectorFactory, detect
    DetectorFactory.seed = 0
    LANGDETECT_AVAILABLE = True
except ImportError:
    LANGDETECT_AVAILABLE = False


def is_english_content(title: str = "", content: str = "") -> bool:
    """Check if opportunity title and content are in English using langdetect.

    Uses title and a generous content sample for reliable detection.
    Returns True for English text, False for non-English (French, Spanish, etc.).
    Falls back to True if langdetect is unavailable or on ambiguous short text.
    """
    clean_t = normalize_text(title or "")
    clean_c = normalize_text(content or "")[:1000]
    sample = f"{clean_t} {clean_c}".strip()
    if not sample:
        return True
    if not LANGDETECT_AVAILABLE:
        return True
    try:
        lang = detect(sample)
        return lang == "en"
    except Exception:
        ascii_chars = sum(1 for c in sample if ord(c) < 128)
        return (ascii_chars / max(len(sample), 1)) >= 0.8

