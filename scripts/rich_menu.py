from __future__ import annotations

import getpass
import os
from pathlib import Path
from typing import Any

import httpx

API_URL = "https://api.line.me"
DATA_URL = "https://api-data.line.me"
MENU_NAME = "line-assistant-accounting-v1"
IMAGE_FILENAMES = ("rich-menu.jpg", "rich-menu.jpeg", "rich-menu.png")
SERVICE_AREAS = (
    ("ledger", "記帳中心"),
    ("placeholder-2", "XX中心"),
    ("placeholder-3", "XX中心"),
    ("placeholder-4", "XX中心"),
    ("placeholder-5", "XX中心"),
    ("placeholder-6", "XX中心"),
)


def rich_menu_content_type(image_path: Path) -> str:
    signature = image_path.read_bytes()[:8]
    if signature.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if signature.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    raise SystemExit("Rich Menu 圖片必須是 PNG 或 JPEG 格式")


def menu_definition() -> dict[str, Any]:
    areas: list[dict[str, Any]] = []
    cell_width = 2500 // 2
    cell_height = 1686 // 3
    for index, (service, label) in enumerate(SERVICE_AREAS):
        column = index % 2
        row = index // 2
        x = column * cell_width
        y = row * cell_height
        width = 2500 - x if column == 1 else cell_width
        height = 1686 - y if row == 2 else cell_height
        areas.append(
            {
                "bounds": {"x": x, "y": y, "width": width, "height": height},
                "action": {
                    "type": "postback",
                    "data": f"act=service.open&service={service}",
                    "displayText": label,
                },
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
    assets_path = Path(__file__).resolve().parents[1] / "assets"
    image_path = next(
        (assets_path / name for name in IMAGE_FILENAMES if (assets_path / name).exists()),
        None,
    )
    if image_path is None:
        raise SystemExit(
            "找不到 Rich Menu 圖片，請放入 rich-menu.jpg、rich-menu.jpeg 或 rich-menu.png"
        )
    image_content_type = rich_menu_content_type(image_path)

    with httpx.Client(timeout=30) as client:
        response = client.get(f"{API_URL}/v2/bot/richmenu/list", headers=headers)
        response.raise_for_status()
        existing_menus = response.json().get("richmenus", [])
        if existing_menus:
            response = client.delete(f"{API_URL}/v2/bot/user/all/richmenu", headers=headers)
            response.raise_for_status()
            for menu in existing_menus:
                response = client.delete(
                    f"{API_URL}/v2/bot/richmenu/{menu['richMenuId']}", headers=headers
                )
                response.raise_for_status()

        response = client.post(
            f"{API_URL}/v2/bot/richmenu",
            headers={**headers, "Content-Type": "application/json"},
            json=menu_definition(),
        )
        response.raise_for_status()
        rich_menu_id = response.json()["richMenuId"]
        response = client.post(
            f"{DATA_URL}/v2/bot/richmenu/{rich_menu_id}/content",
            headers={**headers, "Content-Type": image_content_type},
            content=image_path.read_bytes(),
        )
        response.raise_for_status()

        response = client.post(
            f"{API_URL}/v2/bot/user/all/richmenu/{rich_menu_id}", headers=headers
        )
        response.raise_for_status()
        print(f"Rich Menu 已取代舊選單並發布：{rich_menu_id}")


if __name__ == "__main__":
    main()
