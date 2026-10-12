#!/mnt/us/usbnet/bin/busybox ash
# Check for a new week shortly after start-up, then each time the Kindle wakes
# from its screensaver. Started at boot by /etc/init.d/mealplan.

BASE=/mnt/us/mealplan
BIN=/mnt/us/usbnet/bin
PIDFILE=/tmp/mealplan-watch.pid

if [ -e "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    exit 0
fi
echo $$ > "$PIDFILE"

check() {
    sleep "$1"   # give Wi-Fi time to reconnect
    "$BIN/busybox" ash "$BASE/update.sh"
}

check 60
lipc-wait-event -m com.lab126.powerd outOfScreenSaver 2>/dev/null | while read -r _; do
    check 25
done
