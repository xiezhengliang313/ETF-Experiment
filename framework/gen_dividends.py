# -*- coding: utf-8 -*-
"""分红表生成（唯一 class：DividendBuilder）

反推公式：
    div(t) = hfq_close(t) / hfq_close(t-1) * raw_close(t-1) - raw_close(t)
    即：后复权体现再投资总回报，不复权在除息日会"掉坑"，二者差出来的
    （折成前一日价格的）现金量就是派息。

噪声来源与阈值：ETF 的成交价相对净值有每日漂移的溢价率，加上价格只保留
3 位小数，导致非除息日算出来的 div 在 ±0.02 元附近抖动。真实派息几乎都
>= 0.02 元/份，所以 THRESHOLD 取 0.02。低于它的真实小派息请手动补一行
(source='manual')，高于它的偶发误报请删除或改 source='manual'。

只写入 div > 阈值的除息日；已被人为改成 source='manual' 的行不会被覆盖。
份额拆分/合并也会让比例变化、被误判成"分红"，同样靠人工 table 修正。

运行：python -m framework.gen_dividends
"""

import os
from pathlib import Path

import psycopg


class DividendBuilder:
    THRESHOLD = 0.02           # 每份派息（元）低于此值视为噪声，不记录

    def connect(self):
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

    def codes(self, conn):
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT code FROM etf_bars_hfq ORDER BY code")
            return [r[0] for r in cur.fetchall()]

    def impute(self, conn, code):
        """返回 [(ex_date, div_per_share), ...]，按除息日升序。"""
        with conn.cursor() as cur:
            cur.execute(
                "SELECT trade_date, close FROM etf_bars_hfq WHERE code=%s ORDER BY trade_date",
                (code,))
            hfq = {d: float(c) for d, c in cur.fetchall()}
            cur.execute(
                "SELECT trade_date, close FROM etf_bars_raw WHERE code=%s ORDER BY trade_date",
                (code,))
            raw = {d: float(c) for d, c in cur.fetchall()}
        dates = sorted(set(hfq) & set(raw))
        out = []
        for i in range(1, len(dates)):
            d0, d1 = dates[i - 1], dates[i]
            h0, h1, r0, r1 = hfq[d0], hfq[d1], raw[d0], raw[d1]
            if h0 <= 0 or r0 <= 0:
                continue
            div = h1 / h0 * r0 - r1
            if div > self.THRESHOLD:
                out.append((d1, round(div, 6)))
        return out

    def save(self, conn, code, rows):
        if not rows:
            return 0
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO etf_dividends (code, ex_date, div_per_share, source) "
                "VALUES (%s,%s,%s,'imputed') "
                "ON CONFLICT (code, ex_date) DO UPDATE SET div_per_share = EXCLUDED.div_per_share "
                "WHERE etf_dividends.source = 'imputed'",
                [(code, d, v) for d, v in rows])
            n = cur.rowcount
        conn.commit()
        return n

    def run(self):
        total = 0
        with self.connect() as conn:
            for code in self.codes(conn):
                rows = self.impute(conn, code)
                n = self.save(conn, code, rows)
                total += n
                print(f"[DIV] {code}: {n} 条")
        print(f"[DIV] 完成，共 {total} 条")


if __name__ == "__main__":
    DividendBuilder().run()