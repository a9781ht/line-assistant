# TrueNAS SCALE 部署

## 資料集

建立兩個獨立 dataset：一個存 PostgreSQL data，另一個存 `pg_dump`。建議對兩者設定每日 ZFS snapshot；資料庫備份另行加密同步至異地儲存。

## Custom App

1. 將專案放在 TrueNAS 可讀取的位置。
2. 複製環境變數範例，設定強密碼、LINE Channel Secret 與 Access Token。若資料庫密碼包含 `@`、`:` 等字元，`DATABASE_URL` 內的密碼必須先做 URL encoding。
3. 將 `POSTGRES_DATA_PATH` 與 `POSTGRES_BACKUP_PATH` 設為上述 dataset 的實際掛載路徑。
4. 使用 TrueNAS SCALE 24.10+ 的 Custom App YAML 匯入 Compose 定義。
5. 確認 API 與 PostgreSQL 健康檢查成功；PostgreSQL 不應對 LAN 或 Internet 暴露連接埠。

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
