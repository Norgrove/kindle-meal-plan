#!/mnt/us/usbnet/bin/busybox ash
# Background helper, started at boot by /etc/init.d/mealplan and by the KUAL entry.
#  - checks for a new week shortly after start-up and after each wake-up
#  - opens the app on wake-up when autostart.txt says "on"
#  - closes the app when the Kindle goes to sleep, so it can sleep normally

BASE=/mnt/us/mealplan
BIN=/mnt/us/usbnet/bin
PIDFILE=/tmp/mealplan-watch.pid
APP_PID=/tmp/mealplan.pid

if [ -e "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    exit 0
fi
echo $$ > "$PIDFILE"

check() {
    sleep "$1"   # give Wi-Fi time to reconnect
    "$BIN/busybox" ash "$BASE/update.sh"
}

( check 60 ) &   # in the background, so sleep and wake are handled from the start
lipc-wait-event -m com.lab126.powerd goingToScreenSaver,outOfScreenSaver 2>/dev/null |
while read -r event _; do
    echo "$(date '+%H:%M:%S') $event" >> /tmp/mealplan-watch.log
    case "$event" in
        goingToScreenSaver)
            [ -e "$APP_PID" ] && kill -TERM "$(cat "$APP_PID")" 2>/dev/null
            ;;
        outOfScreenSaver)
            if [ "$(cat "$BASE/autostart.txt" 2>/dev/null)" = "on" ]; then
                "$BIN/busybox" ash "$BASE/mealplan.sh" 2 > /tmp/mealplan.log 2>&1 &
            fi
            ( check 25 ) &   # in the background: the next sleep must not wait for it
            ;;
    esac
done
