# PostgreSQL 備份與還原

備份容器每天建立 custom-format `pg_dump`，先用 `pg_restore --list` 驗證，再刪除超過 30 天的備份。

## 還原演練

1. 選取最新備份並記錄檔案雜湊。
2. 建立一個空白暫存 PostgreSQL 資料庫。
3. 使用 `pg_restore --clean --if-exists --no-owner` 還原。
4. 執行 Alembic migration 到目前版本。
5. 啟動 API readiness 檢查。
6. 核對各帳本交易數、收入、支出與結餘。
7. 完成後刪除暫存資料庫。

部署後可在備份服務內執行 `sh /usr/local/bin/verify_backup.sh` 自動完成上述暫存還原與資料表核對。

NAS 內的 dump 與 ZFS snapshot 仍屬同一故障域，不視為異地備份。
