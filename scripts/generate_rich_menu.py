from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WIDTH = 2500
HEIGHT = 1686
LABELS = ("記支出", "記收入", "最近紀錄", "本月統計", "設定", "說明")
COLORS = ("#DC2626", "#059669", "#2563EB", "#7C3AED", "#EA580C", "#475569")


def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        Path("C:/Windows/Fonts/msjh.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    )
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def main() -> None:
    image = Image.new("RGB", (WIDTH, HEIGHT), "#F8FAFC")
    draw = ImageDraw.Draw(image)
    font = load_font(105)
    sub_font = load_font(42)
    cell_width = WIDTH // 3
    cell_height = HEIGHT // 2

    for index, (label, color) in enumerate(zip(LABELS, COLORS, strict=True)):
        column = index % 3
        row = index // 3
        left = column * cell_width
        top = row * cell_height
        right = WIDTH if column == 2 else (column + 1) * cell_width
        bottom = HEIGHT if row == 1 else (row + 1) * cell_height
        draw.rounded_rectangle(
            (left + 24, top + 24, right - 24, bottom - 24),
            radius=48,
            fill=color,
        )
        box = draw.textbbox((0, 0), label, font=font)
        text_width = box[2] - box[0]
        text_height = box[3] - box[1]
        draw.text(
            ((left + right - text_width) / 2, (top + bottom - text_height) / 2 - 24),
            label,
            font=font,
            fill="white",
        )
        draw.text(
            ((left + right) / 2, bottom - 110),
            "LINE 記帳中心",
            font=sub_font,
            anchor="mm",
            fill="#FFFFFFCC",
        )

    output = Path(__file__).resolve().parents[1] / "assets" / "rich-menu.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, format="PNG", optimize=True)
    print(output)


if __name__ == "__main__":
    main()
