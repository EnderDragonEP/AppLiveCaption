"""Filters for Whisper hallucinations.

Whisper tends to produce stock phrases (often from YouTube subtitle credits)
on silence or noise, and to loop on a phrase. These helpers drop such output.
"""

from __future__ import annotations

import re
import time
import unicodedata

from .backend import Transcript, zlib_compression_ratio

# Matched against the normalised text (lower case, no spaces/punctuation).
# A whole-text match drops the line; the "contains" list drops it if the
# phrase appears anywhere, because those never occur in real speech.
_EXACT = {
    "thankyouforwatching", "thanksforwatching", "thankyouforwatchingandseeyounexttime",
    "thankyousomuchforwatching", "pleasesubscribe", "pleaselikeandsubscribe",
    "likeandsubscribe", "subscribetomychannel", "dontforgettosubscribe",
    "seeyouinthenextvideo", "seeyounexttime", "thankyouforlistening", "byebye",
    "you", "thankyou", "bye", "music", "applause", "silence",
    "謝謝觀看", "谢谢观看", "謝謝收看", "谢谢收看", "感謝觀看", "感谢观看", "感謝收看", "感谢收看",
    "謝謝大家觀看", "谢谢大家观看", "謝謝大家", "谢谢大家", "請訂閱", "请订阅",
    "ご視聴ありがとうございました", "ありがとうございました",
    "字幕", "中文字幕", "優優獨播劇場", "优优独播剧场",
}
_CONTAINS = (
    re.compile(r"字幕(由|by|提供|製作|制作|組|组|志願者|志愿者)"),
    re.compile(r"(由|by).{0,20}(提供|製作|制作)字幕"),
    re.compile(r"amara\.?org"),
    re.compile(r"點點欄目|点点栏目|明鏡|明镜|不吝點贊|不吝点赞|訂閱轉發|订阅转发|打賞支持|打赏支持"),
    re.compile(r"subtitlesby|captionsby|transcribedby|translatedby|subtitledby"),
)

_PUNCT_SPACE = re.compile(r"[\s\W_]+", re.UNICODE)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    return _PUNCT_SPACE.sub("", text)


def is_stock_phrase(text: str) -> bool:
    norm = normalize(text)
    if not norm:
        return True
    if norm in _EXACT:
        return True
    raw = unicodedata.normalize("NFKC", text).lower().replace(" ", "")
    return any(p.search(raw) or p.search(norm) for p in _CONTAINS)


_REPEAT = re.compile(r"(.{1,30}?)(?:\s*\1){3,}", re.DOTALL)


def collapse_repeats(text: str) -> str:
    """Reduce a phrase repeated 4+ times in a row to two occurrences."""

    def keep_two(m: re.Match) -> str:
        run, unit = m.group(0), m.group(1)
        second = run.find(unit, len(unit))  # keeps the original spacing between the two
        return run[: second + len(unit)]

    return _REPEAT.sub(keep_two, text)


class HallucinationFilter:
    def __init__(self, repeat_window: float = 15.0):
        self.repeat_window = repeat_window
        self._last_norm = ""
        self._last_time = 0.0

    def reset(self) -> None:
        self._last_norm = ""
        self._last_time = 0.0

    def clean(self, tr: Transcript, duration: float, final: bool) -> str:
        """Return the text to show, or "" if it should be dropped."""
        text = collapse_repeats(tr.text.strip())
        if not text:
            return ""
        # Whisper's own "this was not speech" signal.
        if tr.no_speech_prob > 0.6 and tr.avg_logprob < -1.0:
            return ""
        if final and tr.compression_ratio > 2.6 and zlib_compression_ratio(text) > 2.6:
            return ""
        if is_stock_phrase(text):
            # Short stock phrases like "Thank you." can be real speech in a
            # long utterance; only drop them for short or low-confidence audio.
            norm = normalize(text)
            if norm in ("thankyou", "bye", "byebye", "謝謝大家", "谢谢大家") and duration > 1.5 and tr.avg_logprob > -0.5:
                pass
            else:
                return ""
        if final:
            norm = normalize(text)
            now = time.monotonic()
            if norm == self._last_norm and now - self._last_time < self.repeat_window:
                return ""
            self._last_norm = norm
            self._last_time = now
        return text
