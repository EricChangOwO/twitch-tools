# twitch-tools

兩個獨立的 Twitch 直播小工具，都是匿名連 Twitch IRC 監聽聊天指令，不需要 OAuth、不需要註冊 bot 帳號。

- **`twitch_tts.py`** — 觀眾打 `!tts <訊息>`，自動偵測語言並用對應語音唸出來。
- **`explosion.html`** — 觀眾打 `!explosion`，在 OBS 畫面上播放（去綠幕後的）爆炸特效影片。

兩個工具完全獨立，可以只用一個。

---

## 共用前置

把 `CHANNEL` 改成你的 Twitch 頻道名（不要加 `#`）：

- `twitch_tts.py` 第 16 行：`CHANNEL = "e0pwr"`
- `explosion.html` 第 71 行：`const CHANNEL = "e0pwr";`

兩個工具都用 `justinfan*****` 這種匿名暱稱連 `irc.chat.twitch.tv`，只讀不寫，所以不需要任何金鑰或授權。

---

## 1. Twitch TTS Bot (`twitch_tts.py`)

### 安裝與執行

```bash
pip install -r requirements.txt
python twitch_tts.py
```

依賴：

- `edge-tts` — 呼叫 Microsoft Edge 的線上 TTS（免費、不需 API key）
- `pygame-ce` — 播放 mp3
- `lingua-language-detector` — 拉丁字母語言判別

之後在你的 Twitch 聊天室打：

```
!tts 你好世界
!tts こんにちは
!tts Hola amigos
!tts 今日 sushi 很好吃
```

電腦上就會用對應語音唸出來。

### 設定項（檔頭 15–23 行）

| 變數 | 預設 | 說明 |
| --- | --- | --- |
| `COMMAND_PREFIX` | `"!tts"` | 觸發指令 |
| `MAX_LENGTH` | `300` | 單則訊息最長字數，超過會截斷 |
| `USER_COOLDOWN` | `3` | 同一使用者冷卻秒數（防洗版） |
| `RATE` | `"+30%"` | 語速，例：`"-20%"`、`"+50%"` |
| `VOLUME` | `"+0%"` | 音量微調 |
| `MIN_LATIN_CHARS_FOR_DETECT` | `4` | 拉丁字母少於此值就直接當英文（避免短字串誤判） |
| `LATIN_CONFIDENCE_THRESHOLD` | `0.6` | lingua 信心 < 此值就 fallback 英文（避免 `hello`/`lol`/`gg` 被判成義文） |

換語音改 `VOICE_MAP`，可選 voice 列表：

```bash
edge-tts --list-voices
```

### 語言偵測原理

混語訊息（例：「今日 sushi 很好吃」、「こんにちは hello」）會被切成多段、每段用各自的語音依序播放。整個流程：

**步驟 1：按 Unicode 腳本切段** (`_segment_by_script`)

逐字元分類成 5 類：

- `ja` — 平假名 / 片假名 / 半形片假名（`U+3040–U+30FF`, `U+FF65–U+FF9F`）
- `ko` — 韓文 Jamo / 諺文音節（`U+1100–U+11FF`, `U+3130–U+318F`, `U+AC00–U+D7AF`）
- `han` — CJK 漢字（`U+4E00–U+9FFF` + Extension A `U+3400–U+4DBF`）— 中日共用
- `latin` — 拉丁字母（含 á/é/ü/ñ 等帶重音字元，`ord < 0x250`）
- `neutral` — 空白、標點、數字（黏在前一段，不切）

連續同類字元歸為一段。

**步驟 2：解析段落語言** (`resolve_segments`)

- `ja` / `ko` 段：直接判定。
- `han` 段：預設中文，但若相鄰有 `ja` 段（含假名）就視為日文 — 這樣「こんにちは 世界」整句都會用日文唸，而「今日 sushi 很好吃」的漢字會用中文唸。
- `latin` 段：丟給 [`lingua`](https://github.com/pemistahl/lingua-py) 判別 en / es / fr / pt / de / it。lingua 對短文本準確度比 langdetect / langid 高，但仍可能對單字（`hello`、`lol`）誤判，所以加了兩個保險：
  - 字母數 < `MIN_LATIN_CHARS_FOR_DETECT` → 直接英文
  - 最高信心 < `LATIN_CONFIDENCE_THRESHOLD` → 直接英文

**步驟 3：合併相鄰同語言段**

例如 `[(zh, "你好"), (zh, "世界")]` 合成 `[(zh, "你好世界")]`，減少 TTS 切換次數。

**步驟 4：逐段合成 + 播放** (`synthesize_and_play`)

每段呼叫 `edge_tts.Communicate(...)` 存成暫存 mp3 → pygame 播放 → 刪檔 → 下一段。整體用 `asyncio.Queue` 排隊，一次只唸一則訊息。

### 已知限制

- 混語訊息會慢一些（要多次呼叫 edge-tts），段落間有短暫停頓。
- 純漢字訊息一律當中文唸（沒有上下文可判別中/日）。
- chat 俚語信心低時會用英文唸（`gg`、`lol`、`pog`），通常聽起來還可接受。

---

## 2. OBS Explosion Trigger (`explosion.html`)

### 用途

放在 OBS 當 Browser Source。觀眾打 `!explosion` 就在直播畫面上播一次爆炸影片，附帶 WebGL 去綠幕。

### 安裝

1. 準備一段綠幕背景的爆炸影片，命名為 `explosion.mp4`，放在 `explosion.html` 旁邊。
2. （可選）本機開 `explosion.html` 測試前，先點一下「點一下任何位置以啟用」解鎖 autoplay。
3. 在 OBS：
   - 新增來源 → Browser
   - Local file 勾選，選 `explosion.html`
   - 寬高設成你想要的覆蓋層尺寸（例：1920×1080）
   - 勾選「Shutdown source when not visible」可省效能（可選）

之後觀眾打 `!explosion` 就會播放。

### 設定項（檔頭 70–82 行）

| 變數 | 預設 | 說明 |
| --- | --- | --- |
| `CHANNEL` | `"e0pwr"` | Twitch 頻道名 |
| `COMMAND` | `"!explosion"` | 觸發指令 |
| `COOLDOWN_MS` | `1000` | 全頻道冷卻（毫秒） |
| `VIDEO_VOLUME` | `1.0` | 音量 0.0–1.0 |
| `CHROMA_KEY` | `true` | 關掉就播原影片 |
| `KEY_COLOR` | `[0, 1, 0]` | 去除的顏色（RGB 0–1），藍幕改 `[0, 0, 1]` |
| `SIMILARITY` | `0.40` | 相似度門檻，越大去越多 |
| `SMOOTHNESS` | `0.10` | 邊緣羽化，越大邊緣越柔 |
| `SPILL` | `0.15` | 邊緣殘色抑制（消綠邊） |

### 原理

**Twitch 連線：** 用 `wss://irc-ws.chat.twitch.tv:443`（WebSocket 版的 IRC）+ `justinfan*` 匿名暱稱 JOIN 頻道，逐行 parse `PRIVMSG`。斷線自動 5 秒後重連。

**去綠幕：** 不依賴 OBS 的 Color Key 濾鏡，直接在 WebGL fragment shader 做 chroma key。流程：

1. `<video>` 暗藏播放 `explosion.mp4`。
2. 每個 frame `texImage2D` 把 video 內容上傳成 WebGL 紋理。
3. Fragment shader 把 RGB 轉到 YCbCr 色度空間（`rgb2cc`），計算當前像素色度跟 `KEY_COLOR` 的距離 `d`。
4. `alpha = smoothstep(SIMILARITY, SIMILARITY + SMOOTHNESS, d)` — 離 key 色越近的像素越透明，並用 `SMOOTHNESS` 範圍做平滑漸變（避免硬邊）。
5. spill suppression：對綠邊殘留區域（距離 key 色仍小於 `SIMILARITY + SPILL` 的像素）按亮度去飽和，去掉物體邊緣的綠暈。
6. 畫到 `<canvas>`，整個 `#stage` 在影片播放時 fade in（0.15s 過渡），`ended` 後 fade out。

**Autoplay 解鎖：** 一般瀏覽器要求使用者互動才能 autoplay，所以放了一個全螢幕「點一下」遮罩。OBS Browser Source 預設允許 autoplay，並會在 User-Agent 包含 `OBS`，偵測到就自動隱藏遮罩。

---

## 開發備忘

```bash
# 改完 twitch_tts.py 後快速冒煙測試（不需要連 Twitch）
python -c "
import sys, types
sys.modules['pygame'] = types.ModuleType('pygame')
sys.modules['pygame'].mixer = types.SimpleNamespace(init=lambda: None, music=None)
sys.modules['edge_tts'] = types.ModuleType('edge_tts')
from twitch_tts import resolve_segments
print(resolve_segments('今日 sushi 很好吃'))
"
```
