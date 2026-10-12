#!/usr/bin/env python3
"""Build this week's meal-plan ebook for the Kindle 4.

    python3 generate.py                  # the week starting this Monday
    python3 generate.py --week 2026-10-19
    python3 generate.py --force          # re-roll a week that was already planned

Writes HTML pages and PNG images to build/<week>/, converts them with Calibre's
ebook-convert, and leaves the result in dist/. history.json records every week
so the next one rotates to recipes that were used least recently.
"""
import argparse
import datetime as dt
import html
import json
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path

from png import BLACK, DITHER, WHITE, Canvas
from recipes import BY_ID, POOLS

ROOT = Path(__file__).resolve().parent
HISTORY = ROOT / "history.json"
EBOOK_CONVERT = os.environ.get(
    "EBOOK_CONVERT", "/Applications/calibre.app/Contents/MacOS/ebook-convert")

TARGET = 90          # grams of protein a day
SALMON_NIGHTS = 5
SCREEN_W = 560       # image width that leaves a margin on the 600 px screen
NEVER = 999


# ---------------------------------------------------------------- planning

def slot_of(rid):
    return {"b": "Breakfast", "l": "Lunch", "n": "Snack"}.get(rid[0], "Dinner")


def load_history():
    if HISTORY.exists():
        return json.loads(HISTORY.read_text())
    return {"weeks": {}}


def recipe_ages(history, week_start):
    """Weeks since each recipe was last used before `week_start`."""
    ages = {}
    for key, days in history["weeks"].items():
        gap = (week_start - dt.date.fromisoformat(key)).days // 7
        if gap <= 0:
            continue
        for day in days:
            for rid in day:
                ages[rid] = min(ages.get(rid, NEVER), gap)
    return ages


def plan_week(week_start, history, rng):
    ages = recipe_ages(history, week_start)

    def pick(pool_name, n):
        pool = [r[0] for r in POOLS[pool_name]]
        pool.sort(key=lambda rid: (-ages.get(rid, NEVER), rng.random()))
        chosen = pool[:n]
        rng.shuffle(chosen)
        return chosen

    breakfasts, lunches = pick("Breakfast", 7), pick("Lunch", 7)
    salmon, other = pick("Salmon", SALMON_NIGHTS), pick("Dinner", 7 - SALMON_NIGHTS)
    salmon_days = set(rng.sample(range(7), SALMON_NIGHTS))

    snack_uses = {r[0]: 0 for r in POOLS["Snack"]}
    days = []
    for i in range(7):
        dinner = salmon.pop() if i in salmon_days else other.pop()
        day = [breakfasts[i], lunches[i], dinner]
        total = sum(BY_ID[r][2] for r in day)
        while total < TARGET:
            need = TARGET - total
            left = [s for s in snack_uses if s not in day]
            enough = [s for s in left if BY_ID[s][2] >= need]
            if enough:  # smallest snack that closes the gap, spread across the week
                snack = min(enough, key=lambda s: (snack_uses[s], BY_ID[s][2], rng.random()))
            else:
                snack = max(left, key=lambda s: (BY_ID[s][2], rng.random()))
            day.append(snack)
            snack_uses[snack] += 1
            total += BY_ID[snack][2]
        days.append(day)
    return days


def validate(days, previous):
    problems = []
    salmon = sum(1 for day in days if any(r.startswith("s_") for r in day))
    if salmon != SALMON_NIGHTS:
        problems.append(f"{salmon} salmon nights, expected {SALMON_NIGHTS}")
    for i, day in enumerate(days):
        total = sum(BY_ID[r][2] for r in day)
        if total < TARGET:
            problems.append(f"day {i} has {total} g protein")
        for rid in day:
            _, name, _, _, ingredients, method = BY_ID[rid]
            text = " ".join([name, method] + ingredients).lower()
            if "avocado" in text:
                problems.append(f"{rid} mentions avocado")
            for ing in ingredients:
                amount, _, item = ing.partition("|")
                if not amount.strip() or not item.strip():
                    problems.append(f"{rid}: ingredient without a quantity: {ing!r}")
    if previous is not None and days == previous:
        problems.append("identical to the previous week")
    if problems:
        sys.exit("Plan failed its checks:\n  " + "\n  ".join(problems))


# ------------------------------------------------------------------ images

def draw_icon(kind, scale):
    """Icons are designed on a 100x60 grid and scaled up."""
    c = Canvas(int(100 * scale), int(60 * scale))
    k = scale
    stroke = max(2, round(2 * k))

    def box(x0, y0, x1, y1, fill=BLACK):
        c.rect(x0 * k, y0 * k, x1 * k, y1 * k, fill)

    if kind == "fish":
        c.triangle((70 * k, 32 * k), (92 * k, 18 * k), (92 * k, 46 * k), BLACK)
        c.outlined_ellipse(41 * k, 32 * k, 31 * k, 17 * k, DITHER, stroke)
        c.ellipse(24 * k, 29 * k, 3 * k, 3 * k, WHITE)
        c.ellipse(24 * k, 29 * k, 2 * k, 2 * k, BLACK)
    elif kind == "pan":
        box(74, 31, 97, 37)
        c.outlined_ellipse(42 * k, 34 * k, 34 * k, 20 * k, DITHER, stroke)
        c.outlined_ellipse(42 * k, 34 * k, 10 * k, 8 * k, WHITE, stroke)
    elif kind == "nut":
        c.outlined_ellipse(36 * k, 34 * k, 19 * k, 12 * k, DITHER, stroke, rot=-25)
        c.outlined_ellipse(64 * k, 36 * k, 19 * k, 12 * k, DITHER, stroke, rot=20)
    else:  # bowl
        below_rim = lambda x, y: y >= 28 * k
        c.outlined_ellipse(50 * k, 28 * k, 38 * k, 28 * k, DITHER, stroke, clip=below_rim)
        box(8, 26, 92, 30)
        for x in (38, 54, 70):  # steam
            box(x, 14, x + 2.5, 21)
            box(x - 2.5, 6, x, 14)
    return c


def draw_week_chart(totals):
    w, h = SCREEN_W, 250
    c = Canvas(w, h)
    top, base, left, right = 30, h - 36, 8, w - 62
    y_max = max(110, max(totals) + 10)
    y_of = lambda grams: base - (base - top) * grams / y_max
    slot = (right - left) / 7
    for i, grams in enumerate(totals):
        x0, x1 = left + i * slot + slot * 0.18, left + (i + 1) * slot - slot * 0.18
        c.rect(x0, y_of(grams), x1, base, BLACK)
        label = str(grams)
        mid = (x0 + x1) / 2
        c.text(int(mid - Canvas.text_width(label, 2) / 2), int(y_of(grams)) - 18, label, 2)
        c.text(int(mid - Canvas.text_width("M", 3) / 2), base + 10, "MTWTFSS"[i], 3)
    c.rect(left, base, right, base + 3, BLACK)
    y_target = int(y_of(TARGET))
    # White gaps where the target line crosses the black bars keep it visible.
    c.hdash(left, right + 6, y_target, width=3, dash=8, gap=6, fill=WHITE)
    c.hdash(left, right + 6, y_target, width=1, dash=8, gap=6, fill=BLACK)
    c.text(right + 10, y_target - 6, f"{TARGET}g", 2)
    return c


def draw_day_bar(proteins):
    w, h = SCREEN_W, 46
    c = Canvas(w, h)
    x0, x1, y0, y1 = 2, w - 2, 8, h - 8
    full = max(sum(proteins), 100)
    per_gram = (x1 - x0) / full
    shades = [BLACK, 85, 150, 205, 110, 180]
    x = x0
    for i, grams in enumerate(proteins):
        nxt = x + grams * per_gram
        c.rect(x, y0, nxt - 2, y1, shades[i % len(shades)])
        x = nxt
    c.frame(x0, y0, x1, y1, 2)
    target_x = int(x0 + TARGET * per_gram)
    c.vdash(target_x - 2, 0, h, width=5, dash=6, gap=4, fill=WHITE)
    c.vdash(target_x - 1, 0, h, width=3, dash=6, gap=4, fill=BLACK)
    return c


# ------------------------------------------------------------------- pages

CSS = """
body { font-family: serif; margin: 0; }
h1 { font-size: 1.5em; margin: 0 0 0.2em 0; }
h2 { font-size: 1em; margin: 1em 0 0.3em 0; border-bottom: 1px solid #000; }
p { margin: 0.3em 0; }
p.sub { font-size: 0.8em; color: #444; margin-bottom: 0.8em; }
p.lnk { margin: 0; padding: 0.4em 0; border-bottom: 1px dotted #777; }
p.nav { margin: 0; padding: 0.4em 0; border-bottom: 1px dotted #777; }
small { font-size: 0.8em; }
div.pic { text-align: center; margin: 0.3em 0 0.6em 0; }
table { width: 100%; border-collapse: collapse; }
td { padding: 0.3em 0.3em; border-bottom: 1px dotted #777; vertical-align: middle; }
td.icon { width: 30%; }
td.amt { width: 32%; font-weight: bold; vertical-align: top; }
td.item { vertical-align: top; }
"""


def page(title, body):
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml"><head>'
        '<meta http-equiv="Content-Type" content="text/html; charset=utf-8"/>'
        f"<title>{html.escape(title)}</title>"
        '<link rel="stylesheet" type="text/css" href="style.css"/>'
        f"</head><body>\n{body}\n</body></html>\n")


def day_label(date):
    return f"{date:%a} {date.day} {date:%b}"


def write_pages(out, week_start, days):
    esc = html.escape
    dates = [week_start + dt.timedelta(days=i) for i in range(7)]
    totals = [sum(BY_ID[r][2] for r in day) for day in days]
    average = round(sum(totals) / 7)
    week_label = f"{week_start.day} {week_start:%b}"

    (out / "style.css").write_text(CSS)
    images = out / "images"
    images.mkdir(exist_ok=True)
    draw_week_chart(totals).save(images / "week.png")
    for kind in ("bowl", "fish", "pan", "nut"):
        draw_icon(kind, 1.5).save(images / f"{kind}.png")
        draw_icon(kind, 3).save(images / f"{kind}_big.png")

    rows = []
    for i, (date, day) in enumerate(zip(dates, days)):
        dinner = next(r for r in day if slot_of(r) == "Dinner")
        rows.append(
            f'<p class="lnk"><a href="day{i}.html">{day_label(date)}</a><br/>'
            f"<small>{esc(BY_ID[dinner][1])} · {totals[i]} g</small></p>")
    (out / "index.html").write_text(page(
        f"Meal plan, week of {week_label}",
        "<h1>This week's plan</h1>"
        f'<p class="sub">Week of {week_label}. About {average} g protein a day. '
        f"Dashed line is your {TARGET} g target.</p>"
        '<div class="pic"><img src="images/week.png" alt="Protein per day"/></div>'
        + "\n".join(rows)))

    for i, (date, day) in enumerate(zip(dates, days)):
        draw_day_bar([BY_ID[r][2] for r in day]).save(images / f"day{i}.png")
        rows = []
        for m, rid in enumerate(day):
            _, name, protein, kind, _, _ = BY_ID[rid]
            rows.append(
                f'<tr><td class="icon"><img src="images/{kind}.png" alt=""/></td>'
                f'<td><a href="r{i}_{m}.html">{slot_of(rid)}: {esc(name)}</a><br/>'
                f"<small>{protein} g protein</small></td></tr>")
        (out / f"day{i}.html").write_text(page(
            day_label(date),
            f"<h1>{day_label(date)}</h1>"
            f'<div class="pic"><img src="images/day{i}.png" alt=""/></div>'
            f'<p class="sub">{totals[i]} g protein. Dashed line marks {TARGET} g.</p>'
            f"<table>{''.join(rows)}</table>"
            '<p class="nav"><a href="index.html">Week overview</a></p>'))

        for m, rid in enumerate(day):
            _, name, protein, kind, ingredients, method = BY_ID[rid]
            table = "".join(
                '<tr><td class="amt">{}</td><td class="item">{}</td></tr>'.format(
                    *map(esc, ing.split("|", 1)))
                for ing in ingredients)
            (out / f"r{i}_{m}.html").write_text(page(
                name,
                f'<div class="pic"><img src="images/{kind}_big.png" alt=""/></div>'
                f"<h1>{esc(name)}</h1>"
                f'<p class="sub">{slot_of(rid)} · about {protein} g protein</p>'
                f"<h2>Ingredients</h2><table>{table}</table>"
                f"<h2>Method</h2><p>{esc(method)}</p>"
                "<h2>Navigate</h2>"
                f'<p class="nav"><a href="day{i}.html">Back to {date:%a}</a></p>'
                '<p class="nav"><a href="index.html">Week overview</a></p>'))
    return week_label, totals


def convert(out, week_start, week_label):
    book = out / f"meal-plan-{week_start.isoformat()}.mobi"
    cmd = [
        EBOOK_CONVERT, str(out / "index.html"), str(book),
        "--title", f"Meal Plan - {week_label}",
        "--authors", "Meal Plan",
        "--language", "en",
        "--pubdate", week_start.isoformat(),
        "--output-profile", "kindle",
        "--mobi-file-type", "both",
        "--max-levels", "2",
        "--chapter", "/",
        "--page-breaks-before", "/",
        "--no-inline-toc",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    (out / "ebook-convert.log").write_text(result.stdout + result.stderr)
    if result.returncode != 0 or not book.exists():
        sys.exit(f"ebook-convert failed, see {out / 'ebook-convert.log'}")
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    shutil.copyfile(book, dist / "meal-plan.mobi")
    return book


# -------------------------------------------------------------------- main

def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--week", help="Monday the week starts on (YYYY-MM-DD)")
    parser.add_argument("--force", action="store_true", help="re-roll an existing week")
    parser.add_argument("--no-convert", action="store_true", help="write HTML only")
    args = parser.parse_args()

    today = dt.date.today()
    week_start = (dt.date.fromisoformat(args.week) if args.week
                  else today - dt.timedelta(days=today.weekday()))
    if week_start.weekday() != 0:
        sys.exit(f"{week_start} is not a Monday")
    key = week_start.isoformat()

    history = load_history()
    if key in history["weeks"] and not args.force:
        days = history["weeks"][key]
    else:
        earlier = sorted(k for k in history["weeks"] if k < key)
        previous = history["weeks"][earlier[-1]] if earlier else None
        seed = key if not args.force else f"{key}-{random.random()}"
        days = plan_week(week_start, history, random.Random(seed))
        validate(days, previous)
        history["weeks"][key] = days
        HISTORY.write_text(json.dumps(history, indent=1) + "\n")

    out = ROOT / "build" / key
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    week_label, totals = write_pages(out, week_start, days)

    print(f"Week of {week_label}: protein per day {totals}")
    for date_i, day in enumerate(days):
        date = week_start + dt.timedelta(days=date_i)
        print(f"  {day_label(date):<11}", " | ".join(BY_ID[r][1] for r in day))
    if args.no_convert:
        print(f"HTML in {out}")
    else:
        print(f"Ebook: {convert(out, week_start, week_label)}")


if __name__ == "__main__":
    main()
