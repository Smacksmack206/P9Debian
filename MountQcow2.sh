#!/bin/bash

# Load the nbd kernel module if not already loaded
sudo modprobe nbd

# Connect the qcow2 image to /dev/nbd0
sudo qemu-nbd -c /dev/nbd0 -f qcow2 /mnt/android/Pixel-9-AVF-Debian/android.qcow2

# Wait for the device to appear
sleep 2  # wait for the device to be ready

# Check if the device is available and then mount
if [ -e /dev/nbd0p1 ]; then
    # Mount the partition
    sudo mount /dev/nbd0p1 /mnt/android
    echo "Successfully mounted /dev/nbd0p1 to /mnt/android"
else
    echo "Failed to find /dev/nbd0p1, could not mount."
    exit 1
fi