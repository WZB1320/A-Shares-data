"""扫描代码库中对 src/stock_data_collector.py 的所有引用

检测目标:
  1. 模块级引用: import stock_data_collector / from src.stock_data_collector import
  2. 类名引用:   StockDataCollector
  3. 顶层导出引用: DB_PATH / LOG_PATH / START_DATE / STOCK_CODES
                  (这些在 stock_data_collector.py 中重复定义,需警惕与 src/config.py 冲突)
  4. 字符串/动态引用: "stock_data_collector" / "StockDataCollector"
  5. 文档/脚本引用: .md / .bat / .ps1 / .sql / .json / .yaml / .txt

输出分级:
  [BLOCK]   代码级 import,删除会直接破坏功能,必须先处理
  [WARN]    字符串/文档引用,需人工确认是否仍需保留
  [INFO]    仅注释/说明性提及,删除安全

用法:
    python scripts/check_stock_data_collector_refs.py
    python scripts/check_stock_data_collector_refs.py --path .
"""
import re
import os
import sys
import argparse
from pathlib import Path
from collections import defaultdict

# Windows 控制台默认 GBK,中文/Unicode 符号会乱码
# 先切换代码页到 UTF-8,再用 reconfigure 让 stdout 走 utf-8
if sys.platform == "win32":
    os.system("chcp 65001 > nul 2>&1")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        # 老版本 Python 无 reconfigure,降级为 TextIOWrapper
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


# 项目根目录(脚本位于 scripts/ 下)
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 跳过的目录(与本项目 .gitignore 及常识对齐)
SKIP_DIRS = {
    ".git", "__pycache__", ".venv", "venv", "env",
    ".idea", ".vscode", ".pytest_cache", ".mypy_cache",
    "data", "logs", "node_modules",
}

# 跳过的文件扩展名(二进制/临时)
SKIP_EXTENSIONS = {".pyc", ".pyo", ".so", ".dll", ".exe", ".db", ".duckdb",
                   ".parquet", ".png", ".jpg", ".jpeg", ".gif", ".pdf",
                   ".zip", ".tar", ".gz"}

# 待检测的扩展名(代码 + 脚本 + 文档 + 配置)
SCAN_EXTENSIONS = {
    ".py", ".pyw",
    ".md", ".rst",
    ".bat", ".cmd", ".ps1", ".sh",
    ".sql",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".txt",
    ".html", ".js",
}

# 引用模式 -> 风险等级
# BLOCK: 直接破坏功能
# WARN:  字符串/文档引用,需人工判断
# INFO:  注释性提及
PATTERNS = [
    # 模块 import(代码级,删除必崩)
    (re.compile(r"\bimport\s+stock_data_collector\b"), "BLOCK", "import 模块"),
    (re.compile(r"from\s+src\.stock_data_collector\s+import\b"), "BLOCK", "from src.stock_data_collector import"),
    (re.compile(r"from\s+stock_data_collector\s+import\b"), "BLOCK", "from stock_data_collector import"),
    (re.compile(r"from\s+\.\s*import\s+stock_data_collector\b"), "BLOCK", "相对导入"),
    (re.compile(r"from\s+\.\.stock_data_collector\s+import\b"), "BLOCK", "相对导入"),

    # 类名引用(代码级,删除必崩)
    (re.compile(r"\bStockDataCollector\b"), "BLOCK", "类名 StockDataCollector"),

    # 顶层导出符号(stock_data_collector.py 中重复定义的常量)
    # 仅在 .py 文件中检测,避免误报
    # 注意: 这些符号在 src/config.py 中也定义,需人工区分来源
    # 因此这里不直接判 BLOCK,而是 WARN,提示人工核对
    (re.compile(r"\b(DB_PATH|LOG_PATH|START_DATE|STOCK_CODES)\b"), "WARN", "可能来自 stock_data_collector 的常量(需人工区分是否来自 src/config.py)"),

    # 字符串/动态引用
    (re.compile(r"['\"]stock_data_collector['\"]"), "WARN", "字符串引用模块名"),
    (re.compile(r"['\"]StockDataCollector['\"]"), "WARN", "字符串引用类名"),
    (re.compile(r"importlib.*stock_data_collector"), "WARN", "动态 importlib 加载"),

    # 文档/注释中的提及(仅提示)
    (re.compile(r"#.*stock_data_collector"), "INFO", "注释提及"),
    (re.compile(r"<!--.*stock_data_collector"), "INFO", "HTML 注释提及"),
]


def should_skip(path: Path) -> bool:
    """是否跳过该路径"""
    # 检查路径各段是否在 SKIP_DIRS 中
    parts = path.parts
    for skip in SKIP_DIRS:
        if skip in parts:
            return True
    # 跳过 stock_data_collector.py 自身
    if path.name == "stock_data_collector.py":
        return True
    # 跳过本脚本自身
    if path.name == "check_stock_data_collector_refs.py":
        return True
    # 扩展名过滤
    if path.suffix.lower() in SKIP_EXTENSIONS:
        return True
    return False


def scan_file(path: Path) -> list:
    """扫描单个文件,返回命中列表

    Returns:
        [{"line_no": int, "line": str, "level": str, "pattern_desc": str, "match_text": str}]
    """
    try:
        # utf-8 优先,失败再尝试 gbk(Windows 中文项目常见)
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="gbk", errors="ignore")
    except Exception as e:
        return [{"error": f"读取失败: {e}"}]

    hits = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        # 去除首尾空白,但保留原始行用于输出
        stripped = line.strip()
        if not stripped:
            continue

        for pattern, level, desc in PATTERNS:
            m = pattern.search(line)
            if m:
                hits.append({
                    "line_no": line_no,
                    "line": line.rstrip(),
                    "level": level,
                    "pattern_desc": desc,
                    "match_text": m.group(0),
                })
                # 同一行只匹配一次同一模式即可,避免重复
                break

    return hits


def main():
    parser = argparse.ArgumentParser(description="扫描对 src/stock_data_collector.py 的引用")
    parser.add_argument("--path", default=str(PROJECT_ROOT),
                        help=f"扫描根目录(默认: {PROJECT_ROOT})")
    parser.add_argument("--ext", nargs="*", default=None,
                        help="覆盖默认扫描扩展名(如 --ext .py .md)")
    args = parser.parse_args()

    root = Path(args.path).resolve()
    scan_exts = set(args.ext) if args.ext else SCAN_EXTENSIONS

    print("=" * 70)
    print("stock_data_collector.py 引用扫描报告")
    print("=" * 70)
    print(f"扫描根目录: {root}")
    print(f"扫描扩展名: {sorted(scan_exts)}")
    print(f"跳过目录:   {sorted(SKIP_DIRS)}")
    print()

    # 按风险等级分组: level -> [(file, hit), ...]
    by_level = defaultdict(list)
    # 按文件分组: file -> [hit, ...]
    by_file = defaultdict(list)
    total_files = 0
    scanned_files = 0
    error_files = []

    for path in root.rglob("*"):
        if not path.is_file():
            continue
        total_files += 1
        if should_skip(path):
            continue
        if path.suffix.lower() not in scan_exts:
            continue

        scanned_files += 1
        hits = scan_file(path)
        if hits and "error" in hits[0]:
            error_files.append((path, hits[0]["error"]))
            continue

        if hits:
            by_file[path].extend(hits)
            for h in hits:
                by_level[h["level"]].append((path, h))

    # 输出统计
    print("-" * 70)
    print("扫描统计")
    print("-" * 70)
    print(f"  总文件数(过滤扩展名后): {scanned_files}")
    print(f"  命中文件数:            {len(by_file)}")
    print(f"  命中总数:              {sum(len(v) for v in by_file.values())}")
    print(f"  读取失败文件数:        {len(error_files)}")
    print()
    print(f"  [BLOCK] 代码级引用:    {len(by_level['BLOCK'])} 处  (删除会破坏功能)")
    print(f"  [WARN]  需人工确认:    {len(by_level['WARN'])} 处  (字符串/常量,需核对)")
    print(f"  [INFO]  注释/文档提及: {len(by_level['INFO'])} 处  (删除安全)")
    print()

    # BLOCK 级别优先输出
    for level in ["BLOCK", "WARN", "INFO"]:
        items = by_level[level]
        if not items:
            continue
        print("=" * 70)
        print(f"[{level}] {level == 'BLOCK' and '阻断性引用' or (level == 'WARN' and '需人工确认' or '注释性提及')} - 共 {len(items)} 处")
        print("=" * 70)
        # 按文件分组输出
        files_in_level = defaultdict(list)
        for f, h in items:
            files_in_level[f].append(h)

        for f in sorted(files_in_level.keys()):
            rel = f.relative_to(root)
            print(f"\n  文件: {rel}")
            for h in files_in_level[f]:
                print(f"    L{h['line_no']:>4} | {h['pattern_desc']}")
                print(f"          | {h['line']}")
        print()

    # 读取失败文件
    if error_files:
        print("=" * 70)
        print(f"[ERROR] 读取失败文件 - 共 {len(error_files)} 个")
        print("=" * 70)
        for f, err in error_files:
            print(f"  {f.relative_to(root)}: {err}")
        print()

    # 常量冲突专项检查(stock_data_collector.py 与 src/config.py 都定义了这些常量)
    print("=" * 70)
    print("常量冲突检查")
    print("=" * 70)
    const_pattern = re.compile(r"\b(DB_PATH|LOG_PATH|START_DATE|STOCK_CODES)\b")
    config_path = root / "src" / "config.py"
    sdc_path = root / "src" / "stock_data_collector.py"

    for label, p in [("src/config.py", config_path), ("src/stock_data_collector.py", sdc_path)]:
        if not p.exists():
            print(f"  {label}: 不存在")
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            text = p.read_text(encoding="gbk", errors="ignore")
        defs = []
        for line_no, line in enumerate(text.splitlines(), start=1):
            m = re.match(r"^\s*(DB_PATH|LOG_PATH|START_DATE|STOCK_CODES)\s*=", line)
            if m:
                defs.append((line_no, line.strip()))
        print(f"  {label}:")
        if defs:
            for ln, content in defs:
                print(f"    L{ln:>3} | {content}")
        else:
            print(f"    (未发现这 4 个常量的定义)")
    print()

    # 最终结论
    print("=" * 70)
    print("删除安全评估")
    print("=" * 70)
    block_count = len(by_level["BLOCK"])
    warn_count = len(by_level["WARN"])
    if block_count == 0 and warn_count == 0:
        print("  ✓ 无任何代码/字符串引用,删除安全")
        print("  ✓ 建议: 直接删除 src/stock_data_collector.py")
    elif block_count == 0 and warn_count > 0:
        print(f"  △ 无代码级 import,但存在 {warn_count} 处字符串/常量引用需人工确认")
        print("    建议: 逐项确认 WARN 级别引用后即可删除")
    else:
        print(f"  ✗ 存在 {block_count} 处代码级 import,删除会破坏功能")
        print("    建议: 先处理以下 BLOCK 文件,再删除 stock_data_collector.py")
        for f, h in by_level["BLOCK"]:
            print(f"      - {f.relative_to(root)} (L{h['line_no']})")
    print()


if __name__ == "__main__":
    main()
