"""
Twitch Chat TTS Bot
觀眾在聊天室打 !tts <訊息> 就會用繁中念出來
"""
import asyncio
import os
import re
import tempfile
import time

import edge_tts
import pygame
from lingua import Language, LanguageDetectorBuilder

# ===================== 設定 =====================
CHANNEL = "e0pwr"                      # 你的 Twitch 頻道名（不要加 # 號）
COMMAND_PREFIX = "!tts"
MAX_LENGTH = 300                       # 訊息最大長度，避免被刷
USER_COOLDOWN = 3                      # 每個使用者冷卻秒數
RATE = "+30%"                           # 語速，可以調 "-20%" 或 "+30%"
VOLUME = "+0%"                         # 音量
MIN_LATIN_CHARS_FOR_DETECT = 4         # 拉丁字母少於這個數量就直接當英文
LATIN_CONFIDENCE_THRESHOLD = 0.6       # 最高語言信心 < 此值就回英文（避免短字串誤判）

# 不同語言使用不同 voice
VOICE_MAP = {
    "zh": "zh-TW-HsiaoChenNeural",     # 繁中 — 曉臻
    "ja": "ja-JP-NanamiNeural",        # 日文 — Nanami
    "ko": "ko-KR-SunHiNeural",         # 韓文 — SunHi
    "en": "en-US-AriaNeural",          # 英文 — Aria
    "es": "es-ES-ElviraNeural",        # 西文 — Elvira
    "fr": "fr-FR-DeniseNeural",        # 法文 — Denise
    "pt": "pt-BR-FranciscaNeural",     # 葡文（巴西）— Francisca
    "de": "de-DE-KatjaNeural",         # 德文 — Katja
    "it": "it-IT-ElsaNeural",          # 義文 — Elsa
}
DEFAULT_VOICE = VOICE_MAP["zh"]

# Lingua detector：拉丁字母段落用來分辨 en/es/fr/pt/de/it
_LATIN_LANGS = [
    Language.ENGLISH,
    Language.SPANISH,
    Language.FRENCH,
    Language.PORTUGUESE,
    Language.GERMAN,
    Language.ITALIAN,
]
_LINGUA_TO_VOICE_KEY = {
    Language.ENGLISH: "en",
    Language.SPANISH: "es",
    Language.FRENCH: "fr",
    Language.PORTUGUESE: "pt",
    Language.GERMAN: "de",
    Language.ITALIAN: "it",
}
_latin_detector = (
    LanguageDetectorBuilder.from_languages(*_LATIN_LANGS)
    .with_preloaded_language_models()
    .build()
)
# ================================================

SERVER = "irc.chat.twitch.tv"
PORT = 6667
NICK = "justinfan12345"                # 匿名讀取，不需要 OAuth

tts_queue: asyncio.Queue = asyncio.Queue()
user_last_used: dict[str, float] = {}


def clean_text(text: str) -> str:
    """過濾 URL 和多餘空白"""
    text = re.sub(r"https?://\S+", "網址", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _script_of(c: str) -> str:
    """把單一字元分到 ja / ko / han / latin / neutral 五類。"""
    o = ord(c)
    # 日文假名：平假名 + 片假名 + 半形片假名
    if 0x3040 <= o <= 0x30FF or 0xFF65 <= o <= 0xFF9F:
        return "ja"
    # 韓文：Jamo + Compatibility Jamo + 諺文音節
    if (
        0x1100 <= o <= 0x11FF
        or 0x3130 <= o <= 0x318F
        or 0xAC00 <= o <= 0xD7AF
    ):
        return "ko"
    # CJK 漢字（中日共用）：基本區 + Extension A
    if 0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF:
        return "han"
    # 拉丁字母（含西/法/葡/德的重音字元）
    if c.isalpha() and o < 0x250:
        return "latin"
    return "neutral"


def _segment_by_script(text: str) -> list[tuple[str, str]]:
    """按腳本切段，neutral 字元（空白、標點、數字）跟著前一段走。"""
    segments: list[tuple[str, str]] = []
    current_script: str | None = None
    current_chars: list[str] = []

    for c in text:
        s = _script_of(c)
        if s == "neutral":
            current_chars.append(c)
            continue
        if current_script is None or s == current_script:
            current_script = s
            current_chars.append(c)
        else:
            segments.append((current_script, "".join(current_chars)))
            current_script = s
            current_chars = [c]

    if current_chars:
        # 整段都是 neutral 時，給拉丁讓英文 voice 處理（數字唸法較合理）
        segments.append((current_script or "latin", "".join(current_chars)))
    return segments


def _detect_latin_lang(text: str) -> str:
    """拉丁字母段落丟給 lingua 分辨語言；信心不夠就當英文。"""
    letters = sum(1 for c in text if c.isalpha())
    if letters < MIN_LATIN_CHARS_FOR_DETECT:
        return "en"
    vals = _latin_detector.compute_language_confidence_values(text)
    if not vals:
        return "en"
    top = vals[0]
    if top.value < LATIN_CONFIDENCE_THRESHOLD:
        return "en"
    return _LINGUA_TO_VOICE_KEY.get(top.language, "en")


def resolve_segments(text: str) -> list[tuple[str, str]]:
    """回傳 [(lang_key, segment_text), ...]，已合併相鄰同語言段落。"""
    raw = _segment_by_script(text)
    if not raw:
        return []

    resolved: list[tuple[str, str]] = []
    for i, (script, seg) in enumerate(raw):
        if script == "han":
            # 漢字段：若相鄰有假名（ja）就視為日文，否則中文
            prev_ja = i > 0 and raw[i - 1][0] == "ja"
            next_ja = i + 1 < len(raw) and raw[i + 1][0] == "ja"
            lang = "ja" if (prev_ja or next_ja) else "zh"
        elif script == "latin":
            lang = _detect_latin_lang(seg)
        else:
            lang = script  # ja / ko
        resolved.append((lang, seg))

    # 合併相鄰同語言段落，減少 TTS 切換感
    merged: list[tuple[str, str]] = []
    for lang, seg in resolved:
        if merged and merged[-1][0] == lang:
            merged[-1] = (lang, merged[-1][1] + seg)
        else:
            merged.append((lang, seg))
    return merged


async def _synthesize_one(segment: str, voice: str) -> str | None:
    """合成單段 mp3，回傳暫存檔路徑；失敗回傳 None。"""
    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    tmp.close()
    try:
        communicate = edge_tts.Communicate(
            segment, voice, rate=RATE, volume=VOLUME
        )
        await communicate.save(tmp.name)
        return tmp.name
    except Exception:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        raise


async def synthesize_and_play(username: str, text: str):
    """依語言分段合成並依序播放"""
    segments = resolve_segments(text)
    if not segments:
        return

    for lang, seg in segments:
        voice = VOICE_MAP.get(lang, DEFAULT_VOICE)
        path: str | None = None
        try:
            path = await _synthesize_one(seg, voice)
            pygame.mixer.music.load(path)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy():
                await asyncio.sleep(0.1)
            pygame.mixer.music.unload()
        except Exception as e:
            print(f"[TTS 錯誤] {username} ({lang}): {e}")
        finally:
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass


async def tts_worker():
    """從佇列拿訊息出來唸（一次一個，不重疊）"""
    while True:
        username, text = await tts_queue.get()
        segments = resolve_segments(text)
        langs = "+".join(lang for lang, _ in segments) or "?"
        print(f"[念/{langs}] {username}: {text}")
        await synthesize_and_play(username, text)
        tts_queue.task_done()


async def handle_message(username: str, message: str):
    if not message.lower().startswith(COMMAND_PREFIX.lower()):
        return

    text = message[len(COMMAND_PREFIX):].strip()
    text = clean_text(text)
    if not text:
        return
    if len(text) > MAX_LENGTH:
        text = text[:MAX_LENGTH] + "..."

    now = time.time()
    last = user_last_used.get(username, 0)
    if now - last < USER_COOLDOWN:
        print(f"[冷卻中] {username}")
        return
    user_last_used[username] = now

    await tts_queue.put((username, text))


async def chat_reader():
    """連線 Twitch IRC 讀聊天，斷線自動重連"""
    while True:
        try:
            reader, writer = await asyncio.open_connection(SERVER, PORT)
            writer.write(f"NICK {NICK}\r\n".encode())
            writer.write(f"JOIN #{CHANNEL.lower()}\r\n".encode())
            await writer.drain()
            print(f"[已連線] #{CHANNEL}")

            buf = b""
            msg_pattern = re.compile(
                r":(\w+)!\w+@[\w.]+ PRIVMSG #\w+ :(.+)"
            )

            while True:
                chunk = await reader.read(4096)
                if not chunk:
                    raise ConnectionError("連線關閉")
                buf += chunk
                while b"\r\n" in buf:
                    raw, buf = buf.split(b"\r\n", 1)
                    line = raw.decode("utf-8", errors="ignore")

                    if line.startswith("PING"):
                        writer.write(b"PONG :tmi.twitch.tv\r\n")
                        await writer.drain()
                        continue

                    m = msg_pattern.match(line)
                    if m:
                        await handle_message(m.group(1), m.group(2))

        except Exception as e:
            print(f"[連線錯誤] {e}，5 秒後重連")
            await asyncio.sleep(5)


async def main():
    if CHANNEL == "your_channel_name":
        print("⚠ 請先在 twitch_tts.py 開頭把 CHANNEL 改成你的頻道名")
        return
    pygame.mixer.init()
    print(f"[啟動] Twitch Chat TTS — 在 #{CHANNEL} 打 {COMMAND_PREFIX} <訊息> 來測試")
    print(f"[語音] 中:{VOICE_MAP['zh']} / 日:{VOICE_MAP['ja']} / 英:{VOICE_MAP['en']}")
    print(f"[拉丁語系] 可分辨：{', '.join(_LINGUA_TO_VOICE_KEY.values())}")
    await asyncio.gather(chat_reader(), tts_worker())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[結束]")
