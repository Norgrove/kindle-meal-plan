#!/usr/bin/env python3
"""Render this week's plan as full-screen pages for the Kindle app.

    python3 app.py                 # the week starting this Monday
    python3 app.py --week 2026-10-19

Every screen the app can show (each page with each possible highlight) is
drawn as HTML, photographed at 600x800 by headless Chrome, and converted to a
grayscale PNG. nav.txt tells the on-device script which screen each button
leads to. Output goes to dist/app/, and dist/site/ holds the download bundle.
"""
import argparse
import datetime as dt
import hashlib
import html
import math
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

import generate as g
from recipes import BY_ID, SERVINGS

ROOT = Path(__file__).resolve().parent
MAC_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CHROME = (os.environ.get("CHROME")
          or (MAC_CHROME if Path(MAC_CHROME).exists() else None)
          or shutil.which("google-chrome") or shutil.which("chromium")
          or shutil.which("chromium-browser"))
SANDBOX = ["--no-sandbox"] if os.environ.get("CI") else []  # CI runners cannot use the sandbox
GRAY_PROFILE = "/System/Library/ColorSync/Profiles/Generic Gray Gamma 2.2 Profile.icc"

PAGE_HEIGHT = 690    # usable pixels between the top bar and the footer
CHARS_PER_LINE = 40  # conservative estimate for 25 px body text across the page

ICONS = {
    "bowl": '<path d="M12 28h76c0 18-14 28-38 28S12 46 12 28z" fill="url(#dt)" stroke="#000" stroke-width="3"/>'
            '<path d="M8 28h84" stroke="#000" stroke-width="4"/>'
            '<path d="M38 20c-4-5 4-8 0-13M54 20c-4-5 4-8 0-13M70 20c-4-5 4-8 0-13" fill="none" stroke="#000" stroke-width="3"/>',
    "fish": '<path d="M10 32c14-20 44-20 62 0-18 20-48 20-62 0z" fill="url(#dt)" stroke="#000" stroke-width="3"/>'
            '<path d="M72 32l20-14v28z" fill="#000"/><circle cx="26" cy="29" r="3" fill="#000"/>',
    "pan": '<ellipse cx="42" cy="34" rx="34" ry="20" fill="url(#dt)" stroke="#000" stroke-width="3"/>'
           '<circle cx="42" cy="34" r="9" fill="#fff" stroke="#000" stroke-width="3"/>'
           '<path d="M76 34h20" stroke="#000" stroke-width="7"/>',
    "nut": '<ellipse cx="36" cy="34" rx="18" ry="12" fill="url(#dt)" stroke="#000" stroke-width="3" transform="rotate(-25 36 34)"/>'
           '<ellipse cx="64" cy="36" rx="18" ry="12" fill="url(#dt)" stroke="#000" stroke-width="3" transform="rotate(20 64 36)"/>',
}

CSS = """
*{box-sizing:border-box}
html,body{margin:0;width:600px;height:800px;overflow:hidden;background:#fff;color:#000;
  font-family:Georgia,Gelasio,"Liberation Serif","Times New Roman",serif}
.scr{width:600px;height:800px;display:flex;flex-direction:column}
.bar,.ft{display:flex;justify-content:space-between;
  font:19px/1 -apple-system,"Helvetica Neue","Liberation Sans","DejaVu Sans",sans-serif;padding:10px 20px}
.bar{border-bottom:3px solid #000}.ft{border-top:3px solid #000;font-size:17px}
.body{flex:1;overflow:hidden;padding:14px 24px 0;font-size:25px;line-height:1.28}
h1{font:400 40px/1.1 Impact,Anton,"Arial Black",sans-serif;letter-spacing:1px;margin:0 0 6px}
h1.sm{font-size:30px}
h2{font-size:24px;margin:12px 0 2px;border-bottom:2px solid #000}
.sub{font-size:20px;line-height:1.3;color:#333;margin:0 0 10px}
.lnk{display:flex;gap:16px;align-items:center;padding:5px 12px;margin:0 -12px;border-bottom:2px dotted #666}
.lnk small{display:block;font-size:19px;line-height:1.25}
.lnk.on{background:#000;color:#fff}.lnk.on svg{filter:invert(1)}
.lnk .g{margin-left:auto;font:22px -apple-system,"Liberation Sans",sans-serif;white-space:nowrap}
.wk .lnk{padding:2px 12px;font-size:23px;line-height:1.18}.wk .lnk small{font-size:17px;line-height:1.18}
.ic{width:120px;height:72px;flex:none}.big{width:160px;height:96px;display:block;margin:0 auto}
.grid{position:relative;display:flex;align-items:flex-end;height:112px;margin:4px 0 6px;border-bottom:3px solid #000}
.grid div{flex:1;text-align:center;position:relative}
.grid i{display:flex;align-items:flex-end;justify-content:center;background:#000;margin:0 12px;color:#fff;
  font:normal 17px/22px -apple-system,"Liberation Sans",sans-serif}
.grid b{display:block;font:19px/24px -apple-system,"Liberation Sans",sans-serif}
.grid u{position:absolute;left:0;right:0;border-top:3px dashed #fff;mix-blend-mode:difference}
.seg{position:relative;display:flex;height:28px;border:3px solid #000;margin:10px 0}
.seg i{display:block;height:100%;border-right:2px solid #fff}
.seg u{position:absolute;top:-9px;bottom:-9px;width:0;border-left:4px dashed #000;outline:1px solid #fff}
table{width:100%;border-collapse:collapse;font-size:24px}
td{padding:3px 6px;border-bottom:2px dotted #666;vertical-align:top}
table.ing td:first-child{width:30%;font-weight:bold;white-space:nowrap}
table.shop{font-size:22px}
table.shop td:last-child{text-align:right;font-weight:bold;white-space:nowrap}
table.shop small{font-weight:normal;font-size:17px}
ol{margin:6px 0 0;padding-left:34px}
li{margin:0 0 9px;padding-left:4px}
p{margin:6px 0}
"""

DEFS = ('<svg width="0" height="0" style="position:absolute"><defs>'
        '<pattern id="dt" width="4" height="4" patternUnits="userSpaceOnUse">'
        '<rect width="4" height="4" fill="#fff"/><rect width="2" height="2" fill="#444"/>'
        '<rect x="2" y="2" width="2" height="2" fill="#444"/></pattern></defs></svg>')

esc = html.escape


def picture(recipe, hero=False):
    return f'<svg class="{"big" if hero else "ic"}" viewBox="0 0 100 60">{ICONS[recipe["kind"]]}</svg>'


def screen(week_label, crumb, hint, body):
    return (f'<!DOCTYPE html><html><head><meta charset="utf-8"><style>{CSS}</style></head><body>'
            f'{DEFS}<div class="scr"><div class="bar"><span>Meal Plan</span>'
            f'<span>Week of {week_label}</span></div><div class="body">{body}</div>'
            f'<div class="ft"><span>{esc(crumb)}</span><span>{hint}</span></div></div></body></html>')


def lines_of(text, per_line=CHARS_PER_LINE):
    return max(1, math.ceil(len(text) / per_line))


# ------------------------------------------------------------------ screens

def week_screen(week_label, dates, days, totals, item_count, selected):
    scale = 86 / max(100, max(totals))
    bars = "".join(f'<div><i style="height:{t * scale}px">{t}</i><b>{"MTWTFSS"[i]}</b></div>'
                   for i, t in enumerate(totals))
    rows = ""
    for i, (date, day) in enumerate(zip(dates, days)):
        dinner = next(r for r in day if g.slot_of(r) == "Dinner")
        rows += (f'<div class="lnk{" on" if i == selected else ""}"><span>{g.day_label(date)}'
                 f'<small>{esc(BY_ID[dinner]["name"])}</small></span><span class="g">{totals[i]} g</span></div>')
    rows += (f'<div class="lnk{" on" if selected == 7 else ""}"><span>Shopping list'
             f'<small>{item_count} items for {SERVINGS} people</small></span><span class="g">▶</span></div>')
    body = ("<h1>This week's plan</h1>"
            f'<p class="sub">About {round(sum(totals) / 7)} g protein a day each. '
            f"Dashed line is the {g.TARGET} g target.</p>"
            f'<div class="grid">{bars}<u style="bottom:{g.TARGET * scale + 24}px"></u></div>'
            f'<div class="wk">{rows}</div>')
    return screen(week_label, f"Week of {week_label}", "▲▼ move · ● open · Home exit", body)


def day_bar(day):
    shades = ["#000", "#555", "#999", "#ccc", "#777", "#bbb"]
    full = max(g.protein(day), 100)
    segs = "".join(f'<i style="width:{BY_ID[r]["protein"] / full * 100}%;background:{shades[m % 6]}"></i>'
                   for m, r in enumerate(day))
    return f'<div class="seg">{segs}<u style="left:{g.TARGET / full * 100}%"></u></div>'


def day_screen(week_label, date, day, selected):
    rows = "".join(
        f'<div class="lnk{" on" if m == selected else ""}">{picture(BY_ID[r])}'
        f'<span>{g.slot_of(r)}: {esc(BY_ID[r]["name"])}'
        f'<small>{BY_ID[r]["protein"]} g protein each</small></span></div>'
        for m, r in enumerate(day))
    body = (f"<h1>{g.day_label(date)}</h1>{day_bar(day)}"
            f'<p class="sub">{g.protein(day)} g protein each. Dashed line marks {g.TARGET} g.</p>{rows}')
    return screen(week_label, g.day_label(date), "▲▼ move · ● open · ◀▶ day · Back", body)


def recipe_screens(week_label, date, rid):
    """One screen when the whole recipe fits, otherwise ingredients first and the method after."""
    recipe = BY_ID[rid]
    slot, name = g.slot_of(rid), recipe["name"]
    times = f"Prep {recipe['prep']} min" if recipe["prep"] else "No prep"
    times += f" · Cook {recipe['cook']} min" if recipe["cook"] else " · No cooking"
    table = "".join(
        f"<tr><td>{esc(g.amount(qty * SERVINGS, unit))}</td>"
        f"<td>{esc(g.item_name(item, qty * SERVINGS, unit))}</td></tr>"
        for qty, unit, item in recipe["ing"])
    top = (f"{picture(recipe, hero=True)}<h1>{esc(name)}</h1>"
           f'<p class="sub">{slot} for {SERVINGS} · about {recipe["protein"]} g protein each<br>{times}</p>'
           f'<h2>Ingredients</h2><table class="ing">{table}</table>')
    top_height = 96 + 46 * lines_of(name, 26) + 62 + 44 + 37 * len(recipe["ing"])

    step_heights = [32 * lines_of(step, CHARS_PER_LINE - 3) + 9 for step in recipe["steps"]]
    crumb = f"{date:%a} · {slot}"

    def method(steps, first_number):
        items = "".join(f"<li>{esc(s)}</li>" for s in steps)
        return f'<h2>Method</h2><ol start="{first_number}">{items}</ol>'

    if top_height + 44 + sum(step_heights) <= PAGE_HEIGHT:
        return [screen(week_label, crumb, "Back", top + method(recipe["steps"], 1))]

    pages = [top]
    budget = PAGE_HEIGHT - 46 * lines_of(name, 34) - 50
    start, used, chunk = 1, 0, []
    for step, height in zip(recipe["steps"], step_heights):
        if chunk and used + height > budget:
            pages.append(f'<h1 class="sm">{esc(name)}</h1>' + method(chunk, start))
            start, used, chunk = start + len(chunk), 0, []
        chunk.append(step)
        used += height
    pages.append(f'<h1 class="sm">{esc(name)}</h1>' + method(chunk, start))

    screens = []
    for n, body in enumerate(pages):
        if n == 0:
            hint = "▼ method · Back"
        elif n == len(pages) - 1:
            hint = "▲ ingredients · Back" if n == 1 else "▲ previous · Back"
        else:
            hint = "▲▼ more · Back"
        screens.append(screen(week_label, f"{crumb} · {n + 1} of {len(pages)}", hint, body))
    return screens


def shopping_screens(week_label, sections, staples):
    """Lay the list out over as many screens as it needs, keeping headings with their items."""
    row_height, heading_height = 34, 46
    first_budget, budget = PAGE_HEIGHT - 96, PAGE_HEIGHT - 10
    pages, body, used, open_section, seen = [], "", 0, None, set()

    def close_page():
        nonlocal body, used, open_section
        if body:
            pages.append(body + ("</table>" if open_section else ""))
        body, used, open_section = "", 0, None

    for section, lines in sections:
        for name, amount in lines:
            limit = budget if pages else first_budget
            need = row_height + (heading_height if section != open_section else 0)
            if used + need > limit:
                close_page()
            if section != open_section:
                if open_section:
                    body += "</table>"
                title = esc(section) + (" (continued)" if section in seen else "")
                body += f'<h2>{title}</h2><table class="shop">'
                used += heading_height
                open_section = section
                seen.add(section)
            amount = esc(amount).replace("(", "<small>(").replace(")", ")</small>")
            body += f"<tr><td>{esc(name)}</td><td>{amount}</td></tr>"
            used += row_height

    staple_text = ", ".join(staples)
    staple_height = heading_height + 30 * lines_of(staple_text, 46) + 10
    if used + staple_height > (budget if pages else first_budget):
        close_page()
    if open_section:
        body += "</table>"
        open_section = None
    body += f'<h2>Check you have</h2><p style="font-size:22px">{esc(staple_text)}</p>'
    close_page()

    count = sum(len(lines) for _, lines in sections)
    screens = []
    for n, page in enumerate(pages):
        head = ("<h1>Shopping list</h1>"
                f'<p class="sub">{count} items for {SERVINGS} people, covering all seven days.</p>'
                if n == 0 else "")
        if len(pages) == 1:
            hint = "Back"
        elif n == 0:
            hint = "▼ more · Back"
        elif n == len(pages) - 1:
            hint = "▲ previous · Back"
        else:
            hint = "▲▼ more · Back"
        screens.append(screen(week_label, f"Shopping list · {n + 1} of {len(pages)}", hint, head + page))
    return screens


def build_screens(week_start, days):
    """Return ({state: html}, [nav rows]). A nav row is: state up down select back left right."""
    dates = [week_start + dt.timedelta(days=i) for i in range(7)]
    totals = [g.protein(day) for day in days]
    week_label = f"{week_start.day} {week_start:%b}"
    sections, staples = g.shopping_list(days)
    item_count = sum(len(lines) for _, lines in sections)
    pages, nav = {}, []

    def paged(prefix, screens, back):
        """States for a run of screens flipped with up/down: prefix, prefix+'b', prefix+'c'..."""
        names = [prefix + ("" if n == 0 else chr(ord("a") + n)) for n in range(len(screens))]
        for n, (name, markup) in enumerate(zip(names, screens)):
            before = names[n - 1] if n else name
            after = names[n + 1] if n + 1 < len(names) else name
            select = names[n + 1] if n + 1 < len(names) else back
            pages[name] = markup
            nav.append((name, before, after, select, back, before, after))

    for i in range(8):
        pages[f"w{i}"] = week_screen(week_label, dates, days, totals, item_count, i)
        select = f"d{i}_0" if i < 7 else "s0"
        nav.append((f"w{i}", f"w{max(i - 1, 0)}", f"w{min(i + 1, 7)}", select, "-", "-", "-"))
    for i, (date, day) in enumerate(zip(dates, days)):
        last = len(day) - 1
        for m, rid in enumerate(day):
            pages[f"d{i}_{m}"] = day_screen(week_label, date, day, m)
            nav.append((f"d{i}_{m}", f"d{i}_{max(m - 1, 0)}", f"d{i}_{min(m + 1, last)}", f"r{i}_{m}",
                        f"w{i}", f"d{max(i - 1, 0)}_0", f"d{min(i + 1, 6)}_0"))
            paged(f"r{i}_{m}", recipe_screens(week_label, date, rid), f"d{i}_{m}")
    paged("s0", shopping_screens(week_label, sections, staples), "w7")
    return pages, nav


# ---------------------------------------------------------------- rendering

def to_gray(png):
    """Shrink a screenshot to 8-bit grayscale with whichever converter this machine has."""
    if shutil.which("sips"):
        cmd = ["sips", "-m", GRAY_PROFILE, str(png), "--out", str(png)]
    elif shutil.which("convert"):
        cmd = ["convert", str(png), "-colorspace", "Gray", "-strip", "-define", "png:color-type=0",
               "-define", "png:bit-depth=8", str(png)]
    else:
        return  # the Kindle's drawing tool also accepts colour PNGs, just larger
    subprocess.run(cmd, capture_output=True, check=True)


def render(pages, out):
    html_dir, png_dir = out / "html", out / "pages"
    html_dir.mkdir(parents=True)
    png_dir.mkdir()
    with tempfile.TemporaryDirectory() as profile:
        for state, markup in pages.items():
            src, shot = html_dir / f"{state}.html", png_dir / f"{state}.png"
            src.write_text(markup)
            result = subprocess.run(
                [CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", *SANDBOX,
                 "--force-device-scale-factor=1", f"--user-data-dir={profile}",
                 "--window-size=600,800", f"--screenshot={shot}", src.as_uri()],
                capture_output=True, text=True)
            if not shot.exists():
                sys.exit(f"Chrome did not render {state}:\n{result.stderr[-500:]}")
            to_gray(shot)


def package(dist):
    """Bundle the app data for the Kindle to download, plus the files it checks first."""
    site = ROOT / "dist" / "site"
    if site.exists():
        shutil.rmtree(site)
    site.mkdir(parents=True)
    bundle = site / "mealplan.tar.gz"
    with tarfile.open(bundle, "w:gz") as tar:
        for name in ("pages", "nav.txt", "week.txt"):
            tar.add(dist / name, arcname=name)
    digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    (site / "mealplan.tar.gz.sha256").write_text(digest + "\n")
    shutil.copyfile(dist / "week.txt", site / "week.txt")
    shutil.copyfile(dist / "pages" / "w0.png", site / "preview.png")
    (site / "index.html").write_text(
        "<!DOCTYPE html><title>Meal plan</title>"
        f"<p>Week of {(dist / 'week.txt').read_text().strip()}</p>"
        '<img src="preview.png" alt="Week overview" width="600" height="800">\n')
    return site


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--week", help="Monday the week starts on (YYYY-MM-DD)")
    args = parser.parse_args()
    today = dt.date.today()
    week_start = (dt.date.fromisoformat(args.week) if args.week
                  else today - dt.timedelta(days=today.weekday()))
    key = week_start.isoformat()
    history = g.load_history()
    if key not in history["weeks"]:
        sys.exit(f"No plan for {key} yet. Run generate.py first.")
    days = history["weeks"][key]

    if not CHROME:
        sys.exit("Chrome not found. Set CHROME to its path.")
    out = ROOT / "build" / key / "app"
    if out.exists():
        shutil.rmtree(out)
    pages, nav = build_screens(week_start, days)
    render(pages, out)
    (out / "nav.txt").write_text("".join(" ".join(row) + "\n" for row in nav))
    (out / "week.txt").write_text(key + "\n")

    dist = ROOT / "dist" / "app"
    if dist.exists():
        shutil.rmtree(dist)
    dist.mkdir(parents=True)
    shutil.copytree(out / "pages", dist / "pages")
    for name in ("nav.txt", "week.txt"):
        shutil.copyfile(out / name, dist / name)
    print(f"{len(pages)} screens rendered to {dist}")
    print(f"Download bundle for the Kindle in {package(dist)}")


if __name__ == "__main__":
    main()
