# -*- coding: utf-8 -*-
"""ETF 当日实时行情（腾讯 qt.gtimg.cn）-> etf_quote

数据源
    https://qt.gtimg.cn/q=sh510300,sz159915&fmt=json
      · 返回 {"sh510300": [88 个字段, ...], ...}，**GBK 编码**（不是 UTF-8，直接 decode 会乱码）
      · 一次可传多只（逗号分隔），本脚本按 BATCH=60 分批
      · 接口不返回字段名，只有位置下标 —— 所以下面这张表是唯一的字段说明。
        标 ★ 的已用 etf_bars_raw 日线交叉核对（数值对得上才算）。表里只放下面写了
        列名的字段；标「未确认」的一律不入库（既不猜着填列，也不留 raw 兜底）。
        主键 = (code, trade_date, quote_time)：同一行情时间重复抓 = 覆盖，
        盘中不同时间点抓的各留一行，天然就是分时留痕。

字段明细（下标 -> 含义 -> 本表列名）
     0  未知（恒为 '1'）
   ★ 1  名称                  name
   ★ 2  代码（不带前缀）       code
   ★ 3  最新价                last          = etf_bars_raw 当日收盘，已核对
   ★ 4  昨收                  prev_close    = 前一交易日收盘，已核对
   ★ 5  今开                  open
   ★ 6  成交量（手，1手=100份） volume        =[7]外盘 + [8]内盘，30 只里有 2 只差 1 手（盘口各自四舍五入）
     7  外盘（手，主动买）     outer_vol
     8  内盘（手，主动卖）     inner_vol
     9~18  买一~买五 价/量     bid1_px/bid1_qty、bid2_px/bid2_qty、bid3_px/bid3_qty、bid4_px/bid4_qty、bid5_px/bid5_qty
     19~28 卖一~卖五 价/量     ask1_px/ask1_qty、ask2_px/ask2_qty、ask3_px/ask3_qty、ask4_px/ask4_qty、ask5_px/ask5_qty
     29 最近逐笔成交（ETF 为空）
   ★ 30 行情时间              quote_time / trade_date  20260930161443 -> 2026-09-30 16:14:43
   ★ 31 涨跌                  change        = 3 − 4，已核对
   ★ 32 涨跌幅 %              change_pct
   ★ 33 最高                  high
   ★ 34 最低                  low
     35 拼接串「最新价/成交量(手)/成交额(元)」—— 不单独存，等价信息见 last/volume/amount_wan
   ★ 36 成交量（手）           （同 6，重复字段）
   ★ 37 成交额（万元，整数）     （amount_wan 是同一数的 4 位小数版）
   ★ 38 换手率 %              turnover_pct
     39 市盈率 TTM            （不落库）     ETF 为空，个股/指数才有（茅台 19.32）
     40 空
   ★ 41 最高（同 33）
   ★ 42 最低（同 34）
   ★ 43 振幅 %                amplitude_pct = (高−低)/昨收
     44 流通市值（亿）         float_cap     ETF 与总市值相等；指数两者不同，已核对
     45 总市值（亿）           market_cap
     46 市净率                （不落库）     ETF 为 0（茅台 6.26）
     47 涨停价                limit_up      = 昨收×1.1，已核对；指数为 -1
     48 跌停价                limit_down    = 昨收×0.9，已核对
     49 量比                  volume_ratio
     50 委差（手）             order_diff
   ★ 51 均价                  avg_price     = 成交额/成交量，已核对
     52 市盈率（动）           （不落库）
     53 市盈率（静）           （不落库）
     54/55 空
     56 涨速?（个股 0.06，ETF 空）—— 未确认
   ★ 57 成交额（万元，4 位小数） amount_wan
     58 未确认：数值 = 成交额(万) ÷ 成交量(手) = 均价/100
     59 未确认（510300=6274、茅台=36）
     60 板块（'   A'）          （不落库）
     61 证券类型                （不落库）     ETF / GP-A(个股) / ZS(指数)，已核对
   ★ 62 年初至今涨跌幅 %        ret_ytd       159915 与库里裸价算的 YTD(-1.00%) 完全一致
   ★ 63 近 5 日涨跌幅 %         ret_5d        = 库里收盘价算的 5 日收益，已核对
     64/65/66 个股非 0（4.13/32.41/27.30，ETF 空）—— 未确认
   ★ 67 52 周最高             high_52w      = 近 250 交易日最高，已核对（365 日口径对不上）
   ★ 68 52 周最低             low_52w       = 近 250 交易日最低，已核对
   ★ 69 近 10 日涨跌幅 %        ret_10d       已核对
   ★ 70 近 20 日涨跌幅 %        ret_20d       已核对
   ★ 71 近 60 日涨跌幅 %        ret_60d       已核对
   ★ 72 总股本（股）           total_shares  = 总市值(亿)×1e8 ÷ 最新价，已核对（茅台 12.5 亿股）
   ★ 73 流通股本（股）         float_shares
     74 未确认：ETF 是 +54.78，但该 ETF 各周期都是负收益，所以**不是**近一年涨跌幅
     75 未确认的百分比
   ★ 76 总股本（股，同 72）
   ★ 77 折溢价率 %             premium       = 最新价 / [78] − 1，已核对（4.432/4.4301−1 = +0.043%）
   ★ 78 净值 / IOPV            nav
     79 未确认的百分比
     80 折溢价率 %（口径 B）     （不落库）     由 [81] 那套净值算的，与 [77] 差约 0.001
     81 净值 / IOPV（口径 B）    （不落库）
     82 币种                  （不落库）     CNY
     83/84/85/86/87 未确认（84 是状态标志，如 '___D__F__N'）

  口径取舍：接口里有 [77]/[78] 与 [80]/[81] 两套净值，两者差约 0.001。本表只存
  [77]/[78] 这一套（列名 premium / nav），[80]/[81] 不落库 —— 哪一套是 IOPV、
  哪一套是当日单位净值无法从数据反推（要实盘对照一次），只留一套就不会在页面上
  出现两个"净值"打架。

关于「近一年涨跌幅」：接口**没有**这个字段（[74] 不是）。要这个指标请用
etf_bars_hfq 自己算（250 个交易日），或者用 etf_bars_raw + 分红还原。

运行
    python -m framework.etf_quote                          # 抓 etf_bars_raw 里全部标的
    python -m framework.etf_quote --codes sh510300,sz159915 # 只抓这几只
    python -m framework.etf_quote --dry-run                # 只打印，不落库
    python -m framework.etf_quote --fields                 # 只打印上面这张字段对照表

落库规则
    主键 (code, trade_date, quote_time)：同一次快照重复抓 = 覆盖同一行；
    盘中每隔几分钟抓一次，一天就会留下多行分时快照。
    行情时间解析不出来的行直接不落库（它是主键的一部分，不能空）。
"""

import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import psycopg
import requests


# (列名, 字段下标, 类型)：类型 num 小数 / int 整数 / time 行情时间 / txt 文本
COLS = [
    ("name",         1,  "txt"),
    ("quote_time",   30, "time"),
    ("last",         3,  "num"),
    ("prev_close",   4,  "num"),
    ("open",         5,  "num"),
    ("high",         33, "num"),
    ("low",          34, "num"),
    ("change",       31, "num"),
    ("change_pct",   32, "num"),
    ("amplitude_pct", 43, "num"),
    ("volume",       6,  "int"),
    ("amount_wan",   57, "num"),
    ("turnover_pct", 38, "num"),
    ("avg_price",    51, "num"),
    ("volume_ratio", 49, "num"),
    ("bid1_px",  9, "num"), ("bid1_qty", 10, "int"),
    ("bid2_px", 11, "num"), ("bid2_qty", 12, "int"),
    ("bid3_px", 13, "num"), ("bid3_qty", 14, "int"),
    ("bid4_px", 15, "num"), ("bid4_qty", 16, "int"),
    ("bid5_px", 17, "num"), ("bid5_qty", 18, "int"),
    ("ask1_px", 19, "num"), ("ask1_qty", 20, "int"),
    ("ask2_px", 21, "num"), ("ask2_qty", 22, "int"),
    ("ask3_px", 23, "num"), ("ask3_qty", 24, "int"),
    ("ask4_px", 25, "num"), ("ask4_qty", 26, "int"),
    ("ask5_px", 27, "num"), ("ask5_qty", 28, "int"),
    ("outer_vol",    7,  "int"),
    ("inner_vol",    8,  "int"),
    ("order_diff",   50, "int"),
    ("nav",          78, "num"),
    ("premium",      77, "num"),
    ("ret_ytd",      62, "num"),
    ("ret_5d",       63, "num"),
    ("ret_10d",      69, "num"),
    ("ret_20d",      70, "num"),
    ("ret_60d",      71, "num"),
    ("high_52w",     67, "num"),
    ("low_52w",      68, "num"),
    ("market_cap",   45, "num"),
    ("float_cap",    44, "num"),
    ("total_shares", 72, "int"),
    ("float_shares", 73, "int"),
    ("limit_up",     47, "num"),
    ("limit_down",   48, "num"),
]

# 控制台打印哪些列（全部列会挤爆屏幕）
SHOW = ["code", "name", "last", "change_pct", "open", "high", "low", "volume",
        "amount_wan", "turnover_pct", "nav", "premium", "high_52w", "low_52w",
        "market_cap"]


class EtfQuote:
    # ================= 要改的配置都在这里 =================
    URL = "https://qt.gtimg.cn/q={codes}&fmt=json"
    BATCH = 60          # 每次请求带多少只（一次几十只没问题，别几千只一起上）
    SLEEP = 0.4         # 每次请求之间的间隔（秒），被限流就调大
    RETRY = 3           # 单批失败重试次数（退避 2s / 4s / 6s）
    TABLE = "etf_quote"
    BAR_TABLE = "etf_bars_raw"      # 代码来源：这张表里的全部标的
    # =====================================================

    # ------------------------------------------------ 连接
    def connect(self):
        """读项目根目录 .env 的连接参数，返回数据库连接"""
        env = Path(__file__).resolve().parent.parent / ".env"
        if env.exists():
            for line in env.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
        return psycopg.connect(
            host=os.environ.get("PG_HOST", "localhost"),
            port=os.environ.get("PG_PORT", "5432"),
            dbname=os.environ.get("PG_DB", "etf"),
            user=os.environ.get("PG_USER", "postgres"),
            password=os.environ.get("PG_PASSWORD", ""),
        )

    def codes_from_db(self, conn):
        """要抓哪些标的：etf_bars_raw 里的全部代码（去重）"""
        with conn.cursor() as cur:
            cur.execute(f"SELECT DISTINCT code FROM {self.BAR_TABLE} ORDER BY code")
            return [r[0] for r in cur.fetchall()]

    # ------------------------------------------------ 取数
    @staticmethod
    def _num(s):
        """接口里空值给的是空串（'-'/'--' 也见过），统一转成 None"""
        s = (s or "").strip()
        if not s or s in ("-", "--"):
            return None
        try:
            return float(s)
        except ValueError:
            return None

    @classmethod
    def _int(cls, s):
        f = cls._num(s)
        return None if f is None else int(f)

    @classmethod
    def _time(cls, s):
        """[30] 形如 20260930161443 -> datetime；顺便用它定 trade_date"""
        s = (s or "").strip()
        if len(s) != 14 or not s.isdigit():
            return None
        try:
            return datetime.strptime(s, "%Y%m%d%H%M%S")
        except ValueError:
            return None

    def _ask(self, codes):
        """请求一批，返回 {code: [字段...]}。

        解析顺序：先试 fmt=json（接口直接给数组），失败再退回文本格式
        v_sh510300="字段0~字段1~…"；两者是同一份数据，文本格式更老更稳，
        所以留作兜底。返回内容一律 GBK 解码（utf-8 会乱码）。
        """
        url = self.URL.format(codes=",".join(codes))
        last = ""
        for attempt in range(self.RETRY):
            try:
                raw = requests.get(url, timeout=20).content
            except Exception as e:                      # 网络抖动
                last = f"{type(e).__name__}: {e}"
                time.sleep(2 * (attempt + 1))
                continue
            text = raw.decode("gbk", errors="replace")
            last = text[:120]
            if text.lstrip().startswith("{"):
                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    data = None
                if isinstance(data, dict):
                    return {k: v for k, v in data.items()
                            if isinstance(v, list) and k in codes}
            elif 'v_' in text:
                out = {}
                for line in text.split(";"):
                    line = line.strip()
                    if not line.startswith("v_"):
                        continue
                    head, _, body = line.partition("=")
                    code = head[2:].strip()
                    if code in codes:
                        out[code] = body.strip().strip('"').split("~")
                if out:
                    return out
            time.sleep(2 * (attempt + 1))                # 2s / 4s / 6s
        raise RuntimeError(
            f"连续 {self.RETRY} 次没拿到行情 —— 腾讯接口可能把你限流了（或代码写错）。"
            f"等几分钟再跑，或把 SLEEP 调大。返回内容开头：{last!r}"
        )

    def parse(self, code, v):
        """把一只标的的字段数组转成 {列名: 值}；行情时间解析不出来就返回 None
        （这种行不落库 —— quote_time 是主键的一部分，不能空）"""
        row = {"code": code}
        for col, idx, kind in COLS:
            s = v[idx] if idx < len(v) else ""
            row[col] = self._time(s) if kind == "time" else (
                self._num(s) if kind == "num" else (
                    self._int(s) if kind == "int" else (s.strip() or None)))
        # trade_date 取自行情时间（不是 date.today）：周末/节假日重跑只会落到
        # 上一个交易日，不会凭空多出一行假数据。
        qt = row.get("quote_time")
        if qt is None:
            return None
        row["trade_date"] = qt.date()
        return row

    def fetch(self, codes):
        """分批抓取，返回 [row, ...]；拿不到的标的在返回里不出现（会打印警告）"""
        rows, missing = [], []
        for i in range(0, len(codes), self.BATCH):
            chunk = codes[i:i + self.BATCH]
            data = self._ask(chunk)
            for code in chunk:
                v = data.get(code)
                row = self.parse(code, v) if v else None
                if row is None:
                    missing.append(code)
                    continue
                rows.append(row)
            if i + self.BATCH < len(codes):
                time.sleep(self.SLEEP)
        if missing:
            print(f"[警告] 这些标的没落库（接口没返回，或行情时间解析失败）：{missing}")
        return rows

    # ------------------------------------------------ 落库
    def save(self, conn, rows):
        """按 (code, trade_date, quote_time) UPSERT：同一行情时间重复抓就是覆盖，
        盘中不同时间点抓的各留一行"""
        cols = ["code"] + [c for c, _, _ in COLS] + ["trade_date"]
        ph = ", ".join(["%s"] * len(cols))
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols
                            if c not in ("code", "trade_date", "quote_time"))
        sql = (f"INSERT INTO {self.TABLE} ({', '.join(cols)}) VALUES ({ph}) "
               f"ON CONFLICT (code, trade_date, quote_time) DO UPDATE SET {updates}")
        data = [[r["code"]] + [r[c] for c, _, _ in COLS] + [r["trade_date"]]
                for r in rows]
        with conn.cursor() as cur:
            cur.executemany(sql, data)
        conn.commit()
        return len(data)

    # ------------------------------------------------ 主流程
    def run(self, codes=None, dry_run=False):
        conn = self.connect()
        try:
            codes = codes or self.codes_from_db(conn)
            if not codes:
                print(f"{self.BAR_TABLE} 里没有标的，先跑 python -m framework.etf_sync")
                return []
            print(f"抓取 {len(codes)} 只标的的实时行情…")
            rows = self.fetch(codes)
            if not rows:
                return []
            df = pd.DataFrame(rows)
            qt = df["quote_time"].dropna()
            print(f"行情时间：{qt.iloc[0] if len(qt) else '—'}　（{len(rows)} 只）")
            print(df[[c for c in SHOW if c in df.columns]].to_string(index=False))
            if dry_run:
                print("\n--dry-run：没有写库")
                return rows
            n = self.save(conn, rows)
            print(f"\n已写入 {self.TABLE}：{n} 行"
                  f"（主键 code+trade_date+quote_time，同一行情时间重复抓 = 覆盖）")
            return rows
        finally:
            conn.close()


def main():
    ap = argparse.ArgumentParser(description="ETF 当日实时行情 -> etf_quote")
    ap.add_argument("--codes", help="只抓这几只，逗号分隔（默认抓 etf_bars_raw 里全部标的）")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不写库")
    ap.add_argument("--fields", action="store_true", help="只打印字段下标对照表")
    args = ap.parse_args()
    if args.fields:
        print(__doc__)
        return
    codes = [c.strip() for c in args.codes.split(",")] if args.codes else None
    EtfQuote().run(codes=codes, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
