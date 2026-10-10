# -*- coding: utf-8 -*-
"""ETF 行情 + 交易日历 -> PostgreSQL

数据源：腾讯接口（行情、ETF 名称）、akshare（交易日历）
三张表：trade_calendar / etf_bars_raw / etf_bars_hfq（表要先建好，见 schema.sql）

更新策略：全部增量更新，只补新数据。
    ETF 名称也来自腾讯接口（返回里的 qt 节点，第 2 个字段），不额外引入数据源；
    名称和行情一起写进两张行情表的 name 字段。

后复权接口没给数据的情况：
    有一部分基金腾讯不提供复权序列（qfq、hfq 都没有）。脚本默认用不复权价填进
    etf_bars_hfq，并在输出里标注出来 —— 推断是这些基金从未分红、复权因子恒为 1。
    这个推断没有第三方数据核实过；介意的话把 FILL_HFQ_WITH_RAW 改成 False。

为什么用后复权而不是前复权：
    前复权价以"最新价"为基准反推，一旦分红或份额折算，全部历史价都要重算，
    只能每次全量重拉替换。后复权的复权因子是向前累积的，历史值不会再变
    （实测：同一日期用不同请求起点拉，取值完全一致），所以可以增量拼接。

运行：python -m framework.etf_sync
"""

import json
import os
import time
from datetime import date, timedelta
from pathlib import Path

# akshare 不在这里 import：包很大（几十 MB 依赖），而且只有同步交易日历要用。
# 改成 fetch_calendar() 内部延迟导入 —— 不装 akshare 也能正常起后端、看页面。
import pandas as pd
import psycopg
import requests


class EtfSync:
    # ================= 要改的配置都在这里 =================
    CODES = [
        # 全天候策略成分
        'sz159915', 'sh518880', 'sh513880', 'sh513030', 
        'sh513080', 'sh513100', 'sh512890', 'sz160723',

        # 行业轮动策略成分
        'sz159928', 'sh512070', 'sh512800', 'sh512400',
        'sh512200', 'sh512980', 'sh512480', 'sh512170',
        'sh512710', 'sz159980', 'sh515220', 'sh516160',
        'sz159611',

        # 风格轮动策略成分(sz159915/sh512890)
        'sh510050', 'sh510500', 'sh512100', 'sh512040',
        'sh512950', 'sh588000', 'sh563300',

        # 趋势轮动策略成分(sh518880/sh512890/sh513100)
        'sz159949',

        # 基准
        'sh510300',
    ]                                              # ETF 代码，必须带 sh/sz 前缀
    START = "2015-01-01"                           # 起始日期
    TABLE_CALENDAR = "trade_calendar"
    TABLE_RAW = "etf_bars_raw"                     # 不复权
    TABLE_HFQ = "etf_bars_hfq"                     # 后复权

    # 腾讯日线接口。旧域名 web.ifzq.gtimg.cn 容易被 WAF 限流（返回 HTTP 501 的网页，
    # 不是 JSON），所以改用下面这个域名；两者返回的数值完全一致（已逐字段比对过）。
    URL = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get"
    # 腾讯返回顺序：日期, 开, 收, 高, 低, 量  —— 是「开-收-高-低」，不是「开-高-低-收」
    FIELDS = ["trade_date", "open", "close", "high", "low", "volume"]
    COLS = ["code", "name", "trade_date", "open", "high", "low", "close", "volume"]

    COUNT = 600     # 每段窗口向接口要多少根。上限约 700，超过会被悄悄截断成 640 根
    SLEEP = 0.3     # 每次请求之间的间隔（秒）。被限流的话调大，比如 1
    RETRY = 3       # 单次请求被限流时的重试次数（退避 2s / 4s / 6s）
    # 有些基金腾讯不给后复权序列，这时用不复权价填进 etf_bars_hfq（输出里会标注）。
    # 已知这类基金的 qfq、hfq 两个序列腾讯都不提供，据此推测其复权因子恒为 1（从未分红）。
    # 这个推断没有第三方数据源核实过：万一某只其实分过红，这张表算出的收益率会偏低。
    # 拿不准就改成 False，让这些基金的后复权留空。
    FILL_HFQ_WITH_RAW = True
    # =====================================================

    def __init__(self):
        self.start = pd.to_datetime(self.START).date()
        self.end = date.today()
        self.names = {}         # 名称缓存，一只 ETF 只请求一次

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

    # ------------------------------------------------ 取数
    def _get_node(self, param, code):
        """请求腾讯接口，返回 data[code] 节点。

        两类情况都退避重试：
        1. 请求太密时腾讯的 WAF 返回一个网页（HTTP 501）而不是 JSON；
        2. 连接失败 / 读超时（ConnectionError 包括 Read timed out）。
        直接 .json() 会抛出看不懂的 JSONDecodeError，所以这里重试几次再抛。
        """
        text = ""
        last_err = None
        for attempt in range(self.RETRY):
            try:
                text = requests.get(self.URL, params={"param": param}, timeout=30).text
                if text.lstrip().startswith("{"):
                    return json.loads(text)["data"][code]
            except requests.exceptions.RequestException as e:
                # 连接错误 / 读超时：别让单次网络抖动直接打断整轮同步，退避后重试
                last_err = e
                text = ""
            time.sleep(2 * (attempt + 1))       # 2s、4s、6s
        if last_err is not None:
            raise RuntimeError(
                f"{code} 连续 {self.RETRY} 次请求失败（连接错误或超时）："
                f"{last_err}。等几分钟再跑，或把 SLEEP 调大。"
            )
        raise RuntimeError(
            f"{code} 连续 {self.RETRY} 次没拿到数据 —— 腾讯接口可能把你限流了。"
            f"等几分钟再跑，或把 SLEEP 调大。返回内容开头：{text[:80]!r}"
        )

    def fetch_calendar(self):
        """akshare 交易日历 -> DataFrame(cal_date, is_open)

        接口自带未来交易日（节假日安排已公布到 2026-12-31），
        所以这里不按 START / 今天 截断 —— 日历要能用来查未来的交易日。
        """
        try:
            import akshare as ak      # 延迟导入：只有这一步需要 akshare
        except ImportError as e:
            raise RuntimeError("同步交易日历需要 akshare：pip install akshare") from e
        raw = ak.tool_trade_date_hist_sina()
        df = pd.DataFrame({"cal_date": pd.to_datetime(raw.iloc[:, 0]).dt.date})
        return df.assign(is_open=True)

    def fetch_kline(self, code, adjust="", start=None):
        """腾讯日线。adjust：''=不复权，'hfq'=后复权；start 不传就从 START 拉全历史

        要留意这个接口的脾气：它不理会请求里的开始日期，永远返回
        「截止 stop 往前 COUNT 根」。所以这里按时间一段段（2 年）推进，
        每段多要一些根数，拿到后自己按日期裁掉多余的。
        """
        begin, frames = start or self.start, []
        while begin <= self.end:
            stop = min(begin + timedelta(days=730), self.end)
            param = f"{code},day,{begin},{stop},{self.COUNT},{adjust}"
            rows = self._get_node(param, code).get((adjust + "day") if adjust else "day", [])
            if rows:
                part = pd.DataFrame([r[:6] for r in rows], columns=self.FIELDS)
                part["trade_date"] = pd.to_datetime(part["trade_date"]).dt.date
                frames.append(part[(part.trade_date >= begin) & (part.trade_date <= stop)])
            begin = stop + timedelta(days=1)
            time.sleep(self.SLEEP)

        if not frames:
            return pd.DataFrame(columns=self.COLS)
        df = pd.concat(frames, ignore_index=True)
        df.insert(0, "code", code)
        df.insert(1, "name", self.fetch_name(code))      # 名称，和行情写进同一张表
        price = ["open", "high", "low", "close"]
        df[price] = df[price].apply(pd.to_numeric)
        df["volume"] = pd.to_numeric(df["volume"]).round().astype("Int64")
        return df.drop_duplicates("trade_date").sort_values("trade_date")[self.COLS]

    def fetch_name(self, code):
        """取 ETF 名称。腾讯返回的 qt 节点里，第 2 个字段就是名称。

        只请求 1 天；即使那天不是交易日，qt 节点照样返回，所以拿名称很稳。
        结果缓存下来，一次运行里同一只只请求一次。
        """
        if code not in self.names:
            param = f"{code},day,{self.end},{self.end},1,"
            self.names[code] = self._get_node(param, code)["qt"][code][1]
        return self.names[code]

    # ------------------------------------------------ 写库
    @staticmethod
    def rows_of(df):
        """DataFrame -> 纯 Python 元组列表（numpy 类型 psycopg 不认，必须转）"""
        return [
            (r.code, r.name, r.trade_date, float(r.open), float(r.high), float(r.low),
             float(r.close), None if pd.isna(r.volume) else int(r.volume))
            for r in df.itertuples()
        ]

    def save_calendar(self, conn, df):
        """交易日历：只插还不存在的日期，返回新增行数"""
        if df.empty:
            return 0
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO trade_calendar (cal_date, is_open) VALUES (%s, %s) "
                "ON CONFLICT (cal_date) DO NOTHING",
                [(r.cal_date, bool(r.is_open)) for r in df.itertuples()],
            )
            n = cur.rowcount
        conn.commit()
        return n

    def save_kline(self, conn, table, df):
        """行情写入：已经存在的 (code, trade_date) 就用新值覆盖，返回写入行数"""
        if df.empty:
            return 0
        slots = ", ".join(["%s"] * len(self.COLS))
        with conn.cursor() as cur:
            cur.executemany(
                f"INSERT INTO {table} ({', '.join(self.COLS)}) VALUES ({slots}) "
                "ON CONFLICT (code, trade_date) DO UPDATE SET "
                "name = EXCLUDED.name, open = EXCLUDED.open, high = EXCLUDED.high, "
                "low = EXCLUDED.low, close = EXCLUDED.close, volume = EXCLUDED.volume, "
                "updated_at = now()",
                self.rows_of(df),
            )
            n = cur.rowcount
        conn.commit()
        return n

    # ------------------------------------------------ 三张表各自的同步
    def sync_calendar(self, conn):
        """表一：交易日历（增量）"""
        df = self.fetch_calendar()
        with conn.cursor() as cur:
            cur.execute(f"SELECT max(cal_date) FROM {self.TABLE_CALENDAR}")
            last = cur.fetchone()[0]
        if last:
            df = df[df.cal_date > last]
        n = self.save_calendar(conn, df)
        print(f"{self.TABLE_CALENDAR}：新增 {n} 天")

    def sync_kline(self, conn, table, adjust):
        """表二 / 表三：行情。

        不复权（etf_bars_raw）：增量即可 —— 原始价不会被重算，历史值永远不变。

        后复权（etf_bars_hfq）：必须全量重拉 + 覆盖。腾讯的后复权因子在分红/份额折算后
        会"往前重算"整段历史；若只做增量，只覆盖最近几根，会让旧数据（旧因子）与新数据
        （新因子）在最后一次同步的边界处断开，产生一根假跳空（此前曾把 10-08→10-09 的
        净值凭空砸掉约 5% —— 底层持仓当天实际只涨了约 +0.5%）。全量覆盖保证一只基金每一根
        K 线都用同一套因子，边界自然平滑。
        """
        for code in self.CODES:
            full = adjust == "hfq"
            with conn.cursor() as cur:
                cur.execute(f"SELECT max(trade_date) FROM {table} WHERE code = %s", (code,))
                last = cur.fetchone()[0]
            if full:
                start = self.start                      # 后复权：全量，从项目起点重拉到底
            else:
                start = None
                if last:
                    start = last + timedelta(days=1)
                    if start > self.end:
                        print(f"{table} {code}：已是最新（{last}）")
                        continue
            df = self.fetch_kline(code, adjust=adjust, start=start)
            note = ""
            if df.empty and adjust == "hfq" and self.FILL_HFQ_WITH_RAW:
                # 接口没有这只基金的后复权序列 —— 它从未分红，后复权就等于不复权
                df = self.fetch_kline(code, adjust="", start=start)
                if not df.empty:
                    note = "（接口没给后复权数据，已用不复权价代替）"
            n = self.save_kline(conn, table, df)
            if df.empty:
                # 正常情况：今天还没收盘，所以还没有今天这根 K 线
                print(f"{table} {code}：暂无新数据（跳过）")
            else:
                scope = "全量刷新" if full else "增量"
                print(f"{table} {code}：{scope} {n} 行（{df.trade_date.min()} ~ {df.trade_date.max()}）{note}")

    # ------------------------------------------------ 主流程
    def run(self):
        with self.connect() as conn:
            self.sync_calendar(conn)
            self.sync_kline(conn, self.TABLE_RAW, "")       # 不复权：增量
            self.sync_kline(conn, self.TABLE_HFQ, "hfq")    # 后复权：全量（复权因子会重算历史，增量会有假跳空）


if __name__ == "__main__":
    try:
        EtfSync().run()
    except KeyboardInterrupt:
        # 每只 ETF 写完就提交了，所以 Ctrl+C 不会丢已完成的进度，
        # 下次运行会从各自的水位接着补。
        print("\n[中断] 已停止。跑完的 ETF 都已入库，下次运行会接着补。")
