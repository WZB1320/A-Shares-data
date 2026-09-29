"""校验 tencent_collector.FIELD_MAP 的索引是否与腾讯行情接口真实语义一致。

为什么需要它:
    该接口返回 88 个用 "~" 分隔的裸字段, 没有字段名。一旦索引错位,
    采集端会把"涨跌额"当成 PE(TTM) 写进估值表, 且不会报任何错。
    本项目历史上就发生过整块错位(见 tencent_collector.FIELD_MAP 注释)。

校验思路(自证, 不依赖任何外部权威源):
    用接口内部必须成立的恒等式交叉验证, 任一等式被打破即说明索引错位。
      1. 涨停价 / 昨收 - 1 ∈ {5%, 10%, 20%}   -> 锁定 high_limit/low_limit/prev_close
      2. close/prev_close - 1 ≈ 涨跌幅%       -> 锁定 close/prev_close/change/change_pct
      3. 振幅% ≈ (最高-最低)/昨收 × 100        -> 锁定 high/low/amplitude
      4. 换手率% ≈ 成交量(手)×100×最新价/流通市值(亿)×1e8×100
                                              -> 锁定 volume/turnover/circ_mv
      5. 总市值 ≥ 流通市值 (允许浮点误差)      -> 锁定 total_mv/circ_mv
      6. PE(TTM) 不落在 (0, 1) 区间            -> 反证 pe_ttm 不是"涨跌额"
      7. PB 落在 (0, 100) 区间                 -> 反证 pb 不是其它量

用法:
    python scripts/verify_tencent_field_map.py [stock_code ...]
"""
import os
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.collector.tencent_collector import TencentCollector  # noqa: E402

DEFAULT_CODES = ["sz000858", "sh600519", "sh600585", "sz002594", "sz300750"]
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://gu.qq.com/",
}


def fetch_parts(code: str, attempts: int = 3):
    """拉取原始字段数组; 返回 (parts, err)。"""
    session = requests.Session()
    session.trust_env = False  # 绕过可能存在的环境代理
    last_err = None
    for _ in range(attempts):
        try:
            resp = session.get(
                f"http://qt.gtimg.cn/q={code}", headers=HEADERS, timeout=12
            )
            resp.encoding = "gbk"
            text = resp.text.strip()
            if "~" in text:
                return text.split("~"), None
            last_err = "响应不含 ~ 分隔符"
        except Exception as exc:  # noqa: BLE001
            last_err = f"{type(exc).__name__}: {exc}"
        time.sleep(1)
    return None, last_err


def approx(a, b, tol):
    return a is not None and b is not None and abs(a - b) <= tol


def check_one(code: str, parts) -> list:
    """对单只股票执行全部恒等式校验, 返回 [(名称, 是否通过, 明细)]。"""
    fmap = {name: idx for idx, name in TencentCollector.FIELD_MAP.items()}

    def val(name):
        idx = fmap.get(name)
        if idx is None or idx >= len(parts):
            return None
        raw = parts[idx]
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    close, prev = val("close"), val("prev_close")
    high, low = val("high"), val("low")
    change, change_pct = val("change"), val("change_pct")
    amplitude, turnover = val("amplitude"), val("turnover")
    circ_mv, total_mv = val("circ_mv"), val("total_mv")
    pe_ttm, pb = val("pe_ttm"), val("pb")
    high_limit, low_limit = val("high_limit"), val("low_limit")
    volume = val("volume")

    results = []

    # 1. 涨停/跌停 与 昨收 的涨跌幅上限必须属于 A 股法定档位
    ratio = (high_limit / prev - 1) if (high_limit and prev) else None
    hit = ratio is not None and any(approx(ratio, lim, 0.005) for lim in (0.05, 0.10, 0.20))
    results.append((
        "涨停价=昨收×(1+法定涨跌幅)",
        hit,
        f"high_limit={high_limit} prev_close={prev} 推算上限={ratio * 100:.2f}%"
        if ratio is not None else "字段为空",
    ))

    # 2. close 相对昨收的涨跌幅 == change_pct
    calc_pct = ((close / prev - 1) * 100) if (close and prev) else None
    results.append((
        "close/prev_close-1 对齐 change_pct",
        approx(calc_pct, change_pct, 0.05),
        f"计算={calc_pct:.4f}% 接口={change_pct}%" if calc_pct is not None else "字段为空",
    ))

    # 3. 振幅
    calc_amp = ((high - low) / prev * 100) if (high is not None and low is not None and prev) else None
    results.append((
        "振幅=(最高-最低)/昨收",
        approx(calc_amp, amplitude, 0.05),
        f"计算={calc_amp:.4f}% 接口={amplitude}%" if calc_amp is not None else "字段为空",
    ))

    # 4. 换手率: 成交量(手)×100股 / (流通市值/最新价) × 100%
    calc_to = None
    if volume and close and circ_mv:
        circ_shares = circ_mv * 1e8 / close
        calc_to = volume * 100 / circ_shares * 100
    results.append((
        "换手率=成交量/流通股本",
        approx(calc_to, turnover, 0.05),
        f"计算={calc_to:.4f}% 接口={turnover}%" if calc_to is not None else "字段为空",
    ))

    # 5. 总市值 ≥ 流通市值
    results.append((
        "总市值 ≥ 流通市值",
        total_mv is not None and circ_mv is not None and total_mv >= circ_mv - 1e-6,
        f"total_mv={total_mv}亿 circ_mv={circ_mv}亿",
    ))

    # 6. PE(TTM) 不应落在 (0,1) —— 若为涨跌额(如 0.21)会命中
    results.append((
        "PE(TTM) 不在 (0,1) 区间",
        pe_ttm is not None and not (0 < abs(pe_ttm) < 1),
        f"pe_ttm={pe_ttm}",
    ))

    # 7. PB 量级
    results.append((
        "PB 落在 (0,100)",
        pb is not None and 0 < pb < 100,
        f"pb={pb}",
    ))

    # 8. 最新价落在当日最高/最低之间
    results.append((
        "最低 ≤ 最新价 ≤ 最高",
        close is not None and high is not None and low is not None and low <= close <= high,
        f"low={low} close={close} high={high}",
    ))

    return results


def main():
    codes = sys.argv[1:] or DEFAULT_CODES
    total = passed = 0

    for code in codes:
        parts, err = fetch_parts(code)
        if parts is None:
            print(f"\n[{code}] 拉取失败: {err}")
            total += 1
            continue
        print(f"\n[{code}] 字段数={len(parts)}")
        for name, ok, detail in check_one(code, parts):
            total += 1
            passed += 1 if ok else 0
            print(f"   {'PASS' if ok else 'FAIL'}  {name:<30} {detail}")

    print(f"\n{'=' * 60}")
    print(f"合计: {passed}/{total} 通过")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
