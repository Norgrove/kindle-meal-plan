#!/usr/bin/env python3
"""Plan a week of meals.

    python3 generate.py                  # the week starting this Monday
    python3 generate.py --week 2026-10-19
    python3 generate.py --force          # re-roll a week that was already planned

history.json records every week, so the next one rotates to the recipes used
least recently. app.py turns the plan into screens for the Kindle.
"""
import argparse
import datetime as dt
import json
import math
import random
import re
import sys
from pathlib import Path

from recipes import BY_ID, PACKS, PLURALS, POOLS, SECTIONS, SERVINGS, SHOP_NAMES, STAPLES, UNITS

ROOT = Path(__file__).resolve().parent
HISTORY = ROOT / "history.json"

TARGET = 90          # grams of protein a day, per person
SALMON_NIGHTS = 3
NEVER = 999

NOT_METRIC = re.compile(
    r"\b(tbsp|tsp|teaspoons?|tablespoons?|cups?|oz|ounces?|lbs?|pounds?|inch(es)?|pints?|fahrenheit)\b|°F",
    re.I)
BANNED = re.compile(
    r"avocado|protein bar|sweetener|honey|syrup|sugar"
    r"|skyr|cottage cheese|\bkale\b|cucumber|pak choi|chives|corn tortilla|sardine", re.I)
SECTION_OF = {item: section for section, items in SECTIONS.items() for item in items}


# ---------------------------------------------------------------- planning

def slot_of(rid):
    return {"b": "Breakfast", "l": "Lunch", "n": "Snack"}.get(rid[0], "Dinner")


def day_label(date):
    return f"{date:%a} {date.day} {date:%b}"


def protein(day):
    return sum(BY_ID[r]["protein"] for r in day)


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
        pool = [r["id"] for r in POOLS[pool_name]]
        pool.sort(key=lambda rid: (-ages.get(rid, NEVER), rng.random()))
        chosen = pool[:n]
        rng.shuffle(chosen)
        return chosen

    breakfasts, lunches = pick("Breakfast", 7), pick("Lunch", 7)
    salmon, other = pick("Salmon", SALMON_NIGHTS), pick("Dinner", 7 - SALMON_NIGHTS)
    salmon_days = set(rng.sample(range(7), SALMON_NIGHTS))

    snack_uses = {r["id"]: 0 for r in POOLS["Snack"]}
    days = []
    for i in range(7):
        dinner = salmon.pop() if i in salmon_days else other.pop()
        day = [breakfasts[i], lunches[i], dinner]
        while protein(day) < TARGET:
            need = TARGET - protein(day)
            left = [s for s in snack_uses if s not in day]
            enough = [s for s in left if BY_ID[s]["protein"] >= need]
            if enough:  # smallest snack that closes the gap, spread across the week
                snack = min(enough, key=lambda s: (snack_uses[s], BY_ID[s]["protein"], rng.random()))
            else:
                snack = max(left, key=lambda s: (BY_ID[s]["protein"], rng.random()))
            day.append(snack)
            snack_uses[snack] += 1
        days.append(day)
    return days


def check_recipes():
    """Problems with the recipe pool itself: units, shop sections, banned words."""
    problems = []
    for rid, recipe in BY_ID.items():
        text = " ".join([recipe["name"], *recipe["steps"], *(i[2] for i in recipe["ing"])])
        for pattern, label in ((NOT_METRIC, "non-metric unit"), (BANNED, "banned ingredient")):
            found = pattern.search(text)
            if found:
                problems.append(f"{rid}: {label} {found.group(0)!r}")
        if not recipe["steps"]:
            problems.append(f"{rid}: no method")
        for qty, unit, item in recipe["ing"]:
            if unit not in UNITS or qty <= 0:
                problems.append(f"{rid}: bad quantity {qty!r} {unit!r} for {item}")
            if item not in SECTION_OF and item not in STAPLES:
                problems.append(f"{rid}: {item!r} has no shopping section")
    return problems


def validate(days, previous):
    problems = check_recipes()
    salmon = sum(1 for day in days if any(r.startswith("s_") for r in day))
    if salmon != SALMON_NIGHTS:
        problems.append(f"{salmon} salmon nights, expected {SALMON_NIGHTS}")
    for i, day in enumerate(days):
        if protein(day) < TARGET:
            problems.append(f"day {i} has {protein(day)} g protein")
    if previous is not None and days == previous:
        problems.append("identical to the previous week")
    if problems:
        sys.exit("Plan failed its checks:\n  " + "\n  ".join(problems))


# ---------------------------------------------------------------- amounts

FRACTIONS = {0.25: "¼", 0.5: "½", 0.75: "¾"}


def amount(qty, unit):
    """A metric quantity as text: 400 g, 1.8 kg, 2 slices, 1½."""
    if unit in ("g", "ml"):
        big = {"g": "kg", "ml": "l"}[unit]
        if qty >= 1000:
            return f"{qty / 1000:.2f}".rstrip("0").rstrip(".") + f" {big}"
        return f"{qty:g} {unit}"
    whole, part = divmod(qty, 1)
    text = (str(int(whole)) if whole else "") + FRACTIONS.get(round(part, 2), "")
    if not text:
        text = f"{qty:g}"
    if unit:
        text += f" {unit}{'' if qty <= 1 else 's'}"
    return text


def item_name(item, qty, unit):
    """Plural for counted things: 2 apples, but 1 lemon and 40 g spinach."""
    return PLURALS.get(item, item) if unit == "" and qty > 1 else item


def shopping_list(days):
    """Return ([(section, [(name, amount text)])], [staples in use]) for SERVINGS people."""
    totals, staples = {}, set()
    for day in days:
        for rid in day:
            for qty, unit, item in BY_ID[rid]["ing"]:
                if item in STAPLES:
                    staples.add(item)
                else:
                    key = (item, unit)
                    totals[key] = totals.get(key, 0) + qty * SERVINGS
    sections = []
    for section, items in SECTIONS.items():
        lines = []
        for item in items:
            for (name, unit), qty in totals.items():
                if name != item:
                    continue
                if unit not in ("g", "ml"):
                    qty = math.ceil(qty)   # you cannot buy half a lemon
                text = amount(qty, unit)
                if item in PACKS:
                    size, pack = PACKS[item]
                    count = math.ceil(qty / size)
                    text += f" (about {count} {pack}{'' if count == 1 else 's'})"
                label = SHOP_NAMES.get(item, item[0].upper() + item[1:])
                lines.append((label, text))
        if lines:
            sections.append((section, lines))
    staples.add("salt and pepper")
    return sections, [s for s in STAPLES if s in staples]


# -------------------------------------------------------------------- main

def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--week", help="Monday the week starts on (YYYY-MM-DD)")
    parser.add_argument("--force", action="store_true", help="re-roll an existing week")
    args = parser.parse_args()

    today = dt.date.today()
    week_start = (dt.date.fromisoformat(args.week) if args.week
                  else today - dt.timedelta(days=today.weekday()))
    if week_start.weekday() != 0:
        sys.exit(f"{week_start} is not a Monday")
    key = week_start.isoformat()

    history = load_history()
    earlier = sorted(k for k in history["weeks"] if k < key)
    previous = history["weeks"][earlier[-1]] if earlier else None
    if key in history["weeks"] and not args.force:
        days = history["weeks"][key]
        validate(days, None)
    else:
        seed = key if not args.force else f"{key}-{random.random()}"
        days = plan_week(week_start, history, random.Random(seed))
        validate(days, previous)
        history["weeks"][key] = days
        HISTORY.write_text(json.dumps(history, indent=1) + "\n")

    print(f"Week of {week_start.day} {week_start:%b}: protein per day {[protein(d) for d in days]}")
    for i, day in enumerate(days):
        date = week_start + dt.timedelta(days=i)
        print(f"  {day_label(date):<11}", " | ".join(BY_ID[r]["name"] for r in day))
    sections, staples = shopping_list(days)
    print(f"Shopping list for {SERVINGS}: {sum(len(lines) for _, lines in sections)} items, "
          f"{len(staples)} staples to check")


if __name__ == "__main__":
    main()
