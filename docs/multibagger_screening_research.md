# Quantitative Research: Screening for Multibaggers in Indian Equities

## 1. Executive Summary & Problem Formulation

A **multibagger** is a stock that multiplies its initial investment capital several times over (2x, 3x, 5x, 10x, or 100x). In systematic quantitative finance, screening for multibaggers is plagued by **hindsight bias (survivorship fallacy)**: traditional screeners look for stocks that have *already* appreciated 500% over the last 3 years. Buying a stock that has already gone 5x without basing almost invariably leads to buying **climax blow-off tops (Stage 3/4 exhaustion)**, resulting in severe drawdowns (-40% to -70%).

The objective of systematic prospective multibagger screening is to identify **"going-to-be" multibaggers** at the inflection point of their structural markup:
1. **Early Stage 2 Breakout**: Transitioning from quiet accumulation into institutional expansion.
2. **Tight Base Consolidation**: Near 52-week highs with declining volatility (VCP), indicating institutional absorption of floating supply.
3. **Momentum Ignition & Relative Strength**: Outperforming the broader market (Nifty 50) with accelerating 3-month velocity.
4. **Volume Accumulation**: Institutional sponsorship fingerprints (20-day volume expansion over 200-day baseline).
5. **High Capital Efficiency (QGLP Overlay)**: High Return on Capital Employed (ROCE > 15-20%) and low leverage (Debt-to-Equity < 1.0), providing intrinsic compounding without dilutive equity issuance or bankruptcy vulnerability.

---

## 2. Theoretical Foundations: Synthesizing the Masters

| School of Thought | Key Work | Core Contribution to Multibagger Formula |
|---|---|---|
| **Thomas Phelps & Chris Mayer** | *100 to 1 in the Stock Market* (1972) / *100 Baggers* (2015) | **Twin Engines**: Multibaggers require simultaneous EPS growth + P/E multiple expansion. High ROCE/ROIC + high reinvestment rate + small-to-mid market cap runway. |
| **Mark Minervini** | *Trade Like a Stock Market Wizard* (SEPA) | **Stage 2 Trend Template & VCP**: Never buy a stock below declining 200 SMA. Buy tightening volatility near 52-week highs before the next leg up. |
| **William O'Neil** | *How to Make Money in Stocks* (CAN SLIM) | **Institutional Sponsorship & Relative Strength**: Relative Strength (RS) > 80, quarterly earnings acceleration, volume surge on breakout. |
| **Peter Lynch** | *One Up On Wall Street* (1989) | **Fast Growers & Under-the-Radar Small Caps**: PEG ratio < 1.0, low institutional coverage, boring/niche business monopolies, high insider ownership. |
| **Motilal Oswal Financial Services** | 25+ Years of Annual Wealth Creation Studies (1995–2025) | **QGLP Framework for Indian Markets**: Quality of business & management (ROCE > 18%), Growth (PAT > 20% CAGR), Longevity of secular runway, Price (fair PEG). |

---

## 3. The Quantitative Multibagger Equation

Phelps and Mayer established the fundamental mathematical identity of equity returns:

$$\frac{P_t}{P_0} = \underbrace{\left(\frac{\text{EPS}_t}{\text{EPS}_0}\right)}_{\text{Earnings Growth Engine}} \times \underbrace{\left(\frac{\text{P/E}_t}{\text{P/E}_0}\right)}_{\text{Multiple Expansion Engine}}$$

When a mid-cap company grows its earnings at a 26% CAGR over 5 years ($3.2\times$ earnings expansion) and the market re-rates its valuation multiple from $15\times$ to $32\times$ ($2.13\times$ multiple re-rating):

$$\frac{P_5}{P_0} = 3.20 \times 2.13 = 6.82\times \quad (\approx 7\text{-bagger})$$

### Why High ROCE is Non-Negotiable
Intrinsic earnings growth is governed by the compounding identity:

$$g = \text{Reinvestment Rate} \times \text{ROCE}$$

If a company generates **30% ROCE** and reinvests **70% of its profits**, its earnings compound intrinsically at $21\%$ per year **without requiring external debt or diluting shareholders**. Conversely, a low-ROCE company (< 10%) attempting to grow at 20% must perpetually issue equity or borrow heavily, inevitably ending in capital destruction.

---

## 4. Quantitative Screener Rules (Prices to As-Of; Fundamentals by Period End Plus Lag)

### Rule 1: Minervini Stage 2 Trend Template
- $\text{Price} \ge \text{SMA}_{50} \ge \text{SMA}_{200}$
- $\text{SMA}_{200} \ge \text{SMA}_{200, \text{prior}}$ (200-day moving average must be sloping upwards).
- $\text{Price} \ge 1.25 \times \text{Low}_{52}$ (must have rebounded at least 25% off its 52-week low, confirming Stage 1 accumulation completion).

### Rule 2: Consolidation Base & Anti-Exhaustion Guard
- $\text{Price} \ge 0.85 \times \text{High}_{52}$ (within 15% of 52-week high, indicating a tight consolidation base ready to break out).
- $\text{Return}_{1Y} \le +175\%$ (filters out parabolic late-stage blow-off runners that have already exhausted their primary move).

### Rule 3: Momentum Acceleration & Volume Accumulation
- $\text{Return}_{3M} \ge +10\%$ (positive near-term acceleration).
- $\text{Volume}_{20} / \text{Volume}_{200} \ge 0.80\times$ (ideally $\ge 1.2\times$, signaling institutional volume accumulation).

### Rule 4: Fundamental Quality & Solvency (QGLP Overlay)
- $\text{ROCE} \ge 12.0\%$ (ideally $\ge 18-20\%$).
- $\text{Debt-to-Equity} \le 1.20$ (ideally $\le 0.50$ or net cash).

---

## 5. Composite Multibagger Scoring Model (`mb_score`)

Each candidate receives an objective composite score out of 100:

$$\text{MB Score} = S_{\text{base}} + S_{\text{mom}} + S_{\text{vol}} + S_{\text{fund}}$$

1. **Base Proximity ($S_{\text{base}}$, max 25 pts)**:
   $$S_{\text{base}} = \max\left(0, 25 - \left|\frac{\text{Price} - \text{High}_{52}}{\text{High}_{52}}\right| \times 100 \times 1.2\right)$$
2. **Momentum Acceleration ($S_{\text{mom}}$, max 35 pts)**:
   $$S_{\text{mom}} = \min\left(35, \max\left(0, \text{Ret}_{3M} \times 0.70 + \text{Ret}_{1Y} \times 0.15\right)\right)$$
3. **Volume Accumulation ($S_{\text{vol}}$, max 20 pts)**:
   $$S_{\text{vol}} = \min\left(20, \frac{\text{Vol}_{20}}{\text{Vol}_{200}} \times 10.0\right)$$
4. **Fundamental Quality ($S_{\text{fund}}$, max 20 pts)**:
   $$S_{\text{fund}} = \min(15, \text{ROCE} \times 0.6) + \max(0, 5 - \text{DebtToEquity} \times 3)$$

---

## 6. Execution: Chained SIP & Allocation

Multibagger investing involves high right-tail skewness. A systematic dollar-cost averaging (SIP) framework delivers three crucial benefits:
1. **Volatility Harvest**: Accumulates more shares during inevitable 15-25% base pullbacks.
2. **Behavioral Discipline**: Eliminates market-timing paralysis on high-momentum names.
3. **Proximity-to-High Weighting**: Allocates higher capital to stocks consolidating tightest near their 52-week highs (tightest supply absorption).
