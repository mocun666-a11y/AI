"""Read and keep a local copy of memo background images."""
from hashlib import sha256
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps


def read_wallpaper(path):
    with Image.open(path) as source:
        if source.width * source.height > 80_000_000:
            raise ValueError("图片过大，请选择 8000 万像素以内的图片。")
        image = ImageOps.exif_transpose(source).convert("RGBA")
        image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
        background = Image.new("RGBA", image.size, "#F5F4EE")
        background.alpha_composite(image)
        return background.convert("RGB")


def keep_wallpaper(path, data_directory):
    if not path:
        return ""
    image = read_wallpaper(path)
    stream = BytesIO()
    image.save(stream, format="PNG")
    content = stream.getvalue()
    directory = Path(data_directory) / "backgrounds"
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"wallpaper-{sha256(content).hexdigest()[:20]}.png"
    if not destination.exists():
        temporary = destination.with_suffix(".tmp")
        temporary.write_bytes(content)
        temporary.replace(destination)
    return str(destination)
