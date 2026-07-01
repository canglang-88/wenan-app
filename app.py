import json
import queue
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
import urllib.request
import zipfile
import ctypes
import base64
import hashlib
import hmac
import platform
import uuid
from pathlib import Path
from tkinter import filedialog, messagebox, ttk


APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
COPY_DIR = APP_DIR / "data"
LEGACY_COPY_DIR = Path(r"D:\桌面\文案")
INSPIRATION_CATEGORY = "励志文案"
SINGLE_INSTANCE_PORT = 39271
APP_VERSION = "v1.6.6"
APP_EXE_NAME = "文案中枢.exe"
GITHUB_OWNER = "canglang-88"
GITHUB_REPO = "wenan-app"
UPDATE_API_URL = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
UPDATE_ASSET_NAME = "wenan_app_update.zip"
LICENSE_SECRET = "wenan-canglang-license-2026-v1"
LICENSE_DIR = Path.home() / "AppData" / "Roaming" / "WenanApp"
LICENSE_FILE = LICENSE_DIR / "license.json"


COLORS = {
    "bg": "#07111f",
    "bg2": "#0b1b2f",
    "panel": "#0d2036",
    "panel2": "#102a45",
    "panel3": "#08192b",
    "line": "#1f4f73",
    "line2": "#2c7da0",
    "ink": "#e8f7ff",
    "muted": "#8fb7c9",
    "cyan": "#25d9ff",
    "cyan2": "#1296db",
    "green": "#39ffb6",
    "red": "#ff5e7a",
    "yellow": "#ffd166",
    "input": "#081827",
}


def read_text(path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(errors="ignore")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8-sig")


def bootstrap_internal_data() -> None:
    COPY_DIR.mkdir(parents=True, exist_ok=True)
    has_data = any(COPY_DIR.rglob("*.txt"))
    if has_data or not LEGACY_COPY_DIR.exists():
        return
    for source in LEGACY_COPY_DIR.rglob("*.txt"):
        relative = source.relative_to(LEGACY_COPY_DIR)
        target = COPY_DIR / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def acquire_single_instance():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", SINGLE_INSTANCE_PORT))
        sock.listen(1)
        return sock
    except OSError:
        return None


def bring_existing_window_to_front() -> None:
    user32 = ctypes.windll.user32
    handles = []

    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        if buffer.value.startswith("文案中枢"):
            handles.append(hwnd)
            return False
        return True

    enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)(callback)
    user32.EnumWindows(enum_proc, 0)
    if handles:
        user32.ShowWindow(handles[0], 9)
        user32.SetForegroundWindow(handles[0])


def parse_version(version: str) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", version)
    return tuple(int(number) for number in numbers) if numbers else (0,)


def is_newer_version(remote: str, current: str) -> bool:
    remote_parts = parse_version(remote)
    current_parts = parse_version(current)
    length = max(len(remote_parts), len(current_parts))
    remote_parts += (0,) * (length - len(remote_parts))
    current_parts += (0,) * (length - len(current_parts))
    return remote_parts > current_parts


def get_device_code() -> str:
    raw = "|".join(
        [
            platform.node(),
            platform.system(),
            platform.machine(),
            str(uuid.getnode()),
        ]
    )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest().upper()
    return "WENAN-" + "-".join([digest[i : i + 4] for i in range(0, 16, 4)])


def license_signature(device_code: str, expires: str) -> str:
    payload = f"{device_code}|{expires}|{LICENSE_SECRET}"
    digest = hmac.new(LICENSE_SECRET.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest().upper()
    return digest[:24]


def make_license_code(device_code: str, expires: str = "PERMANENT") -> str:
    device_code = device_code.strip().upper()
    expires = expires.strip().upper() or "PERMANENT"
    signature = license_signature(device_code, expires)
    token = base64.urlsafe_b64encode(f"{expires}|{signature}".encode("utf-8")).decode("ascii").rstrip("=")
    return "CL-" + "-".join(token[i : i + 5] for i in range(0, len(token), 5))


def verify_license_code(device_code: str, code: str) -> tuple[bool, str]:
    cleaned = code.strip().replace(" ", "").replace("\n", "").replace("\r", "")
    if cleaned.upper().startswith("CL-"):
        cleaned = cleaned[3:]
    normalized = cleaned.replace("-", "")
    if not normalized:
        return False, "请输入授权秘钥"
    try:
        padding = "=" * (-len(normalized) % 4)
        decoded = base64.urlsafe_b64decode((normalized + padding).encode("ascii")).decode("utf-8")
        expires, signature = decoded.split("|", 1)
    except Exception:
        return False, "授权秘钥格式不正确"
    expected = license_signature(device_code.strip().upper(), expires.strip().upper())
    if not hmac.compare_digest(signature.strip().upper(), expected):
        return False, "授权秘钥和本机设备码不匹配"
    if expires.strip().upper() != "PERMANENT":
        try:
            from datetime import date

            expire_date = date.fromisoformat(expires.strip())
            if date.today() > expire_date:
                return False, "授权秘钥已过期"
        except ValueError:
            return False, "授权秘钥有效期格式不正确"
    return True, expires.strip().upper()


def load_license() -> dict:
    if not LICENSE_FILE.exists():
        return {}
    try:
        return json.loads(read_text(LICENSE_FILE))
    except Exception:
        return {}


def save_license(device_code: str, code: str, expires: str) -> None:
    LICENSE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "device_code": device_code,
        "license_code": code.strip(),
        "expires": expires,
        "activated_version": APP_VERSION,
    }
    write_text(LICENSE_FILE, json.dumps(payload, ensure_ascii=False, indent=2))


def is_activated() -> bool:
    license_data = load_license()
    device_code = get_device_code()
    if license_data.get("device_code") != device_code:
        return False
    ok, _message = verify_license_code(device_code, license_data.get("license_code", ""))
    return ok


def show_activation_window() -> bool:
    device_code = get_device_code()
    root = tk.Tk()
    root.title("文案中枢激活")
    root.geometry("560x360")
    root.resizable(False, False)
    root.configure(bg=COLORS["panel"])
    result = {"ok": False}

    tk.Label(root, text="文案中枢激活", bg=COLORS["panel"], fg=COLORS["ink"], font=("Microsoft YaHei", 20, "bold")).pack(anchor="w", padx=26, pady=(24, 6))
    tk.Label(root, text="首次使用需要绑定本机设备。请把设备码发给管理员获取授权秘钥。", bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei", 10), wraplength=500, justify=tk.LEFT).pack(anchor="w", padx=26, pady=(0, 18))

    tk.Label(root, text="本机设备码", bg=COLORS["panel"], fg=COLORS["cyan"], font=("Microsoft YaHei", 10, "bold")).pack(anchor="w", padx=26)
    device_var = tk.StringVar(value=device_code)
    device_entry = tk.Entry(root, textvariable=device_var, bd=0, bg=COLORS["input"], fg=COLORS["ink"], font=("Consolas", 13), readonlybackground=COLORS["input"])
    device_entry.configure(state="readonly")
    device_entry.pack(fill=tk.X, padx=26, pady=(6, 12), ipady=10)

    tk.Label(root, text="授权秘钥", bg=COLORS["panel"], fg=COLORS["cyan"], font=("Microsoft YaHei", 10, "bold")).pack(anchor="w", padx=26)
    code_var = tk.StringVar()
    code_entry = tk.Entry(root, textvariable=code_var, bd=0, bg=COLORS["input"], fg=COLORS["ink"], insertbackground=COLORS["cyan"], font=("Consolas", 12))
    code_entry.pack(fill=tk.X, padx=26, pady=(6, 14), ipady=10)
    code_entry.focus_set()

    status_var = tk.StringVar(value="")
    tk.Label(root, textvariable=status_var, bg=COLORS["panel"], fg=COLORS["yellow"], font=("Microsoft YaHei", 10)).pack(anchor="w", padx=26)

    buttons = tk.Frame(root, bg=COLORS["panel"])
    buttons.pack(fill=tk.X, padx=26, pady=(16, 0))

    def copy_device():
        root.clipboard_clear()
        root.clipboard_append(device_code)
        root.update()
        status_var.set("设备码已复制")

    def activate():
        ok, message = verify_license_code(device_code, code_var.get())
        if not ok:
            status_var.set(message)
            return
        save_license(device_code, code_var.get(), message)
        result["ok"] = True
        messagebox.showinfo("激活成功", "本机已激活，以后打开不需要再次输入")
        root.destroy()

    def cancel():
        root.destroy()

    RoundButton(buttons, "复制设备码", copy_device, COLORS["cyan"], width=130).pack(side=tk.LEFT)
    RoundButton(buttons, "激活", activate, COLORS["green"], width=110).pack(side=tk.RIGHT)
    RoundButton(buttons, "退出", cancel, COLORS["panel2"], fg=COLORS["ink"], width=100).pack(side=tk.RIGHT, padx=10)
    root.mainloop()
    return result["ok"]


def parse_file(path: Path) -> list[dict]:
    try:
        relative = path.relative_to(COPY_DIR)
    except ValueError:
        return []

    parts = relative.parts
    if not parts:
        return []

    category = parts[0]
    subcategory = path.stem if path.stem != category else ""
    items = []
    current_number = None
    current_lines = []

    def flush_current():
        if current_number is None:
            return
        text = "\n".join(current_lines).strip()
        if not text:
            return
        items.append(
            {
                "category": category,
                "subcategory": subcategory,
                "number": current_number,
                "text": text,
                "path": path,
            }
        )

    for line in read_text(path).splitlines():
        match = re.match(r"^\s*(\d+)[\.、]\s*(.*)$", line)
        if not match:
            if current_number is not None:
                current_lines.append(line)
            continue
        flush_current()
        current_number = int(match.group(1))
        current_lines = [match.group(2)]
    flush_current()
    return items


def get_files() -> list[Path]:
    if not COPY_DIR.exists():
        return []
    return sorted(COPY_DIR.rglob("*.txt"), key=lambda p: str(p))


def load_items() -> list[dict]:
    items = []
    for path in get_files():
        items.extend(parse_file(path))
    return items


def load_categories() -> dict[str, list[str]]:
    categories: dict[str, list[str]] = {}
    if not COPY_DIR.exists():
        return categories

    for folder in sorted([p for p in COPY_DIR.iterdir() if p.is_dir()], key=lambda p: p.name):
        files = sorted(folder.glob("*.txt"), key=lambda p: p.stem)
        categories[folder.name] = [p.stem for p in files if p.stem != folder.name]
    return categories


def ensure_category(category: str) -> Path:
    category = category.strip()
    if not category:
        raise ValueError("请输入大分类名称")
    if any(char in category for char in r'\/:*?"<>|'):
        raise ValueError("分类名称不能包含这些字符：\\ / : * ? \" < > |")
    folder = COPY_DIR / category
    folder.mkdir(parents=True, exist_ok=True)
    main_file = folder / f"{category}.txt"
    if not main_file.exists():
        write_text(main_file, f"{category}\n")
    return folder


def rename_category(old_name: str, new_name: str) -> None:
    old_name = old_name.strip()
    new_name = new_name.strip()
    if not old_name or old_name == "全部":
        raise ValueError("请先选择一个要修改的大项")
    if not new_name:
        raise ValueError("请输入新的大项名称")
    if any(char in new_name for char in r'\/:*?"<>|'):
        raise ValueError("大项名称不能包含这些字符：\\ / : * ? \" < > |")
    if old_name == new_name:
        raise ValueError("新名称和原名称一样")

    old_folder = COPY_DIR / old_name
    new_folder = COPY_DIR / new_name
    if not old_folder.exists():
        raise ValueError("原大项不存在，请先重新读取数据")
    if new_folder.exists():
        raise ValueError("这个大项名称已经存在")

    old_main_file = old_folder / f"{old_name}.txt"
    new_main_file = old_folder / f"{new_name}.txt"
    if old_main_file.exists() and not new_main_file.exists():
        old_main_file.rename(new_main_file)
    shutil.move(str(old_folder), str(new_folder))


def ensure_subcategory(category: str, subcategory: str) -> Path:
    category = category.strip()
    subcategory = subcategory.strip()
    if not category or category == "全部":
        raise ValueError("请先选择一个大项")
    if not subcategory:
        raise ValueError("请输入子项目名称")
    if any(char in subcategory for char in r'\/:*?"<>|'):
        raise ValueError("子项目名称不能包含这些字符：\\ / : * ? \" < > |")
    ensure_category(category)
    path = COPY_DIR / category / f"{subcategory}.txt"
    if not path.exists():
        write_text(path, f"{subcategory}\n")
    return path


def target_file(category: str, subcategory: str) -> Path:
    if subcategory:
        return COPY_DIR / category / f"{subcategory}.txt"
    return COPY_DIR / category / f"{category}.txt"


def append_copy(category: str, subcategory: str, text: str) -> None:
    text = text.strip().replace("\r\n", "\n").replace("\r", "\n")
    if not category:
        raise ValueError("请选择分类")
    if not text:
        raise ValueError("请输入文案内容")

    ensure_category(category)
    path = target_file(category, subcategory)
    title = path.stem
    content = read_text(path).rstrip() if path.exists() else f"{title}\n"
    numbers = [int(match.group(1)) for match in re.finditer(r"^\s*(\d+)[\.、]", content, re.M)]
    next_number = max(numbers, default=0) + 1
    write_text(path, f"{content}\n{next_number}. {text}\n")


def parse_import_lines(source: Path) -> list[str]:
    if not source.exists():
        raise ValueError("请选择 TXT 文件")
    content = read_text(source).replace("\r\n", "\n").replace("\r", "\n")
    numbered_items = parse_chinese_numbered_items(content)
    if numbered_items:
        return numbered_items

    blocks = re.split(r"\n\s*\n+", content)
    items = []
    for block in blocks:
        lines = [line.rstrip() for line in block.split("\n") if line.strip()]
        if not lines:
            continue
        first = lines[0]
        match = re.match(r"^\s*(\d+)[\.、]\s*(.*)$", first)
        if match:
            lines[0] = match.group(2)
        items.append("\n".join(lines).strip())
    if not items:
        raise ValueError("TXT 里没有可导入的文案")
    return items


def parse_chinese_numbered_items(content: str) -> list[str]:
    normalized = content.replace("\ufeff", "").replace("\u3000", " ")
    heading_pattern = re.compile(
        r"(?m)(^|[\n\r])\s*(?:第)?(?P<num>[一二三四五六七八九十两壹贰叁肆伍陆柒捌玖拾]{1,5}|\d{1,3})\s*(?:[、.．:：]|\s*$)\s*(?P<rest>[^\n\r]*)"
    )

    raw_headings = []
    for match in heading_pattern.finditer(normalized):
        number_text = match.group("num")
        number = int(number_text) if number_text.isdigit() else chinese_to_int(number_text)
        if not number:
            continue
        heading_start = match.start()
        content_start = match.end()
        rest = match.group("rest").strip()
        raw_headings.append((heading_start, content_start, number, rest))

    sequence = []
    expected = 1
    for heading in raw_headings:
        if heading[2] == expected:
            sequence.append(heading)
            expected += 1
        elif sequence and heading[2] > sequence[-1][2] and heading[2] <= sequence[-1][2] + 2:
            sequence.append(heading)
            expected = heading[2] + 1

    if len(sequence) < 2:
        return fallback_split_numbered_items(normalized)

    items = []
    for index, (_heading_start, content_start, _number, rest) in enumerate(sequence):
        next_start = sequence[index + 1][0] if index + 1 < len(sequence) else len(normalized)
        body = normalized[content_start:next_start]
        if rest and body.startswith(rest):
            item = body.strip()
        else:
            item = ("\n".join([rest, body]) if rest else body).strip()
        if item:
            items.append(item)
    return items


def fallback_split_numbered_items(content: str) -> list[str]:
    markers = []
    marker_pattern = re.compile(r"(?:^|[\n\r\s])(?:第)?([一二三四五六七八九十两壹贰叁肆伍陆柒捌玖拾]{1,5}|\d{1,3})\s*[、.．:：]")
    for match in marker_pattern.finditer(content):
        raw = match.group(1)
        number = int(raw) if raw.isdigit() else chinese_to_int(raw)
        if number:
            markers.append((match.start(), match.end(), number))

    sequence = []
    expected = 1
    for marker in markers:
        if marker[2] == expected:
            sequence.append(marker)
            expected += 1

    if len(sequence) < 2:
        return []

    items = []
    for index, (_start, end, _number) in enumerate(sequence):
        next_start = sequence[index + 1][0] if index + 1 < len(sequence) else len(content)
        item = content[end:next_start].strip()
        if item:
            items.append(item)
    return items


def parse_number_heading(raw: str):
    line = raw.strip().replace("．", ".").replace("：", ":")
    if not line:
        return None

    arabic = re.match(r"^(?:第)?(\d{1,3})(?:[、.．:：]|\s+|$)(.*)$", line)
    if arabic:
        return int(arabic.group(1)), arabic.group(2).strip()

    chinese = re.match(r"^(?:第)?([一二三四五六七八九十两壹贰叁肆伍陆柒捌玖拾]{1,5})(?:[、.．:：]|\s+|$)(.*)$", line)
    if chinese:
        number = chinese_to_int(chinese.group(1))
        if number:
            return number, chinese.group(2).strip()
    return None


def chinese_to_int(value: str) -> int:
    digits = {
        "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
        "壹": 1, "贰": 2, "叁": 3, "肆": 4, "伍": 5, "陆": 6, "柒": 7, "捌": 8, "玖": 9,
    }
    value = value.replace("拾", "十")
    if value == "十":
        return 10
    if "十" in value:
        left, _, right = value.partition("十")
        tens = digits.get(left, 1) if left else 1
        ones = digits.get(right, 0) if right else 0
        return tens * 10 + ones
    return digits.get(value, 0)


def append_lines(category: str, subcategory: str, lines: list[str]) -> int:
    if not lines:
        return 0

    ensure_category(category)
    path = target_file(category, subcategory)
    title = path.stem
    old = read_text(path).rstrip() if path.exists() else f"{title}\n"
    numbers = [int(match.group(1)) for match in re.finditer(r"^\s*(\d+)[\.、]", old, re.M)]
    next_number = max(numbers, default=0) + 1
    additions = []
    for index, text in enumerate(lines, start=next_number):
        text = text.strip().replace("\r\n", "\n").replace("\r", "\n")
        first, *rest = text.split("\n")
        additions.append(f"{index}. {first}")
        additions.extend(rest)
    write_text(path, old + "\n" + "\n".join(additions) + "\n")
    return len(lines)


def delete_copy_items(items: list[dict]) -> int:
    if not items:
        return 0

    grouped: dict[Path, list[dict]] = {}
    for item in items:
        grouped.setdefault(Path(item["path"]), []).append(item)

    total_removed = 0
    for path, path_items in grouped.items():
        total_removed += delete_copy_items_from_file(path, path_items)
    return total_removed


def move_copy_items(items: list[dict], target_category: str, target_tag: str) -> int:
    if not items:
        return 0
    texts = [item["text"] for item in items]
    removed = delete_copy_items(items)
    append_lines(target_category, target_tag, texts)
    return removed


def update_copy_item(item: dict, new_text: str) -> None:
    new_text = new_text.strip().replace("\r\n", "\n").replace("\r", "\n")
    if not new_text:
        raise ValueError("文案内容不能为空")

    path = Path(item["path"])
    entries = parse_file(path)
    updated = False
    output = [path.stem, ""]
    for index, entry in enumerate(entries, start=1):
        text = new_text if entry["number"] == item["number"] and entry["text"] == item["text"] else entry["text"]
        if text == new_text and entry["number"] == item["number"] and entry["text"] == item["text"]:
            updated = True
        first, *rest = text.split("\n")
        output.append(f"{index}. {first}")
        output.extend(rest)

    if not updated:
        raise ValueError("没有找到要修改的文案")
    write_text(path, "\n".join(output).rstrip() + "\n")


def delete_copy_items_from_file(path: Path, items: list[dict]) -> int:
    if not path.exists():
        raise ValueError("对应的文案文件不存在")

    targets = {(item["number"], item["text"]) for item in items}
    title_lines = []
    entries = parse_file(path)
    removed = 0
    for raw in read_text(path).splitlines():
        match = re.match(r"^\s*(\d+)[\.、]\s*(.+?)\s*$", raw.strip())
        if match:
            break
        if raw.strip():
            title_lines.append(raw.strip())

    copy_lines = []
    for entry in entries:
        key = (entry["number"], entry["text"])
        if key in targets:
            removed += 1
        else:
            copy_lines.append(entry["text"])

    if removed == 0:
        raise ValueError("没有找到要删除的文案")

    title = title_lines[0] if title_lines else path.stem
    rewritten = [title, ""]
    for index, text in enumerate(copy_lines, start=1):
        first, *rest = text.split("\n")
        rewritten.append(f"{index}. {first}")
        rewritten.extend(rest)
    write_text(path, "\n".join(rewritten).rstrip() + "\n")
    return removed


def import_txt(category: str, subcategory: str, source: Path) -> int:
    return append_lines(category, subcategory, parse_import_lines(source))


def inspiration_keywords() -> dict[str, list[str]]:
    return {
        "治愈": ["治愈", "温柔", "慢慢来", "好起来", "疲惫", "难熬"],
        "励志": ["励志", "努力", "优秀", "变好", "向上", "争气"],
        "正能量": ["正能量", "阳光", "积极", "乐观", "希望", "热爱"],
        "梦想": ["梦想", "追梦", "远方", "目标", "热爱"],
        "致自己": ["致自己", "自己", "爱自己", "不辜负自己"],
        "希望": ["希望", "天会亮", "期待", "未来", "光"],
        "坚持": ["坚持", "不放弃", "撑住", "继续", "再走一步"],
        "乐观": ["乐观", "笑", "好心态", "晴天", "开心"],
        "积极": ["积极", "行动", "主动", "进步", "解决"],
        "成长": ["成长", "长大", "经历", "成熟", "改变"],
        "奋斗": ["奋斗", "拼搏", "汗水", "努力", "事业"],
        "拼搏": ["拼搏", "不服输", "咬牙", "尽全力"],
        "加油": ["加油", "你可以", "别怕", "相信自己"],
        "三观正": ["三观", "善良", "尊重", "底线", "分寸", "真诚"],
        "自律": ["自律", "执行力", "计划", "拖延", "管住"],
        "勇敢": ["勇敢", "害怕", "重新开始", "尝试", "不退"],
        "救赎": ["救赎", "放过自己", "过去", "新生", "重建"],
    }


def extract_keyword_tag(text: str, category: str = "") -> str:
    tag_keywords = {
        "薪酬": ["薪酬", "薪资", "工资", "年终", "奖金", "rsu", "期权", "401k"],
        "技术支持": ["技术支持", "技术", "项目推进", "效率", "落地"],
        "投资": ["投资", "基金", "股票", "收益", "风险", "质押", "短期交易"],
        "区块链": ["区块链", "验证者", "权益证明", "代币", "链上"],
        "招聘": ["招聘", "岗位", "候选人", "面试", "入职"],
        "合作": ["合作", "伙伴", "政府", "公司", "战略"],
        "生活": ["生活", "日子", "今天", "普通", "烟火"],
        "情感": ["情感", "喜欢", "爱", "关系", "真心", "陪伴"],
        "个性": ["个性", "态度", "自由", "边界", "不讨好"],
        "旅行": ["旅行", "远方", "风景", "山海", "出发"],
        "经典": ["经典", "人生", "时间", "成熟", "道理"],
        "运动": ["运动", "跑步", "健身", "流汗", "训练"],
    }
    normalized = text.lower()
    for tag, keywords in tag_keywords.items():
        if any(keyword.lower() in normalized for keyword in keywords):
            return tag

    words = re.findall(r"[\u4e00-\u9fff]{2,6}|[A-Za-z]{2,12}", text)
    stop_words = {"我们", "你们", "他们", "自己", "一个", "这个", "那个", "可以", "因为", "如果", "但是", "所以", "目前", "项目"}
    for word in words:
        if word not in stop_words and word != category.replace("文案", ""):
            return word[:6]
    return ""


def classify_text(text: str, categories: dict[str, list[str]], fallback_category: str, fallback_subcategory: str) -> tuple[str, str]:
    normalized = text.lower()

    inspiration_keywords = {
        "治愈": ["治愈", "温柔", "慢慢来", "好起来", "疲惫", "难熬"],
        "励志": ["励志", "努力", "优秀", "变好", "向上", "争气"],
        "正能量": ["正能量", "阳光", "积极", "乐观", "希望", "热爱"],
        "梦想": ["梦想", "追梦", "远方", "目标", "热爱"],
        "致自己": ["致自己", "自己", "爱自己", "不辜负自己"],
        "希望": ["希望", "天会亮", "期待", "未来", "光"],
        "坚持": ["坚持", "不放弃", "撑住", "继续", "再走一步"],
        "乐观": ["乐观", "笑", "好心态", "晴天", "开心"],
        "积极": ["积极", "行动", "主动", "进步", "解决"],
        "成长": ["成长", "长大", "经历", "成熟", "改变"],
        "奋斗": ["奋斗", "拼搏", "汗水", "努力", "事业"],
        "拼搏": ["拼搏", "不服输", "咬牙", "尽全力"],
        "加油": ["加油", "你可以", "别怕", "相信自己"],
        "三观正": ["三观", "善良", "尊重", "底线", "分寸", "真诚"],
        "自律": ["自律", "执行力", "计划", "拖延", "管住"],
        "勇敢": ["勇敢", "害怕", "重新开始", "尝试", "不退"],
        "救赎": ["救赎", "放过自己", "过去", "新生", "重建"],
    }

    category_keywords = {
        "日常文案": ["日常", "今天", "日子", "生活", "普通", "烟火", "小确幸", "记录"],
        "情感文案": ["情感", "爱", "喜欢", "感情", "真心", "恋", "想你", "在乎", "陪伴", "关系"],
        "个性文案": ["个性", "态度", "自由", "不讨好", "边界", "随便", "定义", "不合群"],
        "旅行文案": ["旅行", "远方", "山海", "风景", "出发", "城市", "路上", "行囊"],
        "经典文案": ["经典", "人生", "时间", "成熟", "道理", "底气", "格局", "修养"],
        "运动文案": ["运动", "跑步", "健身", "流汗", "汗水", "训练", "身材", "热身"],
    }

    best_category = ""
    best_score = 0
    for category in categories:
        keywords = category_keywords.get(category, [category.replace("文案", "")])
        score = sum(1 for keyword in keywords if keyword and keyword.lower() in normalized)
        category_stem = category.replace("文案", "")
        if category_stem and category_stem.lower() in normalized:
            score += 2
        if score > best_score:
            best_category = category
            best_score = score

    best_subcategory = ""
    best_sub_score = 0
    inspiration_subs = categories.get(INSPIRATION_CATEGORY, [])
    for subcategory in inspiration_subs:
        keywords = inspiration_keywords.get(subcategory, [subcategory])
        score = sum(1 for keyword in keywords if keyword and keyword.lower() in normalized)
        if subcategory.lower() in normalized:
            score += 2
        if score > best_sub_score:
            best_subcategory = subcategory
            best_sub_score = score

    if best_subcategory and best_sub_score >= max(2, best_score):
        return INSPIRATION_CATEGORY, best_subcategory
    if best_category and best_score > 0:
        return best_category, extract_keyword_tag(text, best_category)
    if fallback_category == INSPIRATION_CATEGORY:
        return fallback_category, fallback_subcategory
    return fallback_category, fallback_subcategory or extract_keyword_tag(text, fallback_category)


def classify_tag_in_category(text: str, category: str, selected_subcategory: str, categories: dict[str, list[str]], auto_tag: bool) -> str:
    if not auto_tag:
        return selected_subcategory

    normalized = text.lower()
    known_tags = categories.get(category, [])
    keyword_map = inspiration_keywords() if category == INSPIRATION_CATEGORY else {}
    best_tag = ""
    best_score = 0
    for tag in known_tags:
        keywords = keyword_map.get(tag, [tag])
        score = sum(1 for keyword in keywords if keyword and keyword.lower() in normalized)
        if tag.lower() in normalized:
            score += 2
        if score > best_score:
            best_tag = tag
            best_score = score

    if best_tag:
        return best_tag
    return selected_subcategory or extract_keyword_tag(text, category)


def build_import_plan(source: Path, category: str, selected_subcategory: str, categories: dict[str, list[str]], auto_tag: bool) -> dict[tuple[str, str], list[str]]:
    if not category:
        raise ValueError("请选择导入的大类")
    lines = parse_import_lines(source)
    plan: dict[tuple[str, str], list[str]] = {}
    for line in lines:
        tag = classify_tag_in_category(line, category, selected_subcategory, categories, auto_tag)
        plan.setdefault((category, tag), []).append(line)
    return plan


def import_txt_auto(source: Path, fallback_category: str, fallback_subcategory: str, categories: dict[str, list[str]]) -> tuple[int, dict[str, int]]:
    if not fallback_category:
        raise ValueError("请选择一个默认大类，用来接收无法识别的文案")
    lines = parse_import_lines(source)
    grouped: dict[tuple[str, str], list[str]] = {}
    for line in lines:
        category, subcategory = classify_text(line, categories, fallback_category, fallback_subcategory)
        grouped.setdefault((category, subcategory), []).append(line)

    summary = {}
    total = 0
    for (category, subcategory), target_lines in grouped.items():
        count = append_lines(category, subcategory, target_lines)
        label = f"{category}/{subcategory}" if subcategory else category
        summary[label] = count
        total += count
    return total, summary


class RoundButton(tk.Canvas):
    def __init__(self, parent, text, command, bg, fg="#03101a", width=150, height=42, radius=18):
        parent_bg = parent.cget("bg") if hasattr(parent, "cget") else COLORS["bg"]
        super().__init__(parent, width=width, height=height, bg=parent_bg, highlightthickness=0, bd=0)
        self.command = command
        self.text = text
        self.fill = bg
        self.fg = fg
        self.radius = radius
        self.is_pressed = False
        self.configure(cursor="hand2")
        self.bind("<ButtonPress-1>", self.press)
        self.bind("<ButtonRelease-1>", self.release)
        self.bind("<Enter>", lambda _event: self.draw(hover=True))
        self.bind("<Leave>", lambda _event: self.draw())
        self.draw()

    def rounded_rect(self, x1, y1, x2, y2, radius, **kwargs):
        points = [
            x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
            x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
            x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1,
        ]
        return self.create_polygon(points, smooth=True, **kwargs)

    def draw(self, hover=False):
        self.delete("all")
        width = int(self["width"])
        height = int(self["height"])
        offset = 2 if self.is_pressed else 0
        glow = COLORS["cyan"] if hover else "#10314a"
        shade = COLORS["cyan2"] if hover else "#05111d"
        self.rounded_rect(5, 6, width - 2, height - 1, self.radius, fill=shade, outline="")
        self.rounded_rect(2 + offset, 2 + offset, width - 5 + offset, height - 6 + offset, self.radius, fill=self.fill, outline=glow, width=1)
        self.create_line(12 + offset, 8 + offset, width - 18 + offset, 8 + offset, fill="#ffffff", width=1)
        self.create_text(width / 2 + offset, height / 2 - 1 + offset, text=self.text, fill=self.fg, font=("Microsoft YaHei", 9, "bold"))

    def press(self, _event):
        self.is_pressed = True
        self.draw()

    def release(self, _event):
        self.is_pressed = False
        self.draw(hover=True)
        if self.command:
            self.command()


class CopyApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"文案中枢 {APP_VERSION}")
        self.geometry("1280x780")
        self.minsize(1120, 680)
        self.configure(bg=COLORS["bg"])

        self.items: list[dict] = []
        self.categories: dict[str, list[str]] = {}
        self.selected_category = "全部"
        self.selected_subcategory = ""
        self.keyword_var = tk.StringVar()
        self.category_var = tk.StringVar()
        self.subcategory_var = tk.StringVar()
        self.expanded_categories: set[str] = set()
        self.category_rows: list[tuple[str, str, str]] = []
        self.preview_text = None
        self.preview_meta = None
        self.preview_title = None

        self.style = ttk.Style(self)
        self.style.theme_use("clam")
        self.configure_styles()
        self.build_layout()
        self.refresh_data()
        self.after(1400, self.check_for_updates)

    def configure_styles(self):
        self.style.configure(
            "Neon.Treeview",
            background=COLORS["panel"],
            foreground=COLORS["ink"],
            fieldbackground=COLORS["panel"],
            rowheight=40,
            borderwidth=0,
            font=("Microsoft YaHei", 10),
        )
        self.style.configure(
            "Neon.Treeview.Heading",
            background=COLORS["panel2"],
            foreground=COLORS["cyan"],
            borderwidth=0,
            font=("Microsoft YaHei", 10, "bold"),
        )
        self.style.map("Neon.Treeview", background=[("selected", COLORS["cyan2"])], foreground=[("selected", "#ffffff")])
        self.style.configure("TCombobox", fieldbackground=COLORS["input"], background=COLORS["panel2"], foreground=COLORS["ink"])

    def build_layout(self):
        self.bg_canvas = tk.Canvas(self, bg=COLORS["bg"], highlightthickness=0)
        self.bg_canvas.place(x=0, y=0, relwidth=1, relheight=1)
        self.bind("<Configure>", self.draw_background)

        shell = tk.Frame(self, bg=COLORS["bg"])
        shell.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)

        sidebar = tk.Frame(shell, bg=COLORS["panel3"], width=286, highlightthickness=1, highlightbackground=COLORS["line"])
        sidebar.pack(side=tk.LEFT, fill=tk.Y)
        sidebar.pack_propagate(False)

        brand = tk.Frame(sidebar, bg=COLORS["panel3"])
        brand.pack(fill=tk.X, padx=20, pady=(22, 18))

        mark = tk.Canvas(brand, width=54, height=54, bg=COLORS["panel3"], highlightthickness=0)
        mark.pack(side=tk.LEFT)
        mark.create_rectangle(5, 5, 49, 49, outline=COLORS["cyan"], width=2)
        mark.create_rectangle(10, 10, 44, 44, outline=COLORS["line2"], width=1)
        mark.create_line(12, 40, 24, 16, 34, 32, 44, 12, fill=COLORS["green"], width=2)
        mark.create_text(26, 27, text="AI", fill=COLORS["ink"], font=("Consolas", 13, "bold"))

        title = tk.Frame(brand, bg=COLORS["panel3"])
        title.pack(side=tk.LEFT, padx=12)
        tk.Label(title, text="文案中枢", bg=COLORS["panel3"], fg=COLORS["ink"], font=("Microsoft YaHei", 20, "bold")).pack(anchor="w")
        tk.Label(title, text=f"文案管理中心 · {APP_VERSION}", bg=COLORS["panel3"], fg=COLORS["cyan"], font=("Microsoft YaHei", 9)).pack(anchor="w")

        self.stats_label = tk.Label(sidebar, text="", bg=COLORS["panel3"], fg=COLORS["muted"], font=("Microsoft YaHei", 10))
        self.stats_label.pack(anchor="w", padx=22, pady=(0, 16))

        self.make_action_button(sidebar, "+ 新增大分类", self.open_category_window, COLORS["cyan"], width=230).pack(padx=18, pady=(0, 10))
        self.make_action_button(sidebar, "修改大项名称", self.open_rename_category_window, COLORS["yellow"], width=230).pack(padx=18, pady=(0, 10))
        self.make_action_button(sidebar, "+ 新增子项目", self.open_subcategory_window, COLORS["green"], width=230).pack(padx=18, pady=(0, 14))

        tk.Label(sidebar, text="分类文件夹", bg=COLORS["panel3"], fg=COLORS["muted"], font=("Microsoft YaHei", 10, "bold")).pack(anchor="w", padx=22, pady=(4, 8))

        category_frame = tk.Frame(sidebar, bg=COLORS["input"], highlightthickness=1, highlightbackground=COLORS["line"])
        category_frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 16))

        self.category_list = tk.Listbox(
            category_frame,
            bd=0,
            highlightthickness=0,
            bg=COLORS["input"],
            fg=COLORS["ink"],
            selectbackground=COLORS["cyan2"],
            selectforeground="#ffffff",
            activestyle="none",
            font=("Microsoft YaHei", 10),
        )
        self.category_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.category_list.bind("<<ListboxSelect>>", self.on_category_select)

        category_scroll = ttk.Scrollbar(category_frame, orient=tk.VERTICAL, command=self.category_list.yview)
        category_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.category_list.configure(yscrollcommand=category_scroll.set)

        main = tk.Frame(shell, bg=COLORS["bg"])
        main.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(18, 0))

        header = tk.Frame(main, bg=COLORS["bg"])
        header.pack(fill=tk.X, pady=(0, 12))

        left_header = tk.Frame(header, bg=COLORS["bg"])
        left_header.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.heading = tk.Label(left_header, text="全部文案", bg=COLORS["bg"], fg=COLORS["ink"], font=("Microsoft YaHei", 24, "bold"))
        self.heading.pack(anchor="w")
        self.summary = tk.Label(left_header, text="", bg=COLORS["bg"], fg=COLORS["muted"], font=("Microsoft YaHei", 10))
        self.summary.pack(anchor="w", pady=(4, 0))

        self.make_action_button(header, "添加文案", self.open_add_window, COLORS["green"], width=145, height=48).pack(side=tk.RIGHT)
        tk.Label(
            header,
            text=f"版本 {APP_VERSION}",
            bg=COLORS["bg"],
            fg=COLORS["green"],
            font=("Microsoft YaHei", 11, "bold"),
        ).pack(side=tk.RIGHT, padx=(0, 16))

        search_panel = tk.Frame(main, bg=COLORS["input"], highlightthickness=1, highlightbackground=COLORS["line2"])
        search_panel.pack(fill=tk.X, pady=(0, 12))
        tk.Label(search_panel, text="搜索", bg=COLORS["input"], fg=COLORS["cyan"], font=("Microsoft YaHei", 10, "bold")).pack(side=tk.LEFT, padx=(16, 10))
        search_entry = tk.Entry(
            search_panel,
            textvariable=self.keyword_var,
            bd=0,
            bg=COLORS["input"],
            fg=COLORS["ink"],
            insertbackground=COLORS["cyan"],
            font=("Microsoft YaHei", 13),
        )
        search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=12, padx=(0, 14))
        search_entry.bind("<KeyRelease>", lambda _event: self.render_items())

        workbench = tk.Frame(main, bg=COLORS["bg"])
        workbench.pack(fill=tk.BOTH, expand=True)

        preview = tk.Frame(workbench, bg=COLORS["panel3"], width=300, highlightthickness=1, highlightbackground=COLORS["line2"])
        preview.pack(side=tk.RIGHT, fill=tk.Y, padx=(14, 0))
        preview.pack_propagate(False)

        preview_head = tk.Frame(preview, bg=COLORS["panel3"])
        preview_head.pack(fill=tk.X, padx=16, pady=(16, 10))
        tk.Label(preview_head, text="文案预览", bg=COLORS["panel3"], fg=COLORS["ink"], font=("Microsoft YaHei", 16, "bold")).pack(anchor="w")
        self.preview_meta = tk.Label(preview_head, text="点击一条文案查看完整内容", bg=COLORS["panel3"], fg=COLORS["muted"], font=("Microsoft YaHei", 9), wraplength=252, justify=tk.LEFT)
        self.preview_meta.pack(anchor="w", pady=(4, 0))

        self.preview_title = tk.Label(preview, text="未选择", bg=COLORS["panel3"], fg=COLORS["cyan"], font=("Microsoft YaHei", 10, "bold"), wraplength=252, justify=tk.LEFT)
        self.preview_title.pack(anchor="w", padx=16, pady=(0, 8))

        preview_body = tk.Frame(preview, bg=COLORS["input"], highlightthickness=1, highlightbackground=COLORS["line"])
        preview_body.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 14))
        self.preview_text = tk.Text(preview_body, bd=0, bg=COLORS["input"], fg=COLORS["ink"], insertbackground=COLORS["cyan"], font=("Microsoft YaHei", 10), padx=12, pady=12, wrap=tk.WORD)
        self.preview_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        preview_scroll = ttk.Scrollbar(preview_body, orient=tk.VERTICAL, command=self.preview_text.yview)
        preview_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.preview_text.configure(yscrollcommand=preview_scroll.set, state=tk.DISABLED)

        preview_actions = tk.Frame(preview, bg=COLORS["panel3"])
        preview_actions.pack(fill=tk.X, padx=16, pady=(0, 16))
        self.make_action_button(preview_actions, "复制", self.copy_selected, COLORS["green"], width=82, height=38).pack(side=tk.LEFT)
        self.make_action_button(preview_actions, "编辑", self.preview_selected, COLORS["cyan"], width=82, height=38).pack(side=tk.LEFT, padx=7)
        self.make_action_button(preview_actions, "删除", self.delete_selected, COLORS["red"], fg="#ffffff", width=82, height=38).pack(side=tk.LEFT)

        table_wrap = tk.Frame(workbench, bg=COLORS["panel"], highlightthickness=1, highlightbackground=COLORS["line"])
        table_wrap.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        columns = ("category", "subcategory", "text")
        self.tree = ttk.Treeview(table_wrap, columns=columns, show="headings", selectmode="extended", style="Neon.Treeview")
        self.tree.heading("category", text="分类")
        self.tree.heading("subcategory", text="标签")
        self.tree.heading("text", text="文案内容")
        self.tree.column("category", width=104, minwidth=82, stretch=False)
        self.tree.column("subcategory", width=108, minwidth=78, stretch=False)
        self.tree.column("text", width=430, minwidth=260, stretch=True)
        self.tree.tag_configure("odd", background="#0b1b2f")
        self.tree.tag_configure("even", background="#0d2036")
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(1, 0), pady=1)
        self.tree.bind("<Double-1>", lambda _event: self.preview_selected())
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self.update_preview_panel())

        scrollbar = ttk.Scrollbar(table_wrap, orient=tk.VERTICAL, command=self.tree.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.configure(yscrollcommand=scrollbar.set)

        actions = tk.Frame(main, bg=COLORS["bg"])
        actions.pack(fill=tk.X, pady=(14, 0))

        self.make_action_button(actions, "弹窗预览", self.preview_selected, COLORS["cyan"], width=130).pack(side=tk.LEFT)
        self.make_action_button(actions, "修改标签", self.open_change_tag_window, COLORS["yellow"], width=120).pack(side=tk.LEFT, padx=10)
        self.make_action_button(actions, "删除选中", self.delete_selected, COLORS["red"], fg="#ffffff", width=130).pack(side=tk.LEFT)
        self.make_action_button(actions, "导出 TXT", self.export_txt, COLORS["green"], width=120).pack(side=tk.LEFT, padx=10)
        self.make_action_button(actions, "建快捷方式", self.create_desktop_shortcut, COLORS["cyan"], width=130).pack(side=tk.LEFT)
        self.make_action_button(actions, "重新读取", self.refresh_data, COLORS["panel2"], fg=COLORS["ink"], width=120).pack(side=tk.LEFT, padx=10)

    def draw_background(self, _event=None):
        self.bg_canvas.delete("all")
        width = max(self.winfo_width(), 1)
        height = max(self.winfo_height(), 1)
        self.bg_canvas.create_rectangle(0, 0, width, height, fill=COLORS["bg"], outline="")
        for x in range(0, width, 48):
            self.bg_canvas.create_line(x, 0, x, height, fill="#0b2238", width=1)
        for y in range(0, height, 48):
            self.bg_canvas.create_line(0, y, width, y, fill="#0b2238", width=1)
        self.bg_canvas.create_oval(width - 280, -170, width + 150, 260, outline="#123b5a", width=2)
        self.bg_canvas.create_oval(-170, height - 260, 250, height + 150, outline="#123b5a", width=2)

    def make_action_button(self, parent, text, command, bg, fg="#03101a", width=140, height=42):
        return RoundButton(parent, text, command, bg, fg=fg, width=width, height=height)

    def check_for_updates(self, silent=True):
        def worker():
            try:
                request = urllib.request.Request(UPDATE_API_URL, headers={"User-Agent": "wenan-app-updater"})
                with urllib.request.urlopen(request, timeout=8) as response:
                    release = json.loads(response.read().decode("utf-8"))
                remote_version = release.get("tag_name", "").strip()
                if not remote_version or not is_newer_version(remote_version, APP_VERSION):
                    return
                assets = release.get("assets", [])
                asset = next((item for item in assets if item.get("name") == UPDATE_ASSET_NAME), None)
                if not asset:
                    return
                self.after(0, lambda: self.prompt_update(remote_version, release.get("body", ""), asset["browser_download_url"]))
            except Exception:
                if not silent:
                    self.after(0, lambda: messagebox.showinfo("检查更新", "暂时无法连接 GitHub 检查更新"))

        threading.Thread(target=worker, daemon=True).start()

    def prompt_update(self, remote_version: str, release_note: str, download_url: str):
        note = release_note.strip() or "发现新版程序，建议更新后继续使用。"
        ok = messagebox.askyesno(
            "发现新版本",
            f"当前版本：{APP_VERSION}\n最新版本：{remote_version}\n\n更新内容：\n{note}\n\n是否现在下载并更新？",
        )
        if ok:
            self.open_update_progress(remote_version, download_url)

    def open_update_progress(self, remote_version: str, download_url: str):
        win = tk.Toplevel(self)
        win.title("正在更新")
        win.geometry("520x260")
        win.resizable(False, False)
        win.configure(bg=COLORS["panel"])
        win.transient(self)
        win.grab_set()

        tk.Label(win, text=f"正在下载 {remote_version}", bg=COLORS["panel"], fg=COLORS["ink"], font=("Microsoft YaHei", 17, "bold")).pack(anchor="w", padx=22, pady=(22, 6))
        status_var = tk.StringVar(value="连接 GitHub...")
        tk.Label(win, textvariable=status_var, bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei", 10), wraplength=470, justify=tk.LEFT).pack(anchor="w", padx=22, pady=(0, 18))

        progress_var = tk.DoubleVar(value=0)
        progress = ttk.Progressbar(win, variable=progress_var, maximum=100)
        progress.pack(fill=tk.X, padx=22, pady=(0, 14))
        percent_var = tk.StringVar(value="0%")
        tk.Label(win, textvariable=percent_var, bg=COLORS["panel"], fg=COLORS["cyan"], font=("Microsoft YaHei", 11, "bold")).pack(anchor="e", padx=22)

        events: queue.Queue[tuple[str, object]] = queue.Queue()

        def downloader():
            try:
                download_dir = Path(tempfile.mkdtemp(prefix="wenan_update_"))
                zip_path = download_dir / UPDATE_ASSET_NAME
                request = urllib.request.Request(download_url, headers={"User-Agent": "wenan-app-updater"})
                with urllib.request.urlopen(request, timeout=20) as response:
                    total = int(response.headers.get("Content-Length") or 0)
                    downloaded = 0
                    with zip_path.open("wb") as file:
                        while True:
                            chunk = response.read(1024 * 128)
                            if not chunk:
                                break
                            file.write(chunk)
                            downloaded += len(chunk)
                            percent = min(100, downloaded * 100 / total) if total else 0
                            events.put(("progress", percent))
                events.put(("status", "下载完成，正在准备更新..."))
                update_source = self.extract_update_package(zip_path, download_dir)
                events.put(("done", update_source))
            except Exception as exc:
                events.put(("error", str(exc)))

        def poll():
            try:
                while True:
                    kind, payload = events.get_nowait()
                    if kind == "progress":
                        value = float(payload)
                        progress_var.set(value)
                        percent_var.set(f"{value:.0f}%")
                        status_var.set("正在下载更新包...")
                    elif kind == "status":
                        status_var.set(str(payload))
                    elif kind == "done":
                        progress_var.set(100)
                        percent_var.set("100%")
                        status_var.set("准备重启并更新程序...")
                        self.after(600, lambda: self.apply_update_and_restart(Path(str(payload))))
                        return
                    elif kind == "error":
                        win.destroy()
                        messagebox.showerror("更新失败", f"下载或安装更新失败：\n{payload}")
                        return
            except queue.Empty:
                pass
            win.after(120, poll)

        threading.Thread(target=downloader, daemon=True).start()
        poll()

    def extract_update_package(self, zip_path: Path, download_dir: Path) -> Path:
        extract_dir = download_dir / "package"
        with zipfile.ZipFile(zip_path, "r") as archive:
            archive.extractall(extract_dir)
        if (extract_dir / APP_EXE_NAME).exists() or (extract_dir / "app.py").exists():
            return extract_dir
        matches = [path.parent for path in extract_dir.rglob(APP_EXE_NAME)]
        if not matches:
            matches = [path.parent for path in extract_dir.rglob("app.py")]
        if not matches:
            raise ValueError("更新包中没有找到主程序")
        return matches[0]

    def apply_update_and_restart(self, update_source: Path):
        updater = Path(tempfile.gettempdir()) / "wenan_app_apply_update.bat"
        restart_exe = APP_DIR / APP_EXE_NAME
        launcher = APP_DIR / "启动文案小程序.bat"
        if getattr(sys, "frozen", False):
            restart_command = f'start "" "{Path(sys.executable)}"'
        elif restart_exe.exists():
            restart_command = f'start "" "{restart_exe}"'
        elif launcher.exists():
            restart_command = f'start "" "{launcher}"'
        else:
            pythonw = Path(sys.executable)
            if pythonw.name.lower() == "python.exe":
                candidate = pythonw.with_name("pythonw.exe")
                if candidate.exists():
                    pythonw = candidate
            restart_command = f'start "" "{pythonw}" "{APP_DIR / "app.py"}"'
        script = f"""@echo off
chcp 65001 >nul
timeout /t 2 /nobreak >nul
robocopy "{update_source}" "{APP_DIR}" /E /XD data __pycache__ .git backup_* /XF wenan_app_update.zip "秘钥生成器.py" "激活码生成器.py" "启动激活码生成器.bat" "启动激活码生成器.ps1" "秘钥生成器图标.ico" "秘钥生成器图标.png" >nul
if exist "{update_source / 'data'}" robocopy "{update_source / 'data'}" "{COPY_DIR}" /MIR >nul
{restart_command}
del "%~f0"
"""
        updater.write_text(script, encoding="utf-8")
        subprocess.Popen(["cmd", "/c", str(updater)], creationflags=subprocess.CREATE_NEW_CONSOLE)
        self.destroy()

    def refresh_data(self):
        self.items = load_items()
        self.categories = load_categories()
        self.expanded_categories.intersection_update(self.categories.keys())
        self.render_categories()
        self.render_items()

    def render_categories(self):
        scroll_position = self.category_list.yview()[0] if self.category_list.size() else 0
        self.category_list.delete(0, tk.END)
        if self.selected_subcategory and self.selected_category in self.categories:
            self.expanded_categories.add(self.selected_category)
        self.category_rows = [("all", "全部", "")]
        counts = {name: sum(1 for item in self.items if item["category"] == name) for name in self.categories}

        self.category_list.insert(tk.END, f"  全部  [{len(self.items)}]")
        for category, subs in self.categories.items():
            has_subs = bool(subs)
            arrow = "▾" if category in self.expanded_categories and has_subs else "▸" if has_subs else " "
            self.category_rows.append(("category", category, ""))
            self.category_list.insert(tk.END, f" {arrow} {category}  [{counts.get(category, 0)}]")
            if category in self.expanded_categories:
                for sub in subs:
                    sub_count = sum(1 for item in self.items if item["category"] == category and item.get("subcategory", "") == sub)
                    self.category_rows.append(("subcategory", category, sub))
                    self.category_list.insert(tk.END, f"      └ {sub}  [{sub_count}]")

        index = 0
        for row_index, (kind, category, subcategory) in enumerate(self.category_rows):
            if self.selected_category == "全部" and kind == "all":
                index = row_index
                break
            if self.selected_category == category and self.selected_subcategory == subcategory:
                if (not subcategory and kind == "category") or (subcategory and kind == "subcategory"):
                    index = row_index
                    break
        else:
            index = 0
            self.selected_category = "全部"
            self.selected_subcategory = ""

        self.category_list.selection_clear(0, tk.END)
        self.category_list.selection_set(index)
        self.category_list.activate(index)
        self.category_list.yview_moveto(scroll_position)
        self.stats_label.config(text=f"{len(self.categories)} 个大分类 / {len(self.items)} 条文案")

    def on_category_select(self, _event):
        scroll_position = self.category_list.yview()[0]
        selection = self.category_list.curselection()
        if not selection:
            return
        index = selection[0]
        if index >= len(self.category_rows):
            return

        kind, category, subcategory = self.category_rows[index]
        if kind == "all":
            self.selected_category = "全部"
            self.selected_subcategory = ""
        elif kind == "category":
            self.selected_category = category
            self.selected_subcategory = ""
            if self.categories.get(category):
                if category in self.expanded_categories:
                    self.expanded_categories.remove(category)
                else:
                    self.expanded_categories.add(category)
        else:
            self.selected_category = category
            self.selected_subcategory = subcategory
            self.expanded_categories.add(category)

        self.render_categories()
        self.category_list.yview_moveto(scroll_position)
        self.render_items()

    def close_subcategory_popup(self):
        if self.subcategory_popup and self.subcategory_popup.winfo_exists():
            self.subcategory_popup.destroy()
        self.subcategory_popup = None

    def open_subcategory_dropdown(self, category_index: int):
        current_category = self.selected_category
        subs = self.categories.get(current_category, [])
        if not subs:
            return

        self.close_subcategory_popup()

        win = tk.Toplevel(self)
        self.subcategory_popup = win
        win.overrideredirect(True)
        win.configure(bg=COLORS["cyan"])
        win.attributes("-topmost", True)

        bbox = self.category_list.bbox(category_index)
        list_x = self.category_list.winfo_rootx()
        list_y = self.category_list.winfo_rooty()
        if bbox:
            x = list_x + bbox[0] + 18
            y = list_y + bbox[1] + bbox[3] + 4
        else:
            x = list_x + 18
            y = list_y + 60

        width = 220
        height = min(430, 44 + (len(subs) + 1) * 34)
        screen_height = self.winfo_screenheight()
        if y + height > screen_height - 30:
            y = max(30, screen_height - height - 30)
        win.geometry(f"{width}x{height}+{x}+{y}")

        shell = tk.Frame(win, bg=COLORS["panel"], highlightthickness=1, highlightbackground=COLORS["cyan"])
        shell.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

        header = tk.Frame(shell, bg=COLORS["panel2"])
        header.pack(fill=tk.X)
        tk.Label(header, text="选择标签", bg=COLORS["panel2"], fg=COLORS["cyan"], font=("Microsoft YaHei", 10, "bold")).pack(side=tk.LEFT, padx=10, pady=8)
        tk.Button(
            header,
            text="×",
            command=self.close_subcategory_popup,
            bg=COLORS["panel2"],
            fg=COLORS["muted"],
            activebackground=COLORS["red"],
            activeforeground="#ffffff",
            bd=0,
            width=3,
            cursor="hand2",
        ).pack(side=tk.RIGHT)

        body = tk.Frame(shell, bg=COLORS["panel"])
        body.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        listbox = tk.Listbox(
            body,
            bd=0,
            highlightthickness=0,
            bg=COLORS["input"],
            fg=COLORS["ink"],
            selectbackground=COLORS["cyan2"],
            selectforeground="#ffffff",
            activestyle="none",
            font=("Microsoft YaHei", 10),
        )
        listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scroll = ttk.Scrollbar(body, orient=tk.VERTICAL, command=listbox.yview)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        listbox.configure(yscrollcommand=scroll.set)

        all_label = f"全部{current_category.replace('文案', '')}"
        for name in [all_label] + subs:
            listbox.insert(tk.END, f"  {name}")

        def choose(name: str):
            self.selected_category = current_category
            self.selected_subcategory = "" if name == all_label else name
            self.render_items()
            self.close_subcategory_popup()

        def on_choose(_event=None):
            selection = listbox.curselection()
            if not selection:
                return
            raw = listbox.get(selection[0]).strip()
            choose(raw)

        def on_wheel(event):
            listbox.yview_scroll(int(-1 * (event.delta / 120)), "units")
            return "break"

        listbox.bind("<Double-1>", on_choose)
        listbox.bind("<Return>", on_choose)
        listbox.bind("<MouseWheel>", on_wheel)
        listbox.selection_set(0)
        listbox.focus_set()
        win.bind("<Escape>", lambda _event: self.close_subcategory_popup())
        win.bind("<FocusOut>", lambda _event: self.after(120, self.close_subcategory_popup))

    def filtered_items(self) -> list[dict]:
        keyword = self.keyword_var.get().strip().lower()
        result = []
        for item in self.items:
            category_ok = self.selected_category == "全部" or item["category"] == self.selected_category
            sub_ok = not self.selected_subcategory or item["subcategory"] == self.selected_subcategory
            haystack = f"{item['category']} {item['subcategory']} {item['text']}".lower()
            keyword_ok = not keyword or keyword in haystack
            if category_ok and sub_ok and keyword_ok:
                result.append(item)
        return result

    def render_items(self):
        for row in self.tree.get_children():
            self.tree.delete(row)

        items = self.filtered_items()
        for index, item in enumerate(items):
            tag = item["subcategory"] or "-"
            row_tag = "even" if index % 2 == 0 else "odd"
            self.tree.insert("", tk.END, iid=str(index), values=(item["category"], tag, item["text"]), tags=(row_tag,))

        label = self.selected_subcategory or self.selected_category
        self.heading.config(text=f"{label}文案" if label != "全部" and not label.endswith("文案") else ("全部文案" if label == "全部" else label))
        self.summary.config(text=f"当前筛选 {len(items)} 条 · 可按 Ctrl/Shift 多选 · 双击文案可预览 · 数据保存在程序内部")
        self.update_preview_panel()

    def update_preview_panel(self):
        if not self.preview_text or not self.preview_meta or not self.preview_title:
            return
        items = self.selected_items()
        self.preview_text.configure(state=tk.NORMAL)
        self.preview_text.delete("1.0", tk.END)
        if not items:
            self.preview_title.config(text="未选择")
            self.preview_meta.config(text="点击一条文案查看完整内容")
            self.preview_text.insert(tk.END, "这里会显示选中文案的完整内容。")
            self.preview_text.configure(state=tk.DISABLED)
            return

        first = items[0]
        tag = first["subcategory"] or "无标签"
        self.preview_title.config(text=f"{first['category']} / {tag}")
        if len(items) == 1:
            self.preview_meta.config(text=f"第 {first['number']} 条 · 可复制、编辑或删除")
            content = first["text"]
        else:
            meta = "、".join(sorted({item["subcategory"] or item["category"] for item in items}))
            self.preview_meta.config(text=f"已选择 {len(items)} 条 · {meta}")
            content = "\n\n".join(item["text"] for item in items)
        self.preview_text.insert(tk.END, content)
        self.preview_text.configure(state=tk.DISABLED)

    def selected_item(self):
        items = self.selected_items()
        return items[0] if items else None

    def selected_items(self):
        selected = self.tree.selection()
        if not selected:
            return []
        items = self.filtered_items()
        result = []
        for selected_id in selected:
            index = int(selected_id)
            if index < len(items):
                result.append(items[index])
        return result

    def copy_selected(self):
        items = self.selected_items()
        if not items:
            messagebox.showinfo("提示", "请先选择文案")
            return
        self.clipboard_clear()
        self.clipboard_append("\n".join(item["text"] for item in items))
        self.update()
        messagebox.showinfo("已复制", f"已复制 {len(items)} 条文案到剪贴板")

    def export_txt(self):
        items = self.selected_items()
        export_scope = "选中"
        if not items:
            items = self.filtered_items()
            export_scope = "当前筛选"
        if not items:
            messagebox.showinfo("提示", "没有可导出的文案")
            return

        label = self.selected_subcategory or self.selected_category or "文案"
        safe_label = re.sub(r'[\\/:*?"<>|]+', "_", label).strip() or "文案"
        default_name = f"{safe_label}_{export_scope}_{len(items)}条.txt"
        target = filedialog.asksaveasfilename(
            title="导出 TXT",
            defaultextension=".txt",
            initialfile=default_name,
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")],
        )
        if not target:
            return

        lines = []
        for index, item in enumerate(items, start=1):
            lines.append(f"{index}. {item['text'].strip()}")
        try:
            write_text(Path(target), "\n\n".join(lines) + "\n")
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc))
            return
        messagebox.showinfo("导出完成", f"已导出 {len(items)} 条文案到：\n{target}")

    def create_desktop_shortcut(self):
        try:
            desktop = Path.home() / "Desktop"
            if not desktop.exists():
                desktop = Path(r"D:\桌面")
            shortcut = desktop / "文案小程序.lnk"
            exe = APP_DIR / APP_EXE_NAME
            if exe.exists():
                launcher = exe
            else:
                launcher = APP_DIR / "启动文案小程序.bat"
                if not launcher.exists():
                    launcher = APP_DIR / "start_app.bat"
            icon = APP_DIR / "app.ico"
            powershell = (
                "$shell = New-Object -ComObject WScript.Shell; "
                f"$link = $shell.CreateShortcut('{shortcut}'); "
                f"$link.TargetPath = '{launcher}'; "
                "$link.Arguments = ''; "
                f"$link.WorkingDirectory = '{APP_DIR}'; "
                f"$link.IconLocation = '{icon}'; "
                "$link.Description = '文案中枢'; "
                "$link.Save();"
            )
            subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", powershell],
                check=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except Exception as exc:
            messagebox.showerror("创建失败", f"桌面快捷方式创建失败：\n{exc}")
            return
        messagebox.showinfo("创建成功", f"已创建桌面快捷方式：\n{shortcut}")

    def preview_selected(self):
        items = self.selected_items()
        if not items:
            messagebox.showinfo("提示", "请先选择文案")
            return

        win = self.dialog_base("文案预览", "文案预览", "820x680")
        body = tk.Frame(win, bg=COLORS["panel"])
        body.pack(fill=tk.BOTH, expand=True, padx=22, pady=(0, 20))

        buttons = tk.Frame(body, bg=COLORS["panel"])
        buttons.pack(side=tk.BOTTOM, fill=tk.X, pady=(12, 0))

        meta = "、".join(sorted({item["subcategory"] or item["category"] for item in items}))
        tk.Label(
            body,
            text=f"已选择 {len(items)} 条 · {meta}",
            bg=COLORS["panel"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei", 10),
            wraplength=700,
            justify=tk.LEFT,
        ).pack(anchor="w", pady=(0, 10))

        preview_frame = tk.Frame(body, bg=COLORS["input"], highlightthickness=1, highlightbackground=COLORS["line"])
        preview_frame.pack(fill=tk.BOTH, expand=True)
        text_box = tk.Text(
            preview_frame,
            bd=0,
            bg=COLORS["input"],
            fg=COLORS["ink"],
            insertbackground=COLORS["cyan"],
            font=("Microsoft YaHei", 10),
            padx=12,
            pady=10,
            wrap=tk.WORD,
        )
        text_box.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar = ttk.Scrollbar(preview_frame, orient=tk.VERTICAL, command=text_box.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        text_box.configure(yscrollcommand=scrollbar.set)

        content = "\n\n".join(item["text"] for item in items)
        text_box.insert(tk.END, content)
        text_box.configure(state=tk.DISABLED)

        def copy_preview():
            self.clipboard_clear()
            self.clipboard_append(content)
            self.update()
            messagebox.showinfo("已复制", f"已复制 {len(items)} 条文案到剪贴板")

        def enable_edit():
            if len(items) != 1:
                messagebox.showinfo("提示", "一次只能编辑一条文案")
                return
            text_box.configure(state=tk.NORMAL)
            text_box.focus_set()
            edit_button.pack_forget()
            save_button.pack(side=tk.RIGHT, padx=10)

        def save_edit():
            try:
                update_copy_item(items[0], text_box.get("1.0", tk.END))
            except ValueError as exc:
                messagebox.showwarning("提示", str(exc))
                return
            except Exception as exc:
                messagebox.showerror("保存失败", str(exc))
                return
            win.destroy()
            self.refresh_data()
            messagebox.showinfo("已保存", "文案内容已更新")

        self.make_action_button(buttons, "复制", copy_preview, COLORS["green"], width=105).pack(side=tk.RIGHT)
        self.make_action_button(buttons, "关闭", win.destroy, COLORS["panel2"], fg=COLORS["ink"], width=105).pack(side=tk.RIGHT, padx=10)
        edit_button = self.make_action_button(buttons, "编辑", enable_edit, COLORS["cyan"], width=105)
        save_button = self.make_action_button(buttons, "保存修改", save_edit, COLORS["yellow"], width=125)
        edit_button.pack(side=tk.LEFT)

    def delete_selected(self):
        items = self.selected_items()
        if not items:
            messagebox.showinfo("提示", "请先选择文案")
            return
        preview = "\n".join(f"- {item['text']}" for item in items[:5])
        more = "" if len(items) <= 5 else f"\n……还有 {len(items) - 5} 条"
        ok = messagebox.askyesno("确认删除", f"确定删除选中的 {len(items)} 条文案吗？\n\n{preview}{more}")
        if not ok:
            return
        try:
            removed = delete_copy_items(items)
        except ValueError as exc:
            messagebox.showwarning("提示", str(exc))
            return
        except Exception as exc:
            messagebox.showerror("删除失败", str(exc))
            return
        self.refresh_data()
        messagebox.showinfo("已删除", f"已删除 {removed} 条文案")

    def open_change_tag_window(self):
        items = self.selected_items()
        if not items:
            messagebox.showinfo("提示", "请先选择文案")
            return
        categories = {item["category"] for item in items}
        if len(categories) != 1:
            messagebox.showwarning("提示", "批量修改标签时，请选择同一个大类里的文案")
            return

        category = next(iter(categories))
        win = self.dialog_base("修改标签", "修改标签", "520x360")
        body = tk.Frame(win, bg=COLORS["panel"])
        body.pack(fill=tk.BOTH, expand=True, padx=22, pady=(0, 20))

        old_tags = "、".join(sorted({item["subcategory"] or "无标签" for item in items}))
        tk.Label(
            body,
            text=f"已选择 {len(items)} 条 · 大类：{category} · 当前标签：{old_tags}",
            bg=COLORS["panel"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei", 10),
            wraplength=460,
            justify=tk.LEFT,
        ).pack(anchor="w", pady=(0, 12))

        tag_var = tk.StringVar()
        new_tag_var = tk.StringVar()
        tag_values = ["无标签"] + self.categories.get(category, [])

        tk.Label(body, text="选择已有标签", bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei", 10)).pack(anchor="w")
        tag_combo = ttk.Combobox(body, textvariable=tag_var, values=tag_values, state="readonly")
        tag_combo.pack(fill=tk.X, pady=(6, 12), ipady=5)
        tag_var.set(tag_values[0])

        tk.Label(body, text="或输入新标签", bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei", 10)).pack(anchor="w")
        new_entry = tk.Entry(body, textvariable=new_tag_var, bd=0, bg=COLORS["input"], fg=COLORS["ink"], insertbackground=COLORS["cyan"], font=("Microsoft YaHei", 11))
        new_entry.pack(fill=tk.X, ipady=10, pady=(6, 18))

        buttons = tk.Frame(body, bg=COLORS["panel"])
        buttons.pack(fill=tk.X)

        def save():
            target_tag = new_tag_var.get().strip() or tag_var.get().strip()
            if target_tag == "无标签":
                target_tag = ""
            if any(char in target_tag for char in r'\/:*?"<>|'):
                messagebox.showwarning("提示", "标签不能包含这些字符：\\ / : * ? \" < > |")
                return
            try:
                moved = move_copy_items(items, category, target_tag)
            except Exception as exc:
                messagebox.showerror("修改失败", str(exc))
                return
            self.selected_category = category
            self.selected_subcategory = target_tag
            win.destroy()
            self.refresh_data()
            messagebox.showinfo("已修改", f"已修改 {moved} 条文案的标签")

        self.make_action_button(buttons, "保存", save, COLORS["green"], width=105).pack(side=tk.RIGHT)
        self.make_action_button(buttons, "取消", win.destroy, COLORS["panel2"], fg=COLORS["ink"], width=105).pack(side=tk.RIGHT, padx=10)

    def dialog_base(self, title: str, headline: str, geometry: str):
        win = tk.Toplevel(self)
        win.title(title)
        win.geometry(geometry)
        win.resizable(False, False)
        win.configure(bg=COLORS["panel"])
        win.transient(self)
        win.grab_set()
        tk.Label(win, text=headline, bg=COLORS["panel"], fg=COLORS["ink"], font=("Microsoft YaHei", 17, "bold")).pack(anchor="w", padx=22, pady=(20, 4))
        tk.Label(win, text="数据输入窗口", bg=COLORS["panel"], fg=COLORS["cyan"], font=("Microsoft YaHei", 9)).pack(anchor="w", padx=22, pady=(0, 16))
        return win

    def open_category_window(self):
        win = self.dialog_base("新增大分类", "新增大分类", "440x250")
        body = tk.Frame(win, bg=COLORS["panel"])
        body.pack(fill=tk.BOTH, expand=True, padx=22, pady=(0, 18))
        tk.Label(body, text="分类名称", bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei", 10)).pack(anchor="w")
        name_var = tk.StringVar()
        entry = tk.Entry(body, textvariable=name_var, bd=0, bg=COLORS["input"], fg=COLORS["ink"], insertbackground=COLORS["cyan"], font=("Microsoft YaHei", 12))
        entry.pack(fill=tk.X, ipady=11, pady=(6, 18))
        entry.focus()

        buttons = tk.Frame(body, bg=COLORS["panel"])
        buttons.pack(fill=tk.X)

        def save():
            try:
                ensure_category(name_var.get())
            except ValueError as exc:
                messagebox.showwarning("提示", str(exc))
                return
            self.selected_category = name_var.get().strip()
            self.selected_subcategory = ""
            win.destroy()
            self.refresh_data()
            messagebox.showinfo("已创建", f"已新增大分类：{self.selected_category}")

        self.make_action_button(buttons, "创建", save, COLORS["cyan"]).pack(side=tk.RIGHT)
        self.make_action_button(buttons, "取消", win.destroy, COLORS["panel2"], fg=COLORS["ink"]).pack(side=tk.RIGHT, padx=10)

    def open_rename_category_window(self):
        if not self.categories:
            messagebox.showinfo("提示", "当前还没有大项")
            return

        win = self.dialog_base("修改大项名称", "修改大项名称", "520x330")
        body = tk.Frame(win, bg=COLORS["panel"])
        body.pack(fill=tk.BOTH, expand=True, padx=22, pady=(0, 20))

        category_values = list(self.categories.keys())
        current_category = self.selected_category if self.selected_category in self.categories else category_values[0]
        old_var = tk.StringVar(value=current_category)
        new_var = tk.StringVar(value=current_category)

        tk.Label(body, text="选择大项", bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei", 10)).pack(anchor="w")
        combo = ttk.Combobox(body, textvariable=old_var, values=category_values, state="readonly")
        combo.pack(fill=tk.X, pady=(6, 14), ipady=5)

        tk.Label(body, text="新的大项名称", bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei", 10)).pack(anchor="w")
        entry = tk.Entry(body, textvariable=new_var, bd=0, bg=COLORS["input"], fg=COLORS["ink"], insertbackground=COLORS["cyan"], font=("Microsoft YaHei", 12))
        entry.pack(fill=tk.X, ipady=11, pady=(6, 18))
        entry.focus()
        entry.selection_range(0, tk.END)

        def sync_name(*_args):
            new_var.set(old_var.get())
            entry.selection_range(0, tk.END)

        combo.bind("<<ComboboxSelected>>", sync_name)

        buttons = tk.Frame(body, bg=COLORS["panel"])
        buttons.pack(fill=tk.X)

        def save():
            old_name = old_var.get().strip()
            new_name = new_var.get().strip()
            try:
                rename_category(old_name, new_name)
            except ValueError as exc:
                messagebox.showwarning("提示", str(exc))
                return
            except Exception as exc:
                messagebox.showerror("修改失败", str(exc))
                return

            if old_name in self.expanded_categories:
                self.expanded_categories.remove(old_name)
                self.expanded_categories.add(new_name)
            self.selected_category = new_name
            self.selected_subcategory = ""
            win.destroy()
            self.refresh_data()
            messagebox.showinfo("已修改", f"已将「{old_name}」修改为「{new_name}」")

        self.make_action_button(buttons, "保存", save, COLORS["yellow"]).pack(side=tk.RIGHT)
        self.make_action_button(buttons, "取消", win.destroy, COLORS["panel2"], fg=COLORS["ink"]).pack(side=tk.RIGHT, padx=10)

    def open_subcategory_window(self):
        if not self.categories:
            messagebox.showinfo("提示", "请先新增一个大分类")
            return

        win = self.dialog_base("新增子项目", "新增子项目", "520x330")
        body = tk.Frame(win, bg=COLORS["panel"])
        body.pack(fill=tk.BOTH, expand=True, padx=22, pady=(0, 20))

        category_var = tk.StringVar()
        subcategory_var = tk.StringVar()
        category_values = list(self.categories.keys())
        default_category = self.selected_category if self.selected_category in self.categories else category_values[0]
        category_var.set(default_category)

        tk.Label(body, text="所属大项", bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei", 10)).pack(anchor="w")
        category_combo = ttk.Combobox(body, textvariable=category_var, values=category_values, state="readonly")
        category_combo.pack(fill=tk.X, pady=(6, 14), ipady=5)

        tk.Label(body, text="子项目名称", bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei", 10)).pack(anchor="w")
        entry = tk.Entry(body, textvariable=subcategory_var, bd=0, bg=COLORS["input"], fg=COLORS["ink"], insertbackground=COLORS["cyan"], font=("Microsoft YaHei", 12))
        entry.pack(fill=tk.X, ipady=11, pady=(6, 18))
        entry.focus()

        buttons = tk.Frame(body, bg=COLORS["panel"])
        buttons.pack(fill=tk.X)

        def save():
            try:
                ensure_subcategory(category_var.get(), subcategory_var.get())
            except ValueError as exc:
                messagebox.showwarning("提示", str(exc))
                return
            except Exception as exc:
                messagebox.showerror("创建失败", str(exc))
                return
            self.selected_category = category_var.get()
            self.selected_subcategory = subcategory_var.get().strip()
            win.destroy()
            self.refresh_data()
            messagebox.showinfo("已创建", f"已在「{self.selected_category}」下新增子项目：{self.selected_subcategory}")

        self.make_action_button(buttons, "创建", save, COLORS["green"]).pack(side=tk.RIGHT)
        self.make_action_button(buttons, "取消", win.destroy, COLORS["panel2"], fg=COLORS["ink"]).pack(side=tk.RIGHT, padx=10)

    def open_add_window(self):
        win = self.dialog_base("添加文案", "添加新文案", "640x610")
        form = tk.Frame(win, bg=COLORS["panel"])
        form.pack(fill=tk.BOTH, expand=True, padx=22, pady=(0, 20))

        tk.Label(form, text="分类", bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei", 10)).pack(anchor="w")
        category_values = list(self.categories.keys())
        category_combo = ttk.Combobox(form, textvariable=self.category_var, values=category_values, state="readonly")
        category_combo.pack(fill=tk.X, pady=(6, 12), ipady=5)
        self.category_var.set(self.selected_category if self.selected_category != "全部" else (category_values[0] if category_values else ""))

        tk.Label(form, text="励志标签", bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei", 10)).pack(anchor="w")
        sub_combo = ttk.Combobox(form, textvariable=self.subcategory_var, state="readonly")
        sub_combo.pack(fill=tk.X, pady=(6, 12), ipady=5)

        def refresh_subs(*_args):
            category = self.category_var.get()
            subs = self.categories.get(category, [])
            sub_combo.configure(values=subs)
            if category == INSPIRATION_CATEGORY and subs:
                self.subcategory_var.set(self.selected_subcategory if self.selected_subcategory in subs else subs[0])
                sub_combo.configure(state="readonly")
            else:
                self.subcategory_var.set("")
                sub_combo.configure(state="disabled")

        self.category_var.trace_add("write", refresh_subs)
        refresh_subs()

        tk.Label(form, text="文案内容", bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei", 10)).pack(anchor="w")
        text_box = tk.Text(form, height=9, bd=0, bg=COLORS["input"], fg=COLORS["ink"], insertbackground=COLORS["cyan"], font=("Microsoft YaHei", 11), padx=12, pady=10)
        text_box.pack(fill=tk.BOTH, expand=True, pady=(6, 18))

        buttons = tk.Frame(form, bg=COLORS["panel"])
        buttons.pack(fill=tk.X)

        def save():
            try:
                append_copy(self.category_var.get(), self.subcategory_var.get(), text_box.get("1.0", tk.END))
            except ValueError as exc:
                messagebox.showwarning("提示", str(exc))
                return
            except Exception as exc:
                messagebox.showerror("保存失败", str(exc))
                return
            self.selected_category = self.category_var.get()
            self.selected_subcategory = self.subcategory_var.get()
            win.destroy()
            self.refresh_data()
            messagebox.showinfo("已保存", "文案已保存到对应 TXT 文件")

        self.make_action_button(buttons, "保存", save, COLORS["green"], width=105).pack(side=tk.RIGHT)
        self.make_action_button(buttons, "取消", win.destroy, COLORS["panel2"], fg=COLORS["ink"], width=105).pack(side=tk.RIGHT, padx=10)
        self.make_action_button(buttons, "导入 TXT", lambda: (win.destroy(), self.open_import_window()), COLORS["cyan"], width=125).pack(side=tk.LEFT)

    def open_import_window(self):
        win = self.dialog_base("导入 TXT", "导入 TXT 文案", "660x590")
        body = tk.Frame(win, bg=COLORS["panel"])
        body.pack(fill=tk.BOTH, expand=True, padx=22, pady=(0, 20))

        file_var = tk.StringVar()
        new_category_var = tk.StringVar()
        import_category_var = tk.StringVar(value=self.selected_category if self.selected_category != "全部" else "")
        import_subcategory_var = tk.StringVar(value=self.selected_subcategory)
        auto_tag_var = tk.BooleanVar(value=True)

        tk.Label(body, text="TXT 文件", bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei", 10)).pack(anchor="w")
        file_row = tk.Frame(body, bg=COLORS["panel"])
        file_row.pack(fill=tk.X, pady=(6, 12))
        file_entry = tk.Entry(file_row, textvariable=file_var, bd=0, bg=COLORS["input"], fg=COLORS["ink"], insertbackground=COLORS["cyan"], font=("Microsoft YaHei", 10))
        file_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=10)

        def choose_file():
            path = filedialog.askopenfilename(title="选择 TXT 文件", filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")])
            if path:
                file_var.set(path)

        self.make_action_button(file_row, "选择", choose_file, COLORS["cyan"], width=86, height=38).pack(side=tk.LEFT, padx=(10, 0))

        auto_row = tk.Frame(body, bg=COLORS["panel"])
        auto_row.pack(fill=tk.X, pady=(0, 12))
        tk.Checkbutton(
            auto_row,
            text="自动提取标签",
            variable=auto_tag_var,
            bg=COLORS["panel"],
            fg=COLORS["ink"],
            activebackground=COLORS["panel"],
            activeforeground=COLORS["cyan"],
            selectcolor=COLORS["input"],
            font=("Microsoft YaHei", 10, "bold"),
        ).pack(side=tk.LEFT)
        tk.Label(
            auto_row,
            text="只在所选大类内部加标签，不会分配到其他大类",
            bg=COLORS["panel"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei", 9),
        ).pack(side=tk.LEFT, padx=10)

        tk.Label(body, text="导入到已有大类", bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei", 10)).pack(anchor="w")
        category_values = list(self.categories.keys())
        category_combo = ttk.Combobox(body, textvariable=import_category_var, values=category_values, state="readonly")
        category_combo.pack(fill=tk.X, pady=(6, 12), ipady=5)
        if not import_category_var.get() and category_values:
            import_category_var.set(category_values[0])

        tk.Label(body, text="或输入新大类名称", bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei", 10)).pack(anchor="w")
        new_entry = tk.Entry(body, textvariable=new_category_var, bd=0, bg=COLORS["input"], fg=COLORS["ink"], insertbackground=COLORS["cyan"], font=("Microsoft YaHei", 11))
        new_entry.pack(fill=tk.X, ipady=10, pady=(6, 12))

        tk.Label(body, text="标签（选择已有大类时可选）", bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei", 10)).pack(anchor="w")
        sub_combo = ttk.Combobox(body, textvariable=import_subcategory_var, state="readonly")
        sub_combo.pack(fill=tk.X, pady=(6, 14), ipady=5)

        def refresh_import_subs(*_args):
            category = new_category_var.get().strip() or import_category_var.get()
            subs = self.categories.get(category, [])
            sub_combo.configure(values=subs)
            if subs:
                if import_subcategory_var.get() not in subs:
                    import_subcategory_var.set(subs[0])
                sub_combo.configure(state="readonly")
            else:
                import_subcategory_var.set("")
                sub_combo.configure(state="disabled")

        import_category_var.trace_add("write", refresh_import_subs)
        new_category_var.trace_add("write", refresh_import_subs)
        refresh_import_subs()

        buttons = tk.Frame(body, bg=COLORS["panel"])
        buttons.pack(fill=tk.X)

        def do_import():
            category = new_category_var.get().strip() or import_category_var.get().strip()
            subcategory = "" if new_category_var.get().strip() else import_subcategory_var.get().strip()
            try:
                plan = build_import_plan(Path(file_var.get().strip()), category, subcategory, self.categories, auto_tag_var.get())
            except ValueError as exc:
                messagebox.showwarning("提示", str(exc))
                return
            except Exception as exc:
                messagebox.showerror("预览失败", str(exc))
                return
            win.destroy()
            self.open_import_preview(plan, category, subcategory)

        self.make_action_button(buttons, "开始导入", do_import, COLORS["green"], width=125).pack(side=tk.RIGHT)
        self.make_action_button(buttons, "取消", win.destroy, COLORS["panel2"], fg=COLORS["ink"], width=105).pack(side=tk.RIGHT, padx=10)

    def open_import_preview(self, plan: dict[tuple[str, str], list[str]], selected_category: str, selected_subcategory: str):
        total = sum(len(lines) for lines in plan.values())
        win = self.dialog_base("导入预览", "导入预览", "900x720")
        body = tk.Frame(win, bg=COLORS["panel"])
        body.pack(fill=tk.BOTH, expand=True, padx=22, pady=(0, 20))

        buttons = tk.Frame(body, bg=COLORS["panel"])
        buttons.pack(side=tk.BOTTOM, fill=tk.X, pady=(12, 0))

        summary = "；".join(
            f"{category}/{tag if tag else '无标签'}：{len(lines)}条"
            for (category, tag), lines in plan.items()
        )
        tk.Label(
            body,
            text=f"已识别 {total} 条文案，确认后写入：{summary}",
            bg=COLORS["panel"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei", 10),
            wraplength=760,
            justify=tk.LEFT,
        ).pack(anchor="w", pady=(0, 10))

        preview_frame = tk.Frame(body, bg=COLORS["input"], highlightthickness=1, highlightbackground=COLORS["line"])
        preview_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 0))
        text = tk.Text(
            preview_frame,
            bd=0,
            bg=COLORS["input"],
            fg=COLORS["ink"],
            insertbackground=COLORS["cyan"],
            font=("Microsoft YaHei", 10),
            padx=12,
            pady=10,
            wrap=tk.WORD,
        )
        text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar = ttk.Scrollbar(preview_frame, orient=tk.VERTICAL, command=text.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        text.configure(yscrollcommand=scrollbar.set)

        index = 1
        for (category, tag), lines in plan.items():
            label = f"{category} / {tag if tag else '无标签'}"
            for item in lines:
                text.insert(tk.END, f"{index}. [{label}]\n{item}\n\n")
                index += 1
        text.configure(state=tk.DISABLED)

        def confirm_import():
            try:
                imported = 0
                for (category, tag), lines in plan.items():
                    imported += append_lines(category, tag, lines)
            except Exception as exc:
                messagebox.showerror("导入失败", str(exc))
                return
            self.selected_category = selected_category
            self.selected_subcategory = selected_subcategory
            win.destroy()
            self.refresh_data()
            messagebox.showinfo("导入完成", f"已导入 {imported} 条文案")

        self.make_action_button(buttons, "确认导入", confirm_import, COLORS["green"], width=125).pack(side=tk.RIGHT)
        self.make_action_button(buttons, "取消", win.destroy, COLORS["panel2"], fg=COLORS["ink"], width=105).pack(side=tk.RIGHT, padx=10)


if __name__ == "__main__":
    instance_lock = acquire_single_instance()
    if instance_lock is None:
        bring_existing_window_to_front()
        sys.exit(0)
    if not is_activated() and not show_activation_window():
        sys.exit(0)
    bootstrap_internal_data()
    app = CopyApp()
    app.mainloop()
