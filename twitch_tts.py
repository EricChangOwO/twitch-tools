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

# ===================== 設定 =====================
CHANNEL = "e0pwr"                      # 你的 Twitch 頻道名（不要加 # 號）
COMMAND_PREFIX = "!tts"
MAX_LENGTH = 300                       # 訊息最大長度，避免被刷
USER_COOLDOWN = 3                      # 每個使用者冷卻秒數
RATE = "+30%"                           # 語速，可以調 "-20%" 或 "+30%"
VOLUME = "+0%"                         # 音量

# 不同語言使用不同 voice
VOICE_MAP = {
    "zh": "zh-TW-HsiaoChenNeural",     # 繁中 — 曉臻
    "ja": "ja-JP-NanamiNeural",        # 日文 — Nanami
    "en": "en-US-AriaNeural",          # 英文 — Aria
    "ko": "ko-KR-SunHiNeural",         # 韓文 — SunHi
}
DEFAULT_VOICE = VOICE_MAP["zh"]
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


def detect_voice(text: str) -> str:
    """根據文字內容偵測該用哪個語音"""
    # 有日文假名（ひらがな / カタカナ）→ 日文
    if any("぀" <= c <= "ヿ" for c in text):
        return VOICE_MAP["ja"]
    # 有韓文諺文 → 韓文
    if any("가" <= c <= "힯" for c in text):
        return VOICE_MAP["ko"]
    # 有 CJK 漢字 → 中文
    if any("一" <= c <= "鿿" for c in text):
        return VOICE_MAP["zh"]
    # 全為 ASCII 英文字母 → 英文
    if any(c.isalpha() and ord(c) < 128 for c in text):
        return VOICE_MAP["en"]
    return DEFAULT_VOICE


async def synthesize_and_play(username: str, text: str):
    """生成 TTS 並播放"""
    voice = detect_voice(text)
    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    tmp.close()
    try:
        communicate = edge_tts.Communicate(
            text, voice, rate=RATE, volume=VOLUME
        )
        await communicate.save(tmp.name)

        pygame.mixer.music.load(tmp.name)
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            await asyncio.sleep(0.1)
        pygame.mixer.music.unload()
    except Exception as e:
        print(f"[TTS 錯誤] {username}: {e}")
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


async def tts_worker():
    """從佇列拿訊息出來唸（一次一個，不重疊）"""
    while True:
        username, text = await tts_queue.get()
        voice = detect_voice(text)
        print(f"[念/{voice.split('-')[0]}] {username}: {text}")
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
    await asyncio.gather(chat_reader(), tts_worker())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[結束]")
