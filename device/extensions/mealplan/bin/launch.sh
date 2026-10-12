#!/bin/sh
# KUAL entry point: start the Meal Plan app in the background and return at once.
# Also make sure the background helper is running, in case the boot entry did not start it.
/mnt/us/usbnet/bin/busybox ash /mnt/us/mealplan/watch.sh > /dev/null 2>&1 &
/mnt/us/usbnet/bin/busybox ash /mnt/us/mealplan/mealplan.sh > /tmp/mealplan.log 2>&1 &
