"""Resolve installed font families locally for portable PDF embedding."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path


def _font_roots():
    home = Path.home()
    return [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts",
            Path(os.environ.get("LOCALAPPDATA", str(home))) / "Microsoft/Windows/Fonts",
            Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), home / ".fonts",
            home / ".local/share/fonts", Path("/System/Library/Fonts"), Path("/Library/Fonts"),
            home / "Library/Fonts"]


@lru_cache(maxsize=64)
def resolve_font(family: str) -> tuple[str, int]:
    from fontTools.ttLib import TTCollection, TTFont, TTLibError

    wanted = family.strip().casefold()
    candidates = []
    for root in _font_roots():
        if root.is_dir():
            candidates.extend(path for path in root.rglob("*") if path.suffix.lower() in {".ttf", ".ttc", ".otf"})
    # Common family/file correspondence is only a search optimization; names in
    # the actual font still decide the match, including localized family names.
    aliases = {"宋体": "simsun", "黑体": "simhei", "微软雅黑": "msyh", "仿宋": "simfang", "楷体": "simkai"}
    stem = aliases.get(wanted, wanted.replace(" ", ""))
    candidates.sort(key=lambda path: (not path.stem.casefold().startswith(stem), str(path)))
    for path in candidates:
        container = None
        fonts = []
        try:
            if path.suffix.lower() == ".ttc":
                container = TTCollection(str(path), lazy=True)
                fonts = container.fonts
            else:
                fonts = [TTFont(str(path), lazy=True)]
            match = None
            for index, font in enumerate(fonts):
                names = font["name"].names
                families = {record.toUnicode().strip().casefold() for record in names if record.nameID in {1, 16}}
                if wanted in families:
                    match = (str(path), index)
                    styles = {record.toUnicode().casefold() for record in names if record.nameID in {2, 17}}
                    if styles & {"regular", "normal", "常规", "標準", "roman"}:
                        break
            if match:
                return match
        except (OSError, TTLibError, UnicodeError, KeyError):
            continue
        finally:
            if container is not None:
                container.close()
            else:
                for font in fonts:
                    font.close()
    raise ValueError(f"本机未找到字体“{family}”，请安装该字体或在编译设置中选择已安装字体")
