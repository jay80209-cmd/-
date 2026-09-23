# mini_agent

一個很小、省 token 的終端機 AI 助手，參考 ChatGPT 和 Claude 的功能，約 280 行 Python，透過 Claude API 運作。

## 安裝與執行

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
cd 你的專案資料夾
python /path/to/mini_agent.py            # 每個 shell 指令執行前會先問你 y/N
python /path/to/mini_agent.py --yes      # 自動執行所有指令（小心使用）
python /path/to/mini_agent.py --resume   # 接著上次的對話
```

## 功能

| 功能 | 對應 ChatGPT / Claude 的 | 用法 |
|---|---|---|
| 上網搜尋、讀網頁 | 搜尋 / Web search | 直接問，例如「今天台北天氣」 |
| 跨對話記憶 | Memory | 說「記住我喜歡 Python」；`/memory` 查看 |
| 上傳圖片、PDF、文字檔 | 檔案上傳 / 看圖 | `/attach 路徑`，再輸入你的問題 |
| 對話紀錄 | 聊天紀錄 | 自動儲存；`/resume` 或 `--resume` 繼續 |
| 自訂指示 | Custom instructions / Projects | 寫在 `~/.mini_agent/instructions.md`，或專案裡的 `AGENTS.md` / `CLAUDE.md` |
| 顯示思考過程 | 推理摘要 | `/think` 開關 |
| 停止回答 | 停止產生 | 按 Ctrl-C |
| 換模型、調思考量 | 模型選單 | `/model <id>`、`/effort <level>` |
| 寫程式、改檔、執行指令 | Code interpreter / Claude Code | 直接要求 |
| 開新對話 | New chat | `/new` |

資料存在 `~/.mini_agent/`：`memories/`（記憶）、`chats/`（對話紀錄）、`instructions.md`（自訂指示）。檔案操作只限在目前的資料夾內。

**做不到的：** 產生圖片、語音對話、影片。Claude API 沒有這些功能，要另外接其他服務。

## 怎麼省 token

| 做法 | 效果 |
|---|---|
| Prompt caching（`cache_control`） | 每一輪重送的歷史對話以約 1/10 價格計費 |
| 系統提示短且固定 | 快取不會失效，也不浪費輸入 token |
| `effort=medium`（預設） | 思考較少、工具呼叫較精簡、回答較短 |
| 工具輸出截斷到 8000 字元、網頁最多 20000 token | 大檔案、長 log、長網頁不會塞爆上下文 |
| 每次最多搜尋 5 次、讀 5 個網頁 | 避免搜尋停不下來 |
| 伺服器端壓縮（compaction） | 對話太長時自動摘要舊內容 |
| 每輪顯示 token 用量 | 隨時知道花了多少 |

注意：對話中途用 `/model`、`/effort`、`/think` 會讓快取重新開始，那一輪會比較貴。

## 設定（環境變數）

- `MINI_AGENT_MODEL`：預設 `claude-opus-5`
- `MINI_AGENT_EFFORT`：`low` / `medium` / `high` / `xhigh` / `max`，預設 `medium`。想更省就設成 `low`。

每次回答的上限設為模型最大值 128K token，並用串流接收，長回答不會逾時。上限只是天花板，只按實際用掉的 token 計費。

另外開啟了 `fallbacks: "default"`：萬一請求被安全機制拒絕，伺服器會自動改用其他模型重試。
