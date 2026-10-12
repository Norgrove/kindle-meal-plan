#!/usr/bin/env python3
"""Render this week's plan as full-screen pages for the Kindle app.

    python3 app.py                 # the week starting this Monday
    python3 app.py --week 2026-10-19

Every screen the app can show (each page with each possible highlight) is
drawn as HTML, photographed at 600x800 by headless Chrome, and converted to a
grayscale PNG. nav.txt tells the on-device script which screen each button
leads to. Output goes to dist/app/, ready to copy to /mnt/us/mealplan.
"""
import argparse
import datetime as dt
import hashlib
import html
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

import generate as g
from recipes import BY_ID

ROOT = Path(__file__).resolve().parent
MAC_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CHROME = (os.environ.get("CHROME")
          or (MAC_CHROME if Path(MAC_CHROME).exists() else None)
          or shutil.which("google-chrome") or shutil.which("chromium")
          or shutil.which("chromium-browser"))
SANDBOX = ["--no-sandbox"] if os.environ.get("CI") else []  # CI runners cannot use the sandbox
GRAY_PROFILE = "/System/Library/ColorSync/Profiles/Generic Gray Gamma 2.2 Profile.icc"

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
.bar,.ft{display:flex;justify-content:space-between;font:19px/1 -apple-system,"Helvetica Neue","Liberation Sans","DejaVu Sans",sans-serif;padding:10px 20px}
.bar{border-bottom:3px solid #000}.ft{border-top:3px solid #000;font-size:17px}
.body{flex:1;overflow:hidden;padding:16px 24px 0;font-size:26px;line-height:1.3}
h1{font:400 40px/1.1 Impact,Anton,"Arial Black",sans-serif;letter-spacing:1px;margin:0 0 6px}
h2{font-size:24px;margin:12px 0 2px;border-bottom:2px solid #000}
.sub{font-size:20px;color:#333;margin:0 0 12px}
.lnk{display:flex;gap:16px;align-items:center;padding:7px 12px;margin:0 -12px;border-bottom:2px dotted #666}
.lnk small{display:block;font-size:19px;line-height:1.25}
.wk .lnk{padding:3px 12px;font-size:24px;line-height:1.2}.wk .lnk small{font-size:18px;line-height:1.2}
.lnk.on{background:#000;color:#fff}.lnk.on svg{filter:invert(1)}
.lnk .g{margin-left:auto;font:22px -apple-system,sans-serif;white-space:nowrap}
.ic{width:84px;height:50px;flex:none}.big{width:160px;height:96px;display:block;margin:0 auto}
.grid{position:relative;display:flex;align-items:flex-end;height:136px;margin:6px 0 8px;border-bottom:3px solid #000}
.grid div{flex:1;text-align:center;position:relative}
.grid i{display:flex;align-items:flex-end;justify-content:center;background:#000;margin:0 12px;color:#fff;font:normal 18px/24px -apple-system,sans-serif}
.grid b{display:block;font:20px/26px -apple-system,sans-serif}
.grid u{position:absolute;left:0;right:0;border-top:3px dashed #000;mix-blend-mode:difference;border-color:#fff}
.seg{position:relative;display:flex;height:30px;border:3px solid #000;margin:12px 0}
.seg i{display:block;height:100%;border-right:2px solid #fff}
.seg u{position:absolute;top:-9px;bottom:-9px;width:0;border-left:4px dashed #000;outline:1px solid #fff}
table{width:100%;border-collapse:collapse;font-size:24px}
td{padding:4px 6px;border-bottom:2px dotted #666;vertical-align:top}
td:first-child{width:30%;font-weight:bold;white-space:nowrap}
p{margin:6px 0}
"""

DEFS = ('<svg width="0" height="0" style="position:absolute"><defs>'
        '<pattern id="dt" width="4" height="4" patternUnits="userSpaceOnUse">'
        '<rect width="4" height="4" fill="#fff"/><rect width="2" height="2" fill="#444"/>'
        '<rect x="2" y="2" width="2" height="2" fill="#444"/></pattern></defs></svg>')


def icon(kind, big=False):
    return f'<svg class="{"big" if big else "ic"}" viewBox="0 0 100 60">{ICONS[kind]}</svg>'


def screen(week_label, crumb, hint, body):
    return (f'<!DOCTYPE html><html><head><meta charset="utf-8"><style>{CSS}</style></head><body>'
            f'{DEFS}<div class="scr"><div class="bar"><span>Meal Plan</span>'
            f'<span>Week of {week_label}</span></div><div class="body">{body}</div>'
            f'<div class="ft"><span>{html.escape(crumb)}</span><span>{hint}</span></div></div></body></html>')


def week_screen(week_label, dates, days, totals, selected):
    esc = html.escape
    scale = 110 / max(100, max(totals))
    bars = "".join(
        f'<div><i style="height:{t * scale}px">{t}</i><b>{"MTWTFSS"[i]}</b></div>'
        for i, t in enumerate(totals))
    rows = ""
    for i, (date, day) in enumerate(zip(dates, days)):
        dinner = next(r for r in day if g.slot_of(r) == "Dinner")
        rows += (f'<div class="lnk{" on" if i == selected else ""}"><span>{g.day_label(date)}'
                 f'<small>{esc(BY_ID[dinner][1])}</small></span><span class="g">{totals[i]} g</span></div>')
    body = ("<h1>This week's plan</h1>"
            f'<p class="sub">About {round(sum(totals) / 7)} g protein a day. '
            f"Dashed line is your {g.TARGET} g target.</p>"
            f'<div class="grid">{bars}<u style="bottom:{g.TARGET * scale + 26}px"></u></div>'
            f'<div class="wk">{rows}</div>')
    return screen(week_label, f"Week of {week_label}", "▲▼ move · ● open · Home exit", body)


def day_bar(day):
    shades = ["#000", "#555", "#999", "#ccc", "#777", "#bbb"]
    full = max(sum(BY_ID[r][2] for r in day), 100)
    segs = "".join(f'<i style="width:{BY_ID[r][2] / full * 100}%;background:{shades[m % 6]}"></i>'
                   for m, r in enumerate(day))
    return f'<div class="seg">{segs}<u style="left:{g.TARGET / full * 100}%"></u></div>'


def day_screen(week_label, date, day, total, selected):
    esc = html.escape
    rows = "".join(
        f'<div class="lnk{" on" if m == selected else ""}">{icon(BY_ID[r][3])}'
        f'<span>{g.slot_of(r)}: {esc(BY_ID[r][1])}<small>{BY_ID[r][2]} g protein</small></span></div>'
        for m, r in enumerate(day))
    body = (f"<h1>{g.day_label(date)}</h1>{day_bar(day)}"
            f'<p class="sub">{total} g protein. Dashed line marks {g.TARGET} g.</p>{rows}')
    return screen(week_label, g.day_label(date), "▲▼ move · ● open · ◀▶ day · Back", body)


def recipe_screen(week_label, date, rid):
    esc = html.escape
    _, name, protein, kind, ingredients, method = BY_ID[rid]
    table = "".join("<tr><td>{}</td><td>{}</td></tr>".format(*map(esc, ing.split("|", 1)))
                    for ing in ingredients)
    body = (f"{icon(kind, big=True)}<h1>{esc(name)}</h1>"
            f'<p class="sub">{g.slot_of(rid)} · about {protein} g protein</p>'
            f"<h2>Ingredients</h2><table>{table}</table><h2>Method</h2><p>{esc(method)}</p>")
    return screen(week_label, f"{date:%a} · {g.slot_of(rid)}", "▲▼ other meals · Back", body)


def build_screens(week_start, days):
    """Return ({state: html}, [nav rows]). A nav row is: state up down select back left right."""
    dates = [week_start + dt.timedelta(days=i) for i in range(7)]
    totals = [sum(BY_ID[r][2] for r in day) for day in days]
    week_label = f"{week_start.day} {week_start:%b}"
    pages, nav = {}, []
    for i, (date, day) in enumerate(zip(dates, days)):
        pages[f"w{i}"] = week_screen(week_label, dates, days, totals, i)
        nav.append((f"w{i}", f"w{max(i - 1, 0)}", f"w{min(i + 1, 6)}", f"d{i}_0", "-", "-", "-"))
        last = len(day) - 1
        for m, rid in enumerate(day):
            up, down = max(m - 1, 0), min(m + 1, last)
            pages[f"d{i}_{m}"] = day_screen(week_label, date, day, totals[i], m)
            nav.append((f"d{i}_{m}", f"d{i}_{up}", f"d{i}_{down}", f"r{i}_{m}", f"w{i}",
                        f"d{max(i - 1, 0)}_0", f"d{min(i + 1, 6)}_0"))
            pages[f"r{i}_{m}"] = recipe_screen(week_label, date, rid)
            nav.append((f"r{i}_{m}", f"r{i}_{up}", f"r{i}_{down}", f"d{i}_{m}", f"d{i}_{m}", "-", "-"))
    return pages, nav


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
