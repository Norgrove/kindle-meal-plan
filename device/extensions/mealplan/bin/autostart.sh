#!/bin/sh
# KUAL entry point: choose whether the app opens by itself when the Kindle wakes.
case "$1" in
    on | off) echo "$1" > /mnt/us/mealplan/autostart.txt ;;
esac
