# TrueNAS SCALE 部署

## 資料集

建立兩個獨立 dataset：一個存 PostgreSQL data，另一個存 `pg_dump`。建議對兩者設定每日 ZFS snapshot；資料庫備份另行加密同步至異地儲存。

## Custom App

1. 將完整專案放在 `/mnt/wu-pool/apps/lineBot/source`，其中必須包含 `Dockerfile`、`src`、`migrations` 及 `scripts`。
2. 在該目錄以環境變數範例建立 `.env`，設定 `APP_ENV=production`、強密碼、LINE Channel Secret 與 Access Token。若資料庫密碼包含 `@`、`:` 等字元，`DATABASE_URL` 內的密碼必須先做 URL encoding。
3. 使用 `compose.truenas.yaml` 作為 TrueNAS SCALE 24.10+ Custom App 的 YAML。此檔明確指定 source、PostgreSQL 與備份資料集的絕對路徑，不依賴 Compose 工作目錄。
4. 確認 API 與 PostgreSQL 健康檢查成功；PostgreSQL 不應對 LAN 或 Internet 暴露連接埠。

資料集權限建議如下：

- `/mnt/wu-pool/apps/lineBot/source`：`root:root`、`755`；其中 `.env` 應為 `root:root`、`600`。
- `/mnt/wu-pool/apps/lineBot/postgres`：PostgreSQL 容器使用者（通常 UID/GID `70:70`）、`700`。
- `/mnt/wu-pool/apps/lineBot/backup`：PostgreSQL 容器使用者（通常 UID/GID `70:70`）、`700`。

## Cloudflare Tunnel

在既有 Tunnel 新增 Public Hostname：

- Hostname：`linebot.wufamily.dpdns.org`
- Service：`http://<TrueNAS-IP>:18000`

不要設定 Cloudflare Access 登入政策。LINE 不公布固定來源 IP，因此應以 `X-Line-Signature` 驗證來源。

## LINE Developers Console

1. 設定 Webhook URL 為 `https://linebot.wufamily.dpdns.org/webhooks/line`。
2. 按 Verify，確認回傳成功。
3. 啟用 Use webhook、Webhook redelivery、Allow bot to join group chats。
4. 關閉 LINE Official Account Manager 中會造成雙重回覆的 Greeting message 與 Auto-response messages。
