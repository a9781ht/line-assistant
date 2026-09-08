from pathlib import Path

from PIL import Image


def test_rich_menu_asset_meets_line_requirements() -> None:
    path = Path(__file__).resolve().parents[1] / "assets" / "rich-menu.png"
    assert path.stat().st_size <= 1_000_000
    with Image.open(path) as image:
        assert image.format == "PNG"
        assert image.size == (2500, 1686)
