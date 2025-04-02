#!/system/bin/sh
LOGFILE=/data/local/tmp/keep_terminal.log

while true; do
    if ! pidof com.android.virtualization.terminal > /dev/null; then
        echo "$(date) - Terminal was killed. Restarting..." >> $LOGFILE
        am start -n com.android.virtualization.terminal/.MainActivity
    fi
    sleep 10  # Check every 10 seconds
done &
