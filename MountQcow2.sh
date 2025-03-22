#!/bin/bash
sudo modprobe nbd
sudo qemu-nbd -c /dev/nbd0 -f qcow2 /mnt/android/Pixel-9-AVF-Debian/android.qcow2
sudo mount /dev/nbd0p1 /mnt/android
