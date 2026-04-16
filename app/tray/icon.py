from PIL import Image, ImageDraw, ImageFont


def create_tray_icon(size: int = 64) -> Image.Image:
    """Erzeugt ein Blitztext-Icon: blauer Kreis mit 'B'."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([2, 2, size - 2, size - 2], fill=(59, 130, 246, 255))
    try:
        font = ImageFont.truetype("/usr/share/fonts/TTF/DejaVuSans-Bold.ttf", size // 2)
    except Exception:
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size // 2)
        except Exception:
            font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), "B", font=font)
    x = (size - (bbox[2] - bbox[0])) // 2 - bbox[0]
    y = (size - (bbox[3] - bbox[1])) // 2 - bbox[1]
    draw.text((x, y), "B", fill="white", font=font)
    return img
