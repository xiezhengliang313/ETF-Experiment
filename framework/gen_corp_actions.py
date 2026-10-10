# -*- coding: utf-8 -*-
"""公司行为表生成：分红 + 份额折算（唯一 class：CorpActionBuilder）

按「后复权因子」区分两类公司行为：
    F(t) = hfq_close(t) / raw_close(t)

相邻两日 F 的比值 R = F(t) / F(t-1)：
  - R 接近 1（小步）：      现金分红。反推派息 div = hfq(t)/hfq(t-1) * raw(t-1) - raw(t)
  - R 大步且接近整数比：    份额折算。拆分(1拆N) 时 R=N；合并(N合1) 时 R=1/N。

为什么能区分：市场涨跌在 R 里正好约掉（R = 总回报率/价格涨跌率），所以
分红日 R≈1/(1-分红/价)≈1.0x（小台阶），拆分日 R=拆分比例（2/3/5/10 这种大台阶）。

判据：R >= SPLIT_MIN 或 R <= 1/SPLIT_MIN 视为折算，否则为分红候选。
分红阈值 THRESHOLD=0.02（噪声约 ±0.02，真实派息几乎都 >=0.02）。

分红写入 etf_dividends（现金），折算写入 etf_splits（股数乘数 ratio=R，
供模拟框架在折算日调整持仓股数）。只覆盖 source='imputed' 的行，manual 不覆盖。

运行：python -m framework.gen_corp_actions
"""

import os
from pathlib import Path

import psycopg


class CorpActionBuilder:
    THRESHOLD = 0.02      # 每份派息（元）低于此值视为噪声，不记录
    SPLIT_MIN = 1.5       # 因子比值 >=1.5 或 <=1/1.5 判为份额折算

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

    def analyze(self, conn, code):
        """返回 (dividends, splits)。

        dividends=[(ex_date, div_per_share), ...]
        splits   =[(ex_date, ratio), ...]   ratio=股数乘数（拆分=N，合并=1/N）
        """
        with conn.cursor() as cur:
            cur.execute("SELECT trade_date, close FROM etf_bars_hfq WHERE code=%s ORDER BY trade_date", (code,))
            hfq = {d: float(c) for d, c in cur.fetchall()}
            cur.execute("SELECT trade_date, close FROM etf_bars_raw WHERE code=%s ORDER BY trade_date", (code,))
            raw = {d: float(c) for d, c in cur.fetchall()}
        dates = sorted(set(hfq) & set(raw))
        dividends, splits = [], []
        for i in range(1, len(dates)):
            d0, d1 = dates[i - 1], dates[i]
            h0, h1, r0, r1 = hfq[d0], hfq[d1], raw[d0], raw[d1]
            if h0 <= 0 or r0 <= 0 or h1 <= 0 or r1 <= 0:
                continue
            f0, f1 = h0 / r0, h1 / r1
            ratio = f1 / f0
            if ratio >= self.SPLIT_MIN or ratio <= 1.0 / self.SPLIT_MIN:
                # 份额拆分/合并：不是现金分红
                splits.append((d1, round(ratio, 6)))
            else:
                div = h1 / h0 * r0 - r1
                if div > self.THRESHOLD:
                    dividends.append((d1, round(div, 6)))
        return dividends, splits

    def save_dividends(self, conn, code, rows):
        with conn.cursor() as cur:
            cur.execute("DELETE FROM etf_dividends WHERE code = %s AND source = 'imputed'", (code,))
            if rows:
                cur.executemany(
                    "INSERT INTO etf_dividends (code, ex_date, div_per_share, source) "
                    "VALUES (%s,%s,%s,'imputed')",
                    [(code, d, v) for d, v in rows])
        conn.commit()

    def save_splits(self, conn, code, rows):
        with conn.cursor() as cur:
            cur.execute("DELETE FROM etf_splits WHERE code = %s AND source = 'imputed'", (code,))
            if rows:
                cur.executemany(
                    "INSERT INTO etf_splits (code, ex_date, ratio, source) "
                    "VALUES (%s,%s,%s,'imputed')",
                    [(code, d, v) for d, v in rows])
        conn.commit()

    def run(self):
        with self.connect() as conn:
            for code in self.codes(conn):
                dividends, splits = self.analyze(conn, code)
                self.save_dividends(conn, code, dividends)
                self.save_splits(conn, code, splits)
                print(f"[CA] {code}: 分红 {len(dividends)} 条，折算 {len(splits)} 条")
        print("[CA] 完成")


if __name__ == "__main__":
    CorpActionBuilder().run()