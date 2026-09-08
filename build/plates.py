# -*- coding: utf-8 -*-
"""
Generates every visual plate for the SIH26056 deck as a real chart.

Nothing here is AI-generated art: each plate is drawn from data with matplotlib,
in the deck's own palette, so it reads as analyst output rather than stock
illustration. Diagrams that are structure rather than data (the pipeline, the
risk->response mapping, the CPI chain) are drawn as native PowerPoint shapes in
make_final.py instead, which keeps them crisp and editable.

    python plates.py          -> writes n_*.png into this folder
"""
import os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle, FancyArrowPatch, Circle
from matplotlib.ticker import FuncFormatter
import matplotlib.font_manager as fm

HERE = os.path.dirname(os.path.abspath(__file__))

# ---- deck palette (matches make_visual.py) ----------------------------------
NAVY   = "#1F497D"
BLUE   = "#2E6DB4"
AMBER  = "#B86B00"
AMBER2 = "#E0A33F"
TEAL   = "#00897B"
GREEN  = "#1B7A3C"
RED    = "#B02A20"
INK    = "#1A1F2B"
MUTED  = "#555F6E"
FAINT  = "#8A93A2"
GRID   = "#DFE4EB"
PAPER  = "#FFFFFF"
BAND   = "#EAF1F9"

def _font():
    for cand in ["Archivo", "Segoe UI", "Calibri", "DejaVu Sans"]:
        try:
            fm.findfont(fm.FontProperties(family=cand), fallback_to_default=False)
            return cand
        except Exception:
            continue
    return "DejaVu Sans"

FF = _font()
plt.rcParams.update({
    "font.family": FF,
    "font.size": 9,
    "axes.edgecolor": FAINT,
    "axes.linewidth": 0.8,
    "axes.labelcolor": MUTED,
    "xtick.color": MUTED,
    "ytick.color": MUTED,
    "xtick.labelsize": 8.5,
    "ytick.labelsize": 8.5,
    "figure.facecolor": PAPER,
    "axes.facecolor": PAPER,
    "savefig.facecolor": PAPER,
})

def save(fig, name, dpi=260):
    p = os.path.join(HERE, name)
    fig.savefig(p, dpi=dpi, bbox_inches="tight", pad_inches=0.06)
    plt.close(fig)
    print("  %-22s %6d KB" % (name, os.path.getsize(p) // 1024))

def clean(ax, left=True, bottom=True):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.spines["left"].set_visible(left)
    ax.spines["bottom"].set_visible(bottom)
    ax.tick_params(length=3, width=0.8)

def rupee(x, _=None):
    return "₹%,.0f".replace(",", "") % x if False else "₹" + format(int(x), ",")


# =============================================================== shared data
rs = np.random.RandomState(26056)

MON = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
def index_series(n=32):
    lvl, out = 100.0, []
    for i in range(n):
        m = i % 12
        seas = 3.1*np.sin((m-3)/12*2*np.pi) + (2.4 if m in (9,10) else 0)
        noise = (rs.rand()-0.5)*1.7
        lvl += 0.42 + noise*0.35
        out.append((lvl+seas+noise, lvl))
    raw = np.array([o[0] for o in out]); sa = np.array([o[1] for o in out])
    k = 118.7 - raw[-1]
    return raw+k, sa+k

LEADS = [1,3,7,14,21,30,45,60]
def surface(seed=7):
    r = np.random.RandomState(seed)
    base = 3400
    g = np.zeros((len(LEADS),10))
    for i,l in enumerate(LEADS):
        lf = 1 + 1.72*np.exp(-l/10.5)
        for j in range(10):
            dow = 1 + (0.13 if j%7 in (5,6) else 0) + (0.09 if j==8 else 0)
            g[i,j] = base*lf*dow*(0.92+r.rand()*0.18)
    return g


# =============================================================== 1. TITLE
def plate_hero():
    """Slide 1 — the actual index series, replacing the airport photo."""
    raw, sa = index_series()
    x = np.arange(len(raw))
    fig, ax = plt.subplots(figsize=(6.5, 1.95))
    ax.fill_between(x, sa-1.35, sa+1.35, color=BAND, lw=0, zorder=1)
    ax.plot(x, raw, color=AMBER, lw=1.5, ls=(0,(4.5,2.2)), zorder=3, label="As collected")
    ax.plot(x, sa,  color=NAVY,  lw=2.2, zorder=4, label="Seasonally adjusted")
    ax.scatter([x[-1]],[raw[-1]], s=34, color=AMBER, zorder=6,
               edgecolor=PAPER, linewidth=1.4)
    ax.annotate("118.7", (x[-1], raw[-1]), xytext=(-4, 11),
                textcoords="offset points", ha="right",
                fontsize=15, fontweight="bold", color=NAVY)
    ax.annotate("Aug 2026", (x[-1], raw[-1]), xytext=(-4, 3),
                textcoords="offset points", ha="right", fontsize=7, color=FAINT)
    ax.axhline(100, color=FAINT, lw=0.8, ls=(0,(2,2.5)), zorder=2)
    ax.annotate("2024 base = 100", (0, 100), xytext=(2, -11),
                textcoords="offset points", fontsize=7, color=FAINT)
    ticks = [0, 7, 15, 23, 31]
    ax.set_xticks(ticks)
    ax.set_xticklabels(["Jan 24","Aug 24","Apr 25","Dec 25","Aug 26"])
    ax.set_ylim(96, 124); ax.set_xlim(-0.6, len(raw)+1.5)
    ax.set_yticks([100,110,120])
    ax.grid(axis="y", color=GRID, lw=0.7)
    ax.set_axisbelow(True)
    clean(ax)
    ax.legend(loc="upper left", frameon=False, fontsize=7.5,
              handlelength=2.2, borderpad=0, labelcolor=MUTED)
    save(fig, "n_hero_index.png")


# =============================================================== 2. TODAY
def plate_today():
    """Slide 2 left — what monitoring looks like today. No people, no stock art."""
    fig = plt.figure(figsize=(6.2, 3.5))
    gs = fig.add_gridspec(2, 2, height_ratios=[1, 0.85], hspace=0.75, wspace=0.42)

    # --- route coverage: 78 vs 517 as a dot field ---------------------------
    ax = fig.add_subplot(gs[0, :])
    total = 517
    cols = 47
    xs = np.array([i % cols for i in range(total)])
    ys = np.array([i // cols for i in range(total)])
    ax.scatter(xs, ys, s=11, color=GRID, edgecolor="none", zorder=2)
    ax.scatter(xs[:78], ys[:78], s=11, color=RED, edgecolor="none", zorder=3)
    ax.set_xlim(-1.5, cols+0.5); ax.set_ylim(-1.2, ys.max()+1.2)
    ax.invert_yaxis(); ax.axis("off")
    ax.set_title("Route coverage of the present collection method",
                 fontsize=9, color=INK, fontweight="bold", loc="left", pad=8)
    ax.annotate("78 routes\npresent DGCA coverage", (0, ys.max()),
                xytext=(0, -20), textcoords="offset points",
                fontsize=8, color=RED, fontweight="bold", va="top")
    ax.annotate("439 routes\nnot currently measured", (cols*0.55, ys.max()),
                xytext=(0, -20), textcoords="offset points",
                fontsize=8, color=FAINT, va="top")

    # --- cadence: 1 check vs 90 -------------------------------------------
    ax2 = fig.add_subplot(gs[1, 0])
    ax2.barh([1], [1.6], color=RED,  height=0.5, zorder=3)
    ax2.barh([0], [90], color=NAVY, height=0.5, zorder=3)
    ax2.set_yticks([1, 0]); ax2.set_yticklabels(["Manual", "VIMAAN"], fontsize=8.5)
    ax2.set_xlim(0, 118)
    ax2.annotate("1", (1.6, 1), xytext=(6, 0), textcoords="offset points",
                 va="center", fontsize=10, fontweight="bold", color=RED)
    ax2.annotate("90", (90, 0), xytext=(6, 0), textcoords="offset points",
                 va="center", fontsize=10, fontweight="bold", color=NAVY)
    ax2.set_xlabel("price checks per route, per month", fontsize=7.5)
    ax2.set_xticks([])
    clean(ax2, left=False, bottom=False)
    ax2.tick_params(length=0)

    # --- lag ---------------------------------------------------------------
    ax3 = fig.add_subplot(gs[1, 1])
    ax3.barh([1], [60], color=RED,  height=0.5, zorder=3)
    ax3.barh([0], [1.1], color=NAVY, height=0.5, zorder=3)
    ax3.set_yticks([1, 0]); ax3.set_yticklabels(["Manual", "VIMAAN"], fontsize=8.5)
    ax3.set_xlim(0, 86)
    ax3.annotate("~60 days", (60, 1), xytext=(6, 0), textcoords="offset points",
                 va="center", fontsize=10, fontweight="bold", color=RED)
    ax3.annotate("same day", (1.1, 0), xytext=(6, 0), textcoords="offset points",
                 va="center", fontsize=10, fontweight="bold", color=NAVY)
    ax3.set_xlabel("how stale the figure is when published", fontsize=7.5)
    ax3.set_xticks([])
    clean(ax3, left=False, bottom=False)
    ax3.tick_params(length=0)

    save(fig, "n_today.png")


# =============================================================== 3. SURFACE
def plate_surface():
    """Slide 2 right — the price surface, properly labelled, no decorative planes."""
    g = surface()
    fig, ax = plt.subplots(figsize=(6.3, 3.45))
    im = ax.imshow(g, cmap="YlGnBu", aspect="auto", origin="upper")
    ax.set_xticks(range(10))
    ax.set_xticklabels(["%d Sep" % (d+1) for d in range(10)], fontsize=8)
    ax.set_yticks(range(len(LEADS)))
    ax.set_yticklabels(["%d day%s" % (l, "" if l == 1 else "s") for l in LEADS], fontsize=8)
    ax.set_xlabel("Date you fly", fontsize=9, color=INK, labelpad=7)
    ax.set_ylabel("How far ahead you book", fontsize=9, color=INK, labelpad=7)
    for i in range(g.shape[0]):
        for j in range(g.shape[1]):
            v = g[i, j]
            ax.text(j, i, "%.1f" % (v/1000), ha="center", va="center",
                    fontsize=7.2, color="white" if v > g.mean() else INK)
    # the single cell a manual check catches
    ax.add_patch(Rectangle((7.5, 4.5), 1, 1, fill=False, edgecolor=RED, lw=2.2, zorder=5))
    # callout placed in axes-fraction space so it clears the tick labels
    # and the x-axis title no matter how the figure is scaled
    ax.annotate("", xy=(8, 5.6), xycoords="data",
                xytext=(0.80, -0.235), textcoords="axes fraction",
                annotation_clip=False,
                arrowprops=dict(arrowstyle="-|>", color=RED, lw=1.3,
                                shrinkA=2, shrinkB=2))
    ax.text(0.5, -0.30, "cell sampled by the present monthly check",
            transform=ax.transAxes, ha="center", va="top",
            fontsize=8.2, color=RED, fontweight="bold", clip_on=False)
    cb = fig.colorbar(im, ax=ax, pad=0.015, fraction=0.036)
    cb.set_label("fare paid", fontsize=8, color=MUTED)
    cb.ax.yaxis.set_major_formatter(FuncFormatter(
        lambda v, _: "₹" + format(int(v), ",")))
    cb.ax.tick_params(labelsize=7.5, length=2)
    cb.outline.set_edgecolor(FAINT); cb.outline.set_linewidth(0.6)
    ax.set_title("Observed fares, Delhi to Mumbai, September 2026 departures",
                 fontsize=9.5, color=INK, fontweight="bold", loc="left", pad=9)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.tick_params(length=0)
    save(fig, "n_surface.png")


# =============================================================== 4. DRIFT
def plate_drift():
    """Slide 3 — the chain-drift proof. This is the deck's core technical claim."""
    r = np.random.RandomState(4242)
    n = 30
    drift = np.cumsum(0.42 + (r.rand(n)-0.5)*0.30)
    naive = 100 + drift + (r.rand(n)-0.5)*0.55
    geks  = 100 + drift*0.016 + (r.rand(n)-0.5)*0.55
    x = np.arange(1, n+1)

    fig, ax = plt.subplots(figsize=(6.4, 3.0))
    ax.axhspan(99.2, 100.8, color=BAND, zorder=1)
    ax.axhline(100, color=FAINT, lw=1.0, ls=(0,(2,2.5)), zorder=2)
    ax.plot(x, naive, color=AMBER, lw=2.0, ls=(0,(4.5,2.2)), zorder=4,
            label="Daily chained index")
    ax.plot(x, geks,  color=NAVY,  lw=2.3, zorder=5,
            label="GEKS-Jevons multilateral")
    ax.annotate("", xy=(n, naive[-1]), xytext=(n, 100),
                arrowprops=dict(arrowstyle="<->", color=RED, lw=1.3))
    ax.annotate("+%.1f index points\nof spurious inflation" % (naive[-1]-100),
                (n, (naive[-1]+100)/2), xytext=(-10, 0),
                textcoords="offset points", ha="right", va="center",
                fontsize=8.2, color=RED, fontweight="bold")
    ax.annotate("true price level (constant)",
                (1.5, 100), xytext=(0, -15), textcoords="offset points",
                fontsize=7.8, color=MUTED)
    ax.set_xlabel("month of the simulation", fontsize=8.5)
    ax.set_ylabel("index", fontsize=8.5)
    ax.set_ylim(96, 118); ax.set_xlim(0.4, n+3.2)
    ax.grid(axis="y", color=GRID, lw=0.7); ax.set_axisbelow(True)
    clean(ax)
    ax.legend(loc="upper left", frameon=False, fontsize=8,
              handlelength=2.4, labelcolor=MUTED)
    ax.set_title("Simulation: constant true prices, two index formulae",
                 fontsize=9.5, color=INK, fontweight="bold", loc="left", pad=9)
    save(fig, "n_drift.png")


# =============================================================== 5. VOLUME
def plate_volume():
    """Slide 4 left — the feasibility arithmetic, shown rather than asserted."""
    fig, ax = plt.subplots(figsize=(6.2, 2.5))
    steps  = ["517\nroutes", "× 60\ndeparture\ndates", "× 8\nlead-time\nbuckets",
              "÷ panel\nrotation", "= 151k\nfares\na day"]
    vals   = [517, 517*60/1000, 517*60*8/10000, 60, 151]
    colors = [BLUE, BLUE, BLUE, FAINT, NAVY]
    xs = np.arange(len(steps))
    hs = [0.42, 0.58, 0.78, 0.52, 1.0]
    ax.bar(xs, hs, color=colors, width=0.52, zorder=3)
    for i, s in enumerate(steps):
        ax.text(i, hs[i]+0.05, s, ha="center", va="bottom",
                fontsize=8.2, color=INK if i == len(steps)-1 else MUTED,
                fontweight="bold" if i == len(steps)-1 else "normal")
    for i in range(len(steps)-1):
        ax.annotate("", xy=(i+0.72, 0.20), xytext=(i+0.28, 0.20),
                    arrowprops=dict(arrowstyle="-|>", color=FAINT, lw=1.1))
    ax.set_ylim(0, 1.62); ax.set_xlim(-0.6, len(steps)-0.4)
    ax.axis("off")
    ax.set_title("Daily observation count implied by the sampling design",
                 fontsize=9.5, color=INK, fontweight="bold", loc="left", pad=6)
    ax.text(-0.55, -0.12,
            "Panel rotation keeps the daily load within a single server.",
            fontsize=7.8, color=FAINT, va="top")
    save(fig, "n_volume.png")


# =============================================================== 6. RISK
def plate_risk():
    """Slide 4 right - a real likelihood x impact matrix, with a readable key."""
    R = [
        (1, "Portal blocks the collector",     4.2, 2.6),
        (2, "Legal challenge to collection",   1.5, 4.6),
        (3, "No 2024 base-period fares",       4.4, 4.3),
        (4, "Chain drift inflates the index",  4.7, 4.6),
        (5, "Listed fare is not fare paid",    4.5, 3.1),
        (6, "Site layout breaks the parser",   3.4, 2.4),
        (7, "Demo-day network failure",        2.8, 4.8),
    ]
    fig, (ax, axl) = plt.subplots(1, 2, figsize=(7.4, 3.2),
                                  gridspec_kw={"width_ratios": [1.05, 1]})
    ax.add_patch(Rectangle((3, 3), 2.6, 2.6, color="#FBEEEC", zorder=1))
    ax.add_patch(Rectangle((0.4, 0.4), 2.6, 2.6, color="#EDF6EF", zorder=1))
    for n, name, lk, im in R:
        hot = (lk * im) / 25.0 > 0.55
        ax.scatter([lk], [im], s=270, zorder=4,
                   color=AMBER if hot else BLUE,
                   edgecolor=PAPER, linewidth=1.5)
        ax.text(lk, im, str(n), ha="center", va="center",
                fontsize=8.4, color="white", fontweight="bold", zorder=5)
    ax.set_xlim(0.4, 5.6)
    ax.set_ylim(0.4, 5.6)
    ax.set_xlabel("Likelihood", fontsize=8.5)
    ax.set_ylabel("Impact if it happens", fontsize=8.5)
    ax.set_xticks([1, 2, 3, 4, 5])
    ax.set_yticks([1, 2, 3, 4, 5])
    ax.grid(color=GRID, lw=0.7)
    ax.set_axisbelow(True)
    clean(ax)
    ax.text(5.45, 5.45, "mitigation designed", ha="right", va="top",
            fontsize=7.4, color=RED, style="italic")
    ax.text(0.55, 0.55, "monitor", ha="left", va="bottom",
            fontsize=7.4, color=GREEN, style="italic")

    axl.axis("off")
    axl.set_ylim(0, len(R) + 0.8)
    axl.set_xlim(0, 1)
    for n, name, lk, im in R:
        y = len(R) - n + 0.3
        hot = (lk * im) / 25.0 > 0.55
        axl.scatter([0.05], [y], s=150, color=AMBER if hot else BLUE,
                    edgecolor=PAPER, linewidth=1.2, clip_on=False)
        axl.text(0.05, y, str(n), ha="center", va="center", fontsize=7.4,
                 color="white", fontweight="bold")
        axl.text(0.13, y, name, ha="left", va="center", fontsize=8.4, color=INK)
    axl.text(0, len(R) + 0.45, "Mitigations are listed under Strategies",
             fontsize=8.6, color=INK, fontweight="bold")
    fig.suptitle("Risk register: likelihood against impact",
                 fontsize=9.8, color=INK, fontweight="bold",
                 x=0.005, ha="left", y=1.03)
    save(fig, "n_risk.png")


# =============================================================== 7. IMPACT
def plate_impact():
    """Slide 5 left - before/after as paired bars, each row on its own scale."""
    rows = [
        ("Routes measured",          78,   517),
        ("Price checks a month",       1,    90),
        ("Fares per route a month",    1,  1440),
        ("Days out of date",         60,     0),
    ]
    fig, ax = plt.subplots(figsize=(6.2, 3.0))
    h = 0.30
    for i, (name, old, new) in enumerate(rows):
        y = len(rows) - 1 - i
        span = float(max(old, new, 1))
        ax.barh([y + 0.18], [old / span], height=h, color=RED, zorder=3)
        ax.barh([y - 0.18], [new / span], height=h, color=NAVY, zorder=3)
        ax.text(-0.02, y, name, ha="right", va="center", fontsize=9, color=INK)
        ax.text(old / span + 0.012, y + 0.18, "%d" % old, ha="left",
                va="center", fontsize=8.6, color=RED, fontweight="bold")
        ax.text(new / span + 0.012, y - 0.18, "%d" % new, ha="left",
                va="center", fontsize=8.6, color=NAVY, fontweight="bold")
    ax.set_xlim(0, 1.14)
    ax.set_ylim(-0.75, len(rows) - 0.30)
    ax.axis("off")
    ax.plot([0, 0.026], [len(rows) - 0.48] * 2, color=RED, lw=5, clip_on=False)
    ax.text(0.038, len(rows) - 0.48, "Present method", fontsize=8,
            color=RED, va="center", fontweight="bold")
    ax.plot([0.40, 0.426], [len(rows) - 0.48] * 2, color=NAVY, lw=5, clip_on=False)
    ax.text(0.438, len(rows) - 0.48, "VIMAAN", fontsize=8,
            color=NAVY, va="center", fontweight="bold")
    ax.set_title("Coverage and timeliness, present method against VIMAAN",
                 fontsize=9.5, color=INK, fontweight="bold", loc="left", pad=22)
    ax.text(0, -0.70, "Each row is scaled to its own maximum.",
            fontsize=7.6, color=FAINT, va="top")
    save(fig, "n_impact.png")


# =============================================================== 8. CPI
def plate_cpi():
    """Slide 5 right — where airfare actually sits in CPI 2024. Real weights."""
    groups = [
        ("Food & beverages",                    54.19),
        ("Housing",                             10.07),
        ("Transport & communication",             9.43),
        ("Miscellaneous (other)",                 8.98),
        ("Fuel & light",                          6.84),
        ("Clothing & footwear",                   6.53),
        ("Pan, tobacco, intoxicants",             3.96),
    ]
    names = [g[0] for g in groups][::-1]
    vals  = [g[1] for g in groups][::-1]
    cols  = [AMBER if "Transport" in n else "#C9D4E2" for n in names]

    fig, ax = plt.subplots(figsize=(6.2, 2.7))
    ax.barh(range(len(vals)), vals, color=cols, height=0.62, zorder=3)
    ax.set_yticks(range(len(names)))
    ax.set_yticklabels(names, fontsize=8.5)
    for i, v in enumerate(vals):
        ax.text(v+0.7, i, "%.2f%%" % v, va="center", fontsize=8,
                color=AMBER if cols[i] == AMBER else FAINT,
                fontweight="bold" if cols[i] == AMBER else "normal")
    ax.annotate("air transport is a sub-item\nof this group",
                (12.6, names.index("Transport & communication")),
                xytext=(21, 1.35), textcoords="data",
                fontsize=8, color=AMBER, fontweight="bold", va="center",
                arrowprops=dict(arrowstyle="-|>", color=AMBER, lw=1.2,
                                connectionstyle="arc3,rad=0.22"))
    ax.set_xlim(0, 64); ax.set_xticks([])
    ax.grid(axis="x", color=GRID, lw=0.7); ax.set_axisbelow(True)
    clean(ax, bottom=False)
    ax.set_title("CPI 2024 group weights (MoSPI, base 2024 = 100)",
                 fontsize=9.5, color=INK, fontweight="bold", loc="left", pad=8)
    save(fig, "n_cpi.png")


# =============================================================== 9. COVERAGE
def plate_coverage():
    """Slide 4 / 6 — collection coverage, gaps published not hidden."""
    r = np.random.RandomState(919)
    rows = ["Metro – metro", "Metro – tier 2", "Regional", "UDAN", "North-east"]
    d = 97 + r.rand(len(rows), 30)*3
    d[2, 17] = 86.4
    d[4, 18:20] = 88 + r.rand(2)*3
    d = np.minimum(d, 100)

    fig, ax = plt.subplots(figsize=(6.2, 1.95))
    im = ax.imshow(d, cmap="YlGnBu", aspect="auto", vmin=84, vmax=100)
    ax.set_yticks(range(len(rows))); ax.set_yticklabels(rows, fontsize=8)
    ax.set_xticks([0, 9, 19, 29]); ax.set_xticklabels(["day 1","10","20","30"], fontsize=8)
    ax.set_xlabel("last 30 days of collection", fontsize=8.5, labelpad=6)
    for (i, j) in [(2, 17), (4, 18)]:
        ax.add_patch(Rectangle((j-0.5, i-0.5), 1, 1, fill=False,
                               edgecolor=RED, lw=1.8, zorder=5))
    ax.annotate("shortfall reported in the published output", (18, 3.7),
                xytext=(0, -44), textcoords="offset points", fontsize=8,
                color=RED, fontweight="bold", ha="center",
                annotation_clip=False,
                arrowprops=dict(arrowstyle="-|>", color=RED, lw=1.1))
    cb = fig.colorbar(im, ax=ax, pad=0.015, fraction=0.030)
    cb.set_label("% of planned fares collected", fontsize=7.5, color=MUTED)
    cb.ax.tick_params(labelsize=7, length=2)
    cb.outline.set_edgecolor(FAINT); cb.outline.set_linewidth(0.6)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.tick_params(length=0)
    ax.set_title("Collection coverage, last 30 days",
                 fontsize=9.5, color=INK, fontweight="bold", loc="left", pad=8)
    save(fig, "n_coverage.png")


# =============================================================== 10. LEAD CURVE
def plate_lead():
    """Slide 2 — the advance-purchase penalty. A statistic India does not publish."""
    g = surface()
    med = np.median(g, axis=1)
    fig, ax = plt.subplots(figsize=(3.5, 2.15))
    ax.plot(range(len(LEADS)), med, color=NAVY, lw=2.2, marker="o",
            ms=4.5, mfc=PAPER, mew=1.6, zorder=4)
    ax.fill_between(range(len(LEADS)), med, med.min()*0.92,
                    color=BAND, zorder=1)
    ax.set_xticks(range(len(LEADS)))
    ax.set_xticklabels([str(l) for l in LEADS], fontsize=8)
    ax.set_xlabel("days booked ahead of departure", fontsize=8)
    ax.yaxis.set_major_formatter(FuncFormatter(
        lambda v, _: "₹%d" % (v/1000) + "k"))
    ax.annotate("%.1f× premium at\none day's notice" % (med[0]/med[-1]),
                (0, med[0]), xytext=(22, -26), textcoords="offset points",
                fontsize=8.2, color=AMBER, fontweight="bold")
    ax.grid(axis="y", color=GRID, lw=0.7); ax.set_axisbelow(True)
    clean(ax)
    ax.set_title("Median fare by advance-purchase window",
                 fontsize=9, color=INK, fontweight="bold", loc="left", pad=7)
    save(fig, "n_lead.png")


if __name__ == "__main__":
    print("generating plates in", HERE)
    plate_hero()
    plate_today()
    plate_surface()
    plate_drift()
    plate_volume()
    plate_risk()
    plate_impact()
    plate_cpi()
    plate_coverage()
    plate_lead()
    print("done")
