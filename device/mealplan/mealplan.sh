#!/mnt/us/usbnet/bin/busybox ash
# Meal Plan app for the Kindle 4 (non-touch).
#
# Shows the pre-rendered 600x800 pages in pages/ and moves between them by
# following nav.txt, whose rows are:  state up down select back left right
#
# While it runs, the Kindle's own interface (cvm) is paused and the buttons are
# grabbed, so nothing behind the app reacts to key presses. Home, Menu, or Back
# from the week page exits. Needs USBNetwork's tools in /mnt/us/usbnet/bin.
#
# Usage: mealplan.sh [seconds to wait before drawing, default 3]

BASE=/mnt/us/mealplan
BIN=/mnt/us/usbnet/bin
FIFO=/tmp/mealplan.fifo
PIDFILE=/tmp/mealplan.pid
FLASH_EVERY=5   # partial refreshes before a full one clears the e-ink ghosting

# Key codes measured on this Kindle with evtest.
K_UP=103; K_DOWN=108; K_LEFT=105; K_RIGHT=106; K_SELECT=194
K_BACK=158; K_MENU=139; K_HOME=102
K_PGFWD_A=191; K_PGBACK_A=109
K_PGFWD_B=104; K_PGBACK_B=193   # all four confirmed on the device

if [ -e "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    exit 0
fi
echo $$ > "$PIDFILE"

cleanup() {
    trap - EXIT INT TERM HUP
    kill "$EV0" "$EV1" 2>/dev/null
    killall -CONT cvm 2>/dev/null
    rm -f "$FIFO" "$PIDFILE"
    # The interface was paused, so nudge it into repainting its own screen.
    # Not when the Kindle is going to sleep: it draws its screensaver itself.
    if [ -z "$QUIET" ]; then
        sleep 1
        echo "send $K_HOME" > /proc/keypad 2>/dev/null
    fi
}
trap 'cleanup; exit 0' EXIT INT HUP
trap 'QUIET=1; cleanup; exit 0' TERM   # sent by watch.sh when the Kindle goes to sleep

# Partial refreshes are quick but leave faint ghosts on e-ink, so flash the whole
# screen when the page type changes and after every few partial ones.
partials=0
show() {
    kind=${1%%[0-9]*}
    flash=""
    if [ "$kind" != "$last_kind" ] || [ "$partials" -ge "$FLASH_EVERY" ]; then
        flash="-f"
        partials=0
    else
        partials=$((partials + 1))
    fi
    last_kind=$kind
    # A raw framebuffer copy draws in about 25 ms; decoding the PNG takes about 120 ms.
    if [ -e "$BASE/pages/$1.raw" ]; then
        "$BIN/busybox" cat "$BASE/pages/$1.raw" > /dev/fb0 &&
            "$BIN/fbink" -q $flash -s > /dev/null 2>&1 && return
    fi
    "$BIN/fbink" -q $flash -g file="$BASE/pages/$1.png" > /dev/null 2>&1
}

# nav.txt is held in memory as one variable per state (nav_w0, nav_d2_1, ...), so
# a key press costs no extra processes. bundle.sha256 is the last file update.sh
# writes, so a change there means new screens have arrived.
load_nav() {
    while read -r name rest; do
        case "$name" in
            "" | *[!a-z0-9_]*) continue ;;
        esac
        eval "nav_$name=\$rest"
    done < "$BASE/nav.txt"
    loaded=
    [ -r "$BASE/bundle.sha256" ] && read -r loaded < "$BASE/bundle.sha256"
}

# Start on today's row (plans run Monday to Sunday).
state=w0
weekday=$(date +%u 2>/dev/null)
case "$weekday" in
    [1-7]) state=w$((weekday - 1)) ;;
esac
load_nav
shown=$loaded
awake=0

sleep "${1:-3}"               # let KUAL close and the home screen finish drawing
killall -STOP cvm 2>/dev/null

rm -f "$FIFO"
mkfifo "$FIFO"
exec 3<> "$FIFO"
"$BIN/evtest" --grab /dev/input/event0 > "$FIFO" 2>/dev/null &
EV0=$!
"$BIN/evtest" --grab /dev/input/event1 > "$FIFO" 2>/dev/null &
EV1=$!

show "$state"

# No idle timeout: the app stays up until the Kindle sleeps (watch.sh closes it) or Home.
while read -r line <&3; do
    case "$line" in
        *"EV_KEY"*"value 1") ;;
        *) continue ;;
    esac
    code=${line#*code }
    code=${code%% *}
    current=
    [ -r "$BASE/bundle.sha256" ] && read -r current < "$BASE/bundle.sha256"
    [ "$current" != "$loaded" ] && load_nav   # new screens arrived while the app was open
    eval "row=\$nav_$state"
    if [ "$current" != "$shown" ] || [ -z "$row" ]; then   # this page is from the old set
        shown=$current
        state=w0
        show "$state"
        continue
    fi
    set -- "$state" $row   # the stored row leaves out its own name
    next=-
    case "$code" in
        "$K_UP" | "$K_PGBACK_A" | "$K_PGBACK_B") next=$2 ;;
        "$K_DOWN" | "$K_PGFWD_A" | "$K_PGFWD_B") next=$3 ;;
        "$K_SELECT") next=$4 ;;
        "$K_BACK") next=$5; [ "$next" = "-" ] && break ;;
        "$K_LEFT") next=$6 ;;
        "$K_RIGHT") next=$7 ;;
        "$K_HOME" | "$K_MENU") break ;;
    esac
    if [ -n "$next" ] && [ "$next" != "-" ] && [ "$next" != "$state" ]; then
        state=$next
        show "$state"
    fi

    # The grabbed buttons do not reach the power service, so reset its screensaver
    # timer by hand. (preventScreenSaver is no good: it also disables the power switch.)
    # After the draw and at most every 30 seconds, so it never competes with a page turn.
    read -r now _ < /proc/uptime
    now=${now%%.*}
    if [ "$((now - awake))" -ge 30 ]; then
        awake=$now
        lipc-set-prop com.lab126.powerd touchScreenSaverTimeout 1 2>/dev/null &
    fi
done
