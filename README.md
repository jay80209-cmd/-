# mini_agent

一個很小、省 token 的終端機 AI 程式助手（類似 Claude Code 的迷你版），約 160 行 Python，透過 Claude API 運作。

## 安裝與執行

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
cd 你的專案資料夾
python /path/to/mini_agent.py          # 每個 shell 指令執行前會先問你 y/N
python /path/to/mini_agent.py --yes    # 自動執行所有指令（小心使用）
```

它能執行 shell 指令、讀檔、建立檔案和修改檔案。檔案操作只限在目前的資料夾內。按 Ctrl-D 離開。

## 怎麼省 token

| 做法 | 效果 |
|---|---|
| Prompt caching（`cache_control`） | 每一輪重送的歷史對話以約 1/10 價格計費 |
| 系統提示短且固定 | 快取不會失效，也不浪費輸入 token |
| `effort=medium`（預設） | 思考較少、工具呼叫較精簡、回答較短 |
| 工具輸出截斷到 8000 字元 | 大檔案或長 log 不會塞爆上下文 |
| 伺服器端壓縮（compaction） | 對話太長時自動摘要舊內容 |
| 每輪顯示 token 用量 | 隨時知道花了多少 |

## 設定（環境變數）

- `MINI_AGENT_MODEL`：預設 `claude-opus-5`
- `MINI_AGENT_EFFORT`：`low` / `medium` / `high` / `xhigh` / `max`，預設 `medium`。想更省就設成 `low`。

另外開啟了 `fallbacks: "default"`：萬一請求被安全機制拒絕，伺服器會自動改用其他模型重試。
