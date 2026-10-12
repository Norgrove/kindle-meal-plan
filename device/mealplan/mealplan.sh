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
IDLE=300   # seconds without a key press before handing the screen back

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

show() {
    kind=${1%%[0-9]*}
    flash=""
    [ "$kind" != "$last_kind" ] && flash="-f"   # full refresh when the page type changes
    last_kind=$kind
    "$BIN/fbink" -q $flash -g file="$BASE/pages/$1.png" > /dev/null 2>&1
}

# Start on today's row (plans run Monday to Sunday).
state=w0
weekday=$(date +%u 2>/dev/null)
case "$weekday" in
    [1-7]) state=w$((weekday - 1)) ;;
esac

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

while read -r -t "$IDLE" line <&3; do
    case "$line" in
        *"EV_KEY"*"value 1") ;;
        *) continue ;;
    esac
    code=${line#*code }
    code=${code%% *}
    # The grabbed buttons do not reach the power service, so reset its screensaver
    # timer by hand. (preventScreenSaver is no good: it also disables the power switch.)
    lipc-set-prop com.lab126.powerd touchScreenSaverTimeout 1 2>/dev/null &

    echo "$(date '+%H:%M:%S') key $code on $state" >> /tmp/mealplan-keys.log
    row=$(grep "^$state " "$BASE/nav.txt")
    if [ -z "$row" ]; then   # a new week arrived while this page was open
        state=w0
        show "$state"
        continue
    fi
    set -- $row
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
done
