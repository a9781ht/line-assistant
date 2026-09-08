from __future__ import annotations

import getpass
import os
from pathlib import Path
from typing import Any

import httpx

API_URL = "https://api.line.me"
DATA_URL = "https://api-data.line.me"
MENU_NAME = "line-assistant-accounting-v1"


def menu_definition() -> dict[str, Any]:
    actions = (
        {"type": "postback", "data": "act=entry.start&direction=expense", "displayText": "記支出"},
        {"type": "postback", "data": "act=entry.start&direction=income", "displayText": "記收入"},
        {"type": "postback", "data": "act=entry.recent", "displayText": "最近紀錄"},
        {"type": "postback", "data": "act=entry.summary", "displayText": "本月統計"},
        {"type": "postback", "data": "act=setup.start", "displayText": "設定"},
        {"type": "postback", "data": "act=help", "displayText": "說明"},
    )
    areas: list[dict[str, Any]] = []
    cell_width = 2500 // 3
    cell_height = 1686 // 2
    for index, action in enumerate(actions):
        column = index % 3
        row = index // 3
        x = column * cell_width
        y = row * cell_height
        width = 2500 - x if column == 2 else cell_width
        height = 1686 - y if row == 1 else cell_height
        areas.append(
            {
                "bounds": {"x": x, "y": y, "width": width, "height": height},
                "action": action,
            }
        )
    return {
        "size": {"width": 2500, "height": 1686},
        "selected": True,
        "name": MENU_NAME,
        "chatBarText": "開啟記帳中心",
        "areas": areas,
    }


def main() -> None:
    token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN") or getpass.getpass(
        "LINE Channel Access Token: "
    )
    headers = {"Authorization": f"Bearer {token}"}
    image_path = Path(__file__).resolve().parents[1] / "assets" / "rich-menu.png"
    if not image_path.exists():
        raise SystemExit("找不到 Rich Menu 圖片，請先執行 generate_rich_menu.py")

    with httpx.Client(timeout=30) as client:
        response = client.get(f"{API_URL}/v2/bot/richmenu/list", headers=headers)
        response.raise_for_status()
        existing = next(
            (item for item in response.json().get("richmenus", []) if item["name"] == MENU_NAME),
            None,
        )
        if existing:
            rich_menu_id = existing["richMenuId"]
        else:
            response = client.post(
                f"{API_URL}/v2/bot/richmenu",
                headers={**headers, "Content-Type": "application/json"},
                json=menu_definition(),
            )
            response.raise_for_status()
            rich_menu_id = response.json()["richMenuId"]
            response = client.post(
                f"{DATA_URL}/v2/bot/richmenu/{rich_menu_id}/content",
                headers={**headers, "Content-Type": "image/png"},
                content=image_path.read_bytes(),
            )
            response.raise_for_status()

        response = client.post(
            f"{API_URL}/v2/bot/user/all/richmenu/{rich_menu_id}", headers=headers
        )
        response.raise_for_status()
        print(f"Rich Menu 已發布：{rich_menu_id}")


if __name__ == "__main__":
    main()
