#!/bin/sh
# KUAL entry point: start the Meal Plan app in the background and return at once.
/mnt/us/usbnet/bin/busybox ash /mnt/us/mealplan/mealplan.sh > /tmp/mealplan.log 2>&1 &
