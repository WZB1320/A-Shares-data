"""API 接口冒烟测试

覆盖调用方反馈过的关键接口, 用于改动后快速回归:
  - /api/daily           最新收盘价非 0/null, 换手率有值
  - /api/indicators/valuation   最新记录有合理 PE/PB, 且日期与日线对齐(无孤儿行)
  - /api/indicators/financial   中间表指标(current_ratio/quick_ratio/inventory/accounts_receivable)
  - /api/financial              原始报表(current_assets/current_liabilities)
  - /api/capital                总股本非空
  - /api/industry/list + /api/industry/{name}/stocks   同行业可比样本量
  - 排序: 所有列表接口最新在前

用法(需先启动 API):
    python scripts/smoke_test_api.py
    python scripts/smoke_test_api.py --base http://127.0.0.1:8001
"""
import argparse
import sys
import urllib.parse

import requests

# 关键: 绕过系统 HTTP 代理, 否则请求 localhost 会被代理拦截返回 502
SESSION = requests.Session()
SESSION.trust_env = False

CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8001")
    parser.add_argument("--stocks", nargs="+", default=["sz002594", "sz300750", "sh600585", "sz000858"])
    args = parser.parse_args()
    base = args.base.rstrip("/")

    def get(path, **params):
        r = SESSION.get(base + path, params=params or None, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"{path} -> HTTP {r.status_code}: {r.text[:200]}")
        return r.json()

    # ---- 0. 健康检查 ----
    print("\n[0] 健康检查")
    h = get("/api/health")
    check("GET /api/health", h.get("status") == "ok", str(h.get("db_path")))

    # ---- 0.5 覆盖度自检 ----
    print("\n[0.5] /api/coverage 覆盖度自检")
    cov = get("/api/coverage")
    check("coverage master=22", cov.get("master_count") == 22, str(cov.get("master_count")))
    tbl_map = {t["table"]: t for t in cov.get("tables", []) if "error" not in t}
    daily_cov = tbl_map.get("stock_daily", {})
    check("coverage stock_daily 全覆盖", daily_cov.get("missing_stocks") == [],
          f"missing={daily_cov.get('missing_stocks')}")
    val_cov = tbl_map.get("valuation_indicators", {})
    check("coverage 估值表缺 ETF 而已", val_cov.get("missing_stocks") in [[], ["sh513700"]],
          f"missing={val_cov.get('missing_stocks')}")

    # ---- 1. 日线: close 非 0/null + 排序降序 ----
    print("\n[1] /api/daily 收盘价与排序")
    for code in args.stocks:
        j = get(f"/api/daily/{code}", limit=3)
        rows = j.get("data", [])
        if not rows:
            check(f"{code} 日线数据", False, "count=0")
            continue
        latest = rows[0]
        close = latest.get("close")
        ok_close = close is not None and float(close) != 0
        dates = [r["trade_date"] for r in rows]
        ok_order = dates == sorted(dates, reverse=True)
        check(f"{code} 最新close非0", ok_close, f"date={latest['trade_date']} close={close}")
        check(f"{code} 日线降序", ok_order, str(dates))

    # ---- 2. 估值: PE 合理 + 日期与日线对齐 ----
    print("\n[2] /api/indicators/valuation 估值口径与日期对齐")
    for code in args.stocks:
        try:
            v = get(f"/api/indicators/valuation/{code}", limit=1)
            d = get(f"/api/daily/{code}", limit=1)
        except RuntimeError as e:
            check(f"{code} 估值", False, str(e))
            continue
        if not v.get("count"):
            check(f"{code} 估值非空", False, "count=0")
            continue
        vr, dr = v["data"][0], d["data"][0]
        aligned = vr.get("trade_date") == dr.get("trade_date")
        check(f"{code} 估值日期与日线对齐(无孤儿行)", aligned,
              f"val={vr.get('trade_date')} daily={dr.get('trade_date')}")
        pe = vr.get("pe_ttm")
        check(f"{code} pe_ttm 合理(非±0.x量级)", pe is not None and abs(float(pe)) > 1,
              f"pe_ttm={pe} pb={vr.get('pb')}")
        close_v = vr.get("close")
        close_d = dr.get("close")
        ok_close_join = (
            close_v is not None and float(close_v) != 0
            and close_d is not None
            and abs(float(close_v) - float(close_d)) <= max(0.01, abs(float(close_d)) * 0.001)
        )
        check(f"{code} 估值接口close与日线一致", ok_close_join,
              f"val_close={close_v} daily_close={close_d}")

    # ---- 3. 财务: 中间表 + 原始表口径一致 ----
    print("\n[3] /api/indicators/financial 与 /api/financial")
    for code in args.stocks:
        try:
            mid = get(f"/api/indicators/financial/{code}", limit=1)
            raw = get(f"/api/financial/{code}", limit=1)
        except RuntimeError as e:
            check(f"{code} 财务", False, str(e))
            continue
        m = mid["data"][0] if mid.get("count") else {}
        r = raw["data"][0] if raw.get("count") else {}
        check(f"{code} 中间表 current_ratio/inventory/accounts_receivable",
              all(m.get(k) is not None for k in
                  ("current_ratio", "quick_ratio", "inventory", "accounts_receivable")),
              f"current_ratio={m.get('current_ratio')} inventory={m.get('inventory')}")
        ca, cl = r.get("current_assets"), r.get("current_liabilities")
        check(f"{code} 原始表 current_assets/current_liabilities", ca is not None and cl is not None,
              f"ca={ca} cl={cl}")
        if ca and cl and m.get("current_ratio") is not None:
            hand = round(float(ca) / float(cl), 4)
            check(f"{code} 两表流动比率一致", abs(hand - float(m["current_ratio"])) < 0.01,
                  f"手算={hand} 中间表={m['current_ratio']}")

    # ---- 4. 股本 ----
    print("\n[4] /api/capital 总股本")
    for code in args.stocks:
        j = get(f"/api/capital/{code}")
        ts = j["data"][0]["total_shares"] if j.get("count") else None
        check(f"{code} total_shares 非空", ts is not None, f"total_shares={ts}")

    # ---- 5. 同行业可比样本量 ----
    print("\n[5] /api/industry 同行业可比")
    lst = get("/api/industry/list")
    check("行业总数 > 50", lst.get("count", 0) > 50, f"count={lst['count']}")
    top = lst["data"][0] if lst.get("count") else {}
    if top:
        peers = get(f"/api/industry/{urllib.parse.quote(top['industry_name'])}/stocks")
        check("最大行业组样本量 > 100", peers.get("count", 0) > 100,
              f"{top['industry_name']} -> {peers['count']} 只")

    # ---- 汇总 ----
    failed = [c for c in CHECKS if not c[1]]
    print("\n" + "=" * 60)
    print(f"合计 {len(CHECKS)} 项, 通过 {len(CHECKS) - len(failed)}, 失败 {len(failed)}")
    for n, _, d in failed:
        print(f"  FAIL: {n} {d}")
    print("=" * 60)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
