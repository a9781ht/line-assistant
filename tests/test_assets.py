from pathlib import Path

from PIL import Image


def test_rich_menu_asset_meets_line_requirements() -> None:
    assets_path = Path(__file__).resolve().parents[1] / "assets"
    path = next(
        (
            assets_path / name
            for name in ("rich-menu.jpg", "rich-menu.jpeg", "rich-menu.png")
            if (assets_path / name).exists()
        ),
        None,
    )
    assert path is not None
    assert path.stat().st_size <= 1_000_000
    with Image.open(path) as image:
        assert image.format in {"JPEG", "PNG"}
        assert image.size == (2500, 1686)
