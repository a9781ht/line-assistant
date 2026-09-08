# LINE 個人／群組記帳助理

以 FastAPI、PostgreSQL 與 LINE Messaging API 建立的私人／群組記帳中心。

## 功能

- 私人帳本與依 LINE 群組隔離的共享帳本
- 固定前兩層、第三層後可自訂的記帳分類樹
- 個人現金／信用卡付款方式與付款工具
- LINE Flex Message、Quick Reply 與 Rich Menu 流程
- 新增、確認、修改、備註、刪除、最近紀錄及月彙總
- Webhook 簽章驗證、重送冪等與完整審計紀錄
- TrueNAS SCALE 24.10+ 容器部署與 PostgreSQL 備份

## 本機開發

1. 使用 Python 3.12–3.14 建立虛擬環境。
2. 安裝開發依賴：`python -m pip install -e ".[dev]"`。
3. 複製環境變數範例並填入 LINE 測試頻道憑證。
4. 啟動 PostgreSQL，執行 `alembic upgrade head`。
5. 執行 `uvicorn line_assistant.main:app --reload`。

品質驗證：

- `python -m ruff check .`
- `python -m mypy src`
- `python -m pytest`

## LINE 設定

Webhook URL 為 `https://linebot.wufamily.dpdns.org/webhooks/line`。在 LINE Developers Console 啟用 Use webhook、Webhook redelivery 與 Allow bot to join group chats，並停用會重複回覆的內建自動回應。

先執行 Rich Menu 圖片產生工具，再執行發布工具。Access Token 由環境變數或隱藏輸入取得，不會寫入檔案。

## TrueNAS SCALE

將 PostgreSQL 資料、備份目錄分別指向不同 ZFS dataset，在 Custom App 匯入 Compose 定義並設定所有必要環境變數。現有 Cloudflare Tunnel 新增 `linebot.wufamily.dpdns.org`，導向 TrueNAS 的應用程式連接埠，且不要對 webhook 套用 Cloudflare Access 登入頁。

詳細步驟請參考部署與備份文件。
