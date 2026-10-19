#!/usr/bin/env python3
"""Plan a week of meals.

    python3 generate.py                  # the week starting this Monday
    python3 generate.py --week 2026-10-19
    python3 generate.py --force          # re-roll a week that was already planned

Every rule lives in rules.toml. This file plans a week to those rules and
refuses to save one that breaks them. history.json records every week, so the
next one rotates to the recipes used least recently, and remembers which
lunch batch to make on Sunday for the week after. app.py turns the plan into
screens for the Kindle.
"""
import argparse
import datetime as dt
import itertools
import json
import math
import random
import re
import sys
import tomllib
from pathlib import Path

from recipes import BY_ID, OPTIONAL, PACKS, PLURALS, SECTIONS, SHOP_NAMES, STAPLES, UNITS

ROOT = Path(__file__).resolve().parent
HISTORY = ROOT / "history.json"
RULES = tomllib.loads((ROOT / "rules.toml").read_text())

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
TARGET = RULES["protein"]["target_line"]
NEVER = 999
TRIES = 2000   # planning attempts before giving up on a week

NOT_METRIC = re.compile(
    r"\b(tbsp|tsp|teaspoons?|tablespoons?|cups?|oz|ounces?|lbs?|pounds?|inch(es)?|pints?|fahrenheit)\b|°F",
    re.I)
OVEN_TEMP = re.compile(r"(\d+)°C( \((\d+)°C fan\))?")
YOGHURT = re.compile(r"yog(h)?urt", re.I)
SECTION_OF = {item: section for section, items in SECTIONS.items() for item in items}


def words(terms):
    """Match any of these as whole words, allowing a plural ending."""
    return re.compile(r"\b(" + "|".join(re.escape(t) for t in terms) + r")(e?s)?\b", re.I)


BANNED = words(RULES["ingredients"]["never"])
MEAT = words(RULES["ingredients"]["not_for_me"])
ACID = words(RULES["flavour"]["acid"])
SPARING = words(RULES["ingredients"]["sparingly"])
NEEDS_SWAP = words(RULES["people"]["husband_swap_needed_for"])


# ---------------------------------------------------------------- recipes

def slot_of(rid):
    return BY_ID[rid]["slot"]


def servings(slot):
    return RULES[slot.lower()]["serves"] if slot != "Snack" else 1


def minutes(rid):
    return BY_ID[rid]["prep"] + BY_ID[rid]["cook"]


def protein(day):
    return sum(BY_ID[r]["protein"] for r in day)


def text_of(recipe):
    """Everything written in a recipe, for the word checks."""
    return " ".join([recipe["name"], *recipe["steps"], *(i[2] for i in recipe["ing"]), recipe["husband"] or ""])


def has_swap_item(recipe):
    return any(NEEDS_SWAP.search(item) for _, _, item, _ in recipe["ing"])


def uses_sparing(rid):
    return any(SPARING.search(item) for _, _, item, _ in BY_ID[rid]["ing"])


def day_label(date):
    return f"{date:%a} {date.day} {date:%b}"


def ids(slot, group=None):
    return [rid for rid, r in BY_ID.items() if r["slot"] == slot and (group is None or r["group"] == group)]


def sandwich_id():
    return next(rid for rid in ids("Lunch", "sandwich") if BY_ID[rid]["name"] == RULES["lunch"]["sandwich"])


def shake_id():
    return ids("Snack", "shake")[0]


# ---------------------------------------------------------------- lunches

def batches():
    return RULES["lunch"]["batches"]


def made_week_before(batch):
    """A batch made on a day after the ones it covers (Sunday for Mon-Wed) is made the week before."""
    return DAYS.index(batch["made"]) > DAYS.index(batch["covers"][0])


def lunch_layout():
    """For each day, the name of the batch that covers it, or None for the sandwich."""
    office = set(RULES["lunch"]["office_days"])
    layout = [None] * 7
    for batch in batches():
        for day in batch["covers"]:
            if day not in office:
                layout[DAYS.index(day)] = batch["name"]
    return layout


def batch_days(name):
    return [DAYS[i] for i, b in enumerate(lunch_layout()) if b == name]


# ---------------------------------------------------------------- history

def load_history():
    history = json.loads(HISTORY.read_text()) if HISTORY.exists() else {}
    history.setdefault("weeks", {})
    history.setdefault("ahead", {})   # week -> {batch name: lunch} chosen the week before
    return history


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


def recent_dinners(history, week_start):
    recent = set()
    for back in range(1, RULES["dinner"]["no_repeat_weeks"] + 1):
        key = (week_start - dt.timedelta(weeks=back)).isoformat()
        for day in history["weeks"].get(key, []):
            recent.update(r for r in day if r in BY_ID and slot_of(r) == "Dinner")
    return recent


def bought_ahead(history, week_start):
    """Lunch batches for this week that were chosen, and shopped for, the week before."""
    preset = history["ahead"].get(week_start.isoformat(), {})
    return {name: rid for name, rid in preset.items() if rid in BY_ID and batch_days(name)}


def next_week_batches(history, week_start, days):
    """Pick the batches to make at the end of this week for next week, and remember them."""
    nxt = (week_start + dt.timedelta(weeks=1)).isoformat()
    if nxt in history["weeks"]:      # already planned: its batches are settled
        return history["ahead"].get(nxt, {})
    ages = recipe_ages(history, week_start)
    this_week = {r for day in days for r in day}
    chosen = {}
    for batch in batches():
        if made_week_before(batch) and batch_days(batch["name"]):
            pool = [r for r in ids("Lunch", "batch") if r not in this_week and r not in chosen.values()]
            pool.sort(key=lambda r: (-ages.get(r, NEVER), r))
            chosen[batch["name"]] = pool[0]
    history["ahead"][nxt] = chosen
    return chosen


# ---------------------------------------------------------------- planning

def plan_week(week_start, history, rng):
    ages = recipe_ages(history, week_start)
    preset = bought_ahead(history, week_start)
    problems = []
    for n in range(TRIES):
        days = attempt(week_start, history, ages, preset, rng, looseness=n / 100)
        if days is None:
            continue
        problems = week_problems(days, history, week_start)
        if not problems:
            return days
    sys.exit("Could not plan a week that meets rules.toml. Last attempt:\n  " + "\n  ".join(
        problems or ["not enough dinners in recipes.py for the dinner rules"]))


def attempt(week_start, history, ages, preset, rng, looseness=0.0):
    """One randomised try at a week. Returns None if the dinner rules cannot be met this way.
    The least recently used recipes win, with more randomness the more tries it takes."""
    rotation = week_start.toordinal() // 7
    fresh = lambda rid: (-min(ages.get(rid, NEVER), 52) + rng.random() * looseness, rng.random())

    # Lunches: one recipe per batch, the sandwich for everything else.
    chosen = {}
    for batch in batches():
        name = batch["name"]
        if not batch_days(name):
            continue
        if name in preset:
            chosen[name] = preset[name]
        else:
            pool = [r for r in ids("Lunch", "batch") if r not in chosen.values()]
            chosen[name] = min(pool, key=fresh)
    lunch = [chosen[b] if b else sandwich_id() for b in lunch_layout()]

    # Dinners: the protein mix in a random order, quick ones on the quick days.
    rules = RULES["dinner"]
    kinds = [kind for kind, n in rules["nights"].items() for _ in range(n)]
    rng.shuffle(kinds)
    recent, dinner = recent_dinners(history, week_start), []
    for i, kind in enumerate(kinds):
        quick = DAYS[i] in rules["quick_days"]
        cuisines = [BY_ID[r]["cuisine"] for r in dinner]
        spicy = sum("very spicy" in BY_ID[r]["tags"] for r in dinner)
        sparing = sum(uses_sparing(r) for r in dinner)
        pool = [r for r in ids("Dinner", kind)
                if r not in recent and r not in dinner and fits_night(r, quick)
                and cuisines.count(BY_ID[r]["cuisine"]) < rules["max_same_cuisine"]
                and spicy + ("very spicy" in BY_ID[r]["tags"]) <= rules["very_spicy_max"]
                and sparing + uses_sparing(r) <= RULES["ingredients"]["sparingly_max"]]
        if not pool:
            return None
        dinner.append(min(pool, key=fresh))

    # Breakfasts: weekday types in rotation, weekend types at the weekend. Chosen last,
    # so a day that would overshoot the protein aim gets a lighter one of the right type.
    high = RULES["protein"]["aim"][1]
    breakfast, used = [], set()
    for i in range(7):
        types = RULES["breakfast"]["weekday_types" if i < 5 else "weekend_types"]
        rest = BY_ID[lunch[i]]["protein"] + BY_ID[dinner[i]]["protein"]
        if DAYS[i] in RULES["shakes"]["training_days"]:
            rest += BY_ID[shake_id()]["protein"]
        pool = ids("Breakfast", types[(rotation + i) % len(types)])
        rid = min(pool, key=lambda r: (r in used, max(0, rest + BY_ID[r]["protein"] - high), *fresh(r)))
        breakfast.append(rid)
        used.add(rid)

    # Snacks: the shake on training days, then top up towards the aim.
    days, uses = [], {r: 0 for r in ids("Snack", "snack")}
    for i in range(7):
        day = [breakfast[i], lunch[i], dinner[i]]
        if DAYS[i] in RULES["shakes"]["training_days"]:
            day.append(shake_id())
        day += top_up(day, uses, rng)
        days.append(day)
    return days


def fits_night(rid, quick):
    rules = RULES["dinner"]
    low, high = rules["other_minutes"]
    return minutes(rid) <= rules["quick_max_minutes"] if quick else low <= minutes(rid) <= high


def top_up(day, uses, rng):
    """The fewest snacks that bring the day into the protein aim, spread across the week."""
    low, high = RULES["protein"]["aim"]
    total = protein(day)
    if total >= low:
        return []
    snacks = list(uses)
    combos = [(s,) for s in snacks] + list(itertools.combinations(snacks, 2))

    def score(combo):
        t = total + sum(BY_ID[s]["protein"] for s in combo)
        return (not low <= t <= high, t < low, len(combo), sum(uses[s] for s in combo),
                abs(t - (low + high) / 2), rng.random())

    best = min(combos, key=score)
    for s in best:
        uses[s] += 1
    return list(best)


# ---------------------------------------------------------------- checking

def check_recipes():
    """Problems with the recipe pool itself, against rules.toml."""
    problems = []
    rules, yoghurt = RULES, RULES["ingredients"]["yoghurt"]
    low, high = rules["protein"]["per_meal"]
    for rid, r in BY_ID.items():
        text = text_of(r)
        found = BANNED.search(text)
        if found:
            problems.append(f"{rid}: banned ingredient {found.group(0)!r}")
        found = NOT_METRIC.search(text)
        if found:
            problems.append(f"{rid}: non-metric unit {found.group(0)!r}")
        for m in OVEN_TEMP.finditer(" ".join(r["steps"])):
            if not m.group(3) or int(m.group(3)) != int(m.group(1)) - 20:
                problems.append(f"{rid}: write oven temperatures as 220°C (200°C fan), not {m.group(0)!r}")
        if not r["steps"]:
            problems.append(f"{rid}: no method")
        if r["slot"] != "Snack" and not low <= r["protein"] <= high:
            problems.append(f"{rid}: {r['protein']} g protein, outside {low}-{high} g a meal")
        if has_swap_item(r) and not r["husband"]:
            problems.append(f"{rid}: has eggs but no Husband: swap")
        for qty, unit, item, flag in r["ing"]:
            if unit not in UNITS or qty <= 0:
                problems.append(f"{rid}: bad quantity {qty!r} {unit!r} for {item}")
            if item not in SECTION_OF and item not in STAPLES:
                problems.append(f"{rid}: {item!r} has no shopping section")
            if YOGHURT.search(item) and item != yoghurt:
                problems.append(f"{rid}: yoghurt must be {yoghurt}, not {item!r}")
            if MEAT.search(item):
                problems.append(f"{rid}: {item!r} is not pescetarian (meat only goes in a Husband: swap)")
            if SPARING.search(item) and flag != OPTIONAL:
                problems.append(f"{rid}: {item} must be an optional extra")
            if item == rules["shakes"]["powder"] and r["group"] != "shake":
                problems.append(f"{rid}: protein powder only goes in shakes")
        if r["slot"] in ("Lunch", "Dinner") and not any(ACID.search(i[2]) for i in r["ing"]):
            problems.append(f"{rid}: nothing acidic to finish it")
        if r["slot"] == "Breakfast":
            types = rules["breakfast"]["weekday_types"] + rules["breakfast"]["weekend_types"]
            if r["group"] not in types:
                problems.append(f"{rid}: breakfast type {r['group']!r} is not in rules.toml")
        if r["slot"] == "Lunch":
            if minutes(rid) > rules["lunch"]["max_minutes"]:
                problems.append(f"{rid}: lunch takes {minutes(rid)} min, over {rules['lunch']['max_minutes']}")
            if rules["lunch"]["no_oven"] and re.search(r"\boven\b", text, re.I):
                problems.append(f"{rid}: lunches must not need the oven")
            if any(i[2] == "rice" for i in r["ing"]):
                problems.append(f"{rid}: lunches use a {rules['lunch']['rice']}, never cooked-ahead rice")
        if r["slot"] == "Dinner":
            if r["cuisine"] not in rules["dinner"]["cuisines"]:
                problems.append(f"{rid}: cuisine {r['cuisine']!r} is not in rules.toml")
            if r["group"] not in rules["dinner"]["nights"]:
                problems.append(f"{rid}: dinner protein {r['group']!r} is not in rules.toml")
            if not (fits_night(rid, True) or fits_night(rid, False)):
                problems.append(f"{rid}: {minutes(rid)} min suits neither a quick nor a normal night")
    if RULES["lunch"]["sandwich"] not in (BY_ID[r]["name"] for r in ids("Lunch", "sandwich")):
        problems.append(f"no lunch called {RULES['lunch']['sandwich']!r}")
    return problems


def week_problems(days, history, week_start):
    """Everything wrong with a planned week, against rules.toml."""
    rules, problems = RULES, []
    unknown = sorted({r for day in days for r in day if r not in BY_ID})
    if unknown:
        return [f"recipes no longer in recipes.py: {', '.join(unknown)}"]
    shape = [[slot_of(r) for r in day][:3] for day in days]
    if len(days) != 7 or any(s != ["Breakfast", "Lunch", "Dinner"] for s in shape):
        return ["each of the 7 days must start with a breakfast, a lunch and a dinner"]
    used = sorted({r for day in days for r in day})
    dinners = [day[2] for day in days]

    # The checks asked for by name.
    for i, day in enumerate(days):
        if protein(day) < rules["protein"]["floor"]:
            problems.append(f"{DAYS[i]} has {protein(day)} g protein, under {rules['protein']['floor']} g")
    for rid in used:
        text = text_of(BY_ID[rid])
        found = BANNED.search(text)
        if found:
            problems.append(f"{rid}: banned ingredient {found.group(0)!r}")
        if re.search(r"\bskyr\b", text, re.I):
            problems.append(f"{rid}: skyr")
        if has_swap_item(BY_ID[rid]) and not BY_ID[rid]["husband"]:
            problems.append(f"{rid}: has eggs but no Husband: swap")
    for i, rid in enumerate(dinners):
        if DAYS[i] in rules["dinner"]["quick_days"] and minutes(rid) > rules["dinner"]["quick_max_minutes"]:
            problems.append(f"{DAYS[i]} dinner takes {minutes(rid)} min, over {rules['dinner']['quick_max_minutes']}")

    # The rest of rules.toml.
    low, high = rules["dinner"]["other_minutes"]
    for i, rid in enumerate(dinners):
        if DAYS[i] not in rules["dinner"]["quick_days"] and not low <= minutes(rid) <= high:
            problems.append(f"{DAYS[i]} dinner takes {minutes(rid)} min, outside {low}-{high}")
    for kind, n in rules["dinner"]["nights"].items():
        got = sum(BY_ID[r]["group"] == kind for r in dinners)
        if got != n:
            problems.append(f"{got} {kind} dinners, expected {n}")
    if len(set(dinners)) < 7:
        problems.append("a dinner repeats within the week")
    repeats = sorted(set(dinners) & recent_dinners(history, week_start))
    if repeats:
        problems.append(f"dinners from the previous {rules['dinner']['no_repeat_weeks']} weeks: {', '.join(repeats)}")
    for cuisine in set(BY_ID[r]["cuisine"] for r in dinners):
        n = sum(BY_ID[r]["cuisine"] == cuisine for r in dinners)
        if n > rules["dinner"]["max_same_cuisine"]:
            problems.append(f"{n} {cuisine} dinners, more than {rules['dinner']['max_same_cuisine']}")
    if sum("very spicy" in BY_ID[r]["tags"] for r in dinners) > rules["dinner"]["very_spicy_max"]:
        problems.append("too many very spicy dinners")
    sparing = sum(uses_sparing(r) for day in days for r in day)
    if sparing > rules["ingredients"]["sparingly_max"]:
        problems.append(f"{sparing} meals offer feta or halloumi, more than {rules['ingredients']['sparingly_max']}")
    for i, day in enumerate(days):
        if DAYS[i] in rules["shakes"]["training_days"] and shake_id() not in day:
            problems.append(f"{DAYS[i]} is a training day without a shake")
        types = rules["breakfast"]["weekday_types" if i < 5 else "weekend_types"]
        if BY_ID[day[0]]["group"] not in types:
            problems.append(f"{DAYS[i]} breakfast is a {BY_ID[day[0]]['group']}, expected one of {types}")
    layout = lunch_layout()
    for name in set(b for b in layout if b):
        lunches = {days[i][1] for i, b in enumerate(layout) if b == name}
        if len(lunches) != 1 or BY_ID[lunches.pop()]["group"] != "batch":
            problems.append(f"batch {name} days do not share one batch lunch")
    for i, b in enumerate(layout):
        if b is None and days[i][1] != sandwich_id():
            problems.append(f"{DAYS[i]} should be the {rules['lunch']['sandwich']}")
    return problems


def validate(days, history, week_start):
    problems = check_recipes() + week_problems(days, history, week_start)
    if problems:
        sys.exit("Plan failed its checks against rules.toml:\n  " + "\n  ".join(problems))


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


def purchases(days, history, week_start):
    """(recipe, portions) to shop for this week: everything except lunch batches bought
    last week, plus the batches made on Sunday for next week."""
    preset = bought_ahead(history, week_start)
    skip = {(DAYS.index(d), rid) for name, rid in preset.items() for d in batch_days(name)}
    buy = [(rid, servings(slot_of(rid))) for i, day in enumerate(days) for rid in day if (i, rid) not in skip]
    nxt = history["ahead"].get((week_start + dt.timedelta(weeks=1)).isoformat(), {})
    buy += [(rid, len(batch_days(name))) for name, rid in nxt.items() if rid in BY_ID]
    return buy


def shopping_list(days, history, week_start):
    """Return ([(section, [(name, amount text)])], [staples in use])."""
    totals, staples = {}, set()
    for rid, portions in purchases(days, history, week_start):
        for qty, unit, item, flag in BY_ID[rid]["ing"]:
            if item in STAPLES:
                staples.add(item)
            else:
                key = (item, unit, flag)
                totals[key] = totals.get(key, 0) + qty * portions
    sections = []
    for section, items in SECTIONS.items():
        lines = []
        for item in items:
            for (name, unit, flag), qty in totals.items():
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
                lines.append((label + (" (optional)" if flag else ""), text))
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

    problems = check_recipes()
    if problems:
        sys.exit("The recipe pool breaks rules.toml:\n  " + "\n  ".join(problems))
    history = load_history()
    if key in history["weeks"] and not args.force:
        days = history["weeks"][key]
        validate(days, history, week_start)
    else:
        seed = key if not args.force else f"{key}-{random.random()}"
        days = plan_week(week_start, history, random.Random(seed))
        validate(days, history, week_start)
        history["weeks"][key] = days
    ahead = next_week_batches(history, week_start, days)
    HISTORY.write_text(json.dumps(history, indent=1) + "\n")

    low, high = RULES["protein"]["aim"]
    totals = [protein(d) for d in days]
    print(f"Week of {week_start.day} {week_start:%b}: protein per day {totals} (aim {low}-{high})")
    for i, day in enumerate(days):
        date = week_start + dt.timedelta(days=i)
        print(f"  {day_label(date):<11}", " | ".join(BY_ID[r]["name"] for r in day))
    for name, rid in ahead.items():
        print(f"  Batch {name} for next week: {BY_ID[rid]['name']}")
    outside = [f"{DAYS[i]} {t} g" for i, t in enumerate(totals) if not low <= t <= high]
    if outside:
        print("  Outside the aim (allowed; only the floor fails a week): " + ", ".join(outside))
    sections, staples = shopping_list(days, history, week_start)
    print(f"Shopping list: {sum(len(lines) for _, lines in sections)} items, {len(staples)} staples to check")


if __name__ == "__main__":
    main()
