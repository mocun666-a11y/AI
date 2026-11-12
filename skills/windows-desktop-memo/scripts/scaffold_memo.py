"""Create a clean Windows memo project without installing or launching anything."""
import argparse
import ast
from pathlib import Path
import re

FILES = ("app.py", "storage.py", "wallpaper.py", "test_memo.py", "requirements.txt", ".gitignore")


def scaffold(output, app_id, app_name, hotkey="M"):
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{2,60}", app_id):
        raise ValueError("app-id must be 3–61 ASCII letters, digits or underscores, starting with a letter")
    if not re.fullmatch(r"[\w ·-]{1,40}", app_name):
        raise ValueError("app-name must contain only letters, digits, spaces, ·, - or _ (1–40 characters)")
    hotkey = hotkey.upper()
    if not re.fullmatch(r"[A-Z]", hotkey):
        raise ValueError("hotkey must be one ASCII letter")
    output = Path(output).expanduser().resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise FileExistsError("Refusing to replace a non-empty project directory")
    template = Path(__file__).resolve().parents[1] / "assets" / "template"
    prepared = {}
    for name in FILES:
        content = (template / name).read_text(encoding="utf-8")
        if name == "app.py":
            content = content.replace("DesktopMemoTemplate", app_id).replace("今日备忘", app_name)
            content = content.replace('ord("M")', f'ord("{hotkey}")')
            content = content.replace("Ctrl+Alt+M", f"Ctrl+Alt+{hotkey}").replace("Ctrl + Alt + M", f"Ctrl + Alt + {hotkey}")
        if name.endswith(".py"):
            ast.parse(content, filename=name)
        prepared[name] = content
    output.mkdir(parents=True, exist_ok=True)
    for name, content in prepared.items():
        (output / name).write_text(content, encoding="utf-8")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--app-id", required=True)
    parser.add_argument("--app-name", default="今日备忘")
    parser.add_argument("--hotkey", default="M")
    args = parser.parse_args()
    print(scaffold(args.output, args.app_id, args.app_name, args.hotkey))


if __name__ == "__main__":
    main()
