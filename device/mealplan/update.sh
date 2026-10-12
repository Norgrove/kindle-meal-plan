#!/mnt/us/usbnet/bin/busybox ash
# Fetch the published screens when they differ from the ones installed.
# The address to check lives in url.txt. Does nothing when offline.

BASE=/mnt/us/mealplan
BIN=/mnt/us/usbnet/bin
TMP=$BASE/incoming
LOG=$BASE/update.log
LOCK=/tmp/mealplan-update.lock

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"; }
fail() { log "$*"; rm -rf "$TMP"; exit 1; }

URL=$(cat "$BASE/url.txt" 2>/dev/null)
[ -n "$URL" ] || exit 0
[ -e "$LOCK" ] && exit 0
touch "$LOCK"
trap 'rm -f "$LOCK"' EXIT

# The published checksum doubles as the version: fetch only when it changes.
want=$("$BIN/curl" -fsS -m 30 --retry 3 --retry-delay 10 "$URL/mealplan.tar.gz.sha256" 2>/dev/null) || exit 0
[ "${#want}" -eq 64 ] || fail "unexpected checksum file from $URL"
[ "$want" = "$(cat "$BASE/bundle.sha256" 2>/dev/null)" ] && exit 0

rm -rf "$TMP"
mkdir -p "$TMP"
"$BIN/curl" -fsS -m 300 -o "$TMP/mealplan.tar.gz" "$URL/mealplan.tar.gz" || fail "download failed"
got=$("$BIN/openssl" dgst -sha256 "$TMP/mealplan.tar.gz" | sed 's/.*= //')
[ "$want" = "$got" ] || fail "checksum mismatch"

# -o: do not try to restore file owners, which the FAT user partition cannot store
( cd "$TMP" && "$BIN/bsdtar" -x -z -o -f mealplan.tar.gz 2>/dev/null ) || fail "could not unpack"
[ -s "$TMP/nav.txt" ] && [ -s "$TMP/pages/w0.png" ] || fail "bundle is incomplete"

# Swap the new week in, keeping the previous week's screens as pages.old.
rm -rf "$BASE/pages.old"
mv "$BASE/pages" "$BASE/pages.old"
mv "$TMP/pages" "$BASE/pages"
cp "$TMP/nav.txt" "$BASE/nav.txt"
cp "$TMP/week.txt" "$BASE/week.txt"
rm -rf "$TMP"
sync
echo "$want" > "$BASE/bundle.sha256"
sync
log "updated to week $(cat "$BASE/week.txt")"
