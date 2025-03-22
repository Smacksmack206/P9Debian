#!/bin/bash
sudo modprobe nbd
sudo qemu-nbd -c /dev/nbd0 /mnt/android/android.qcow2
sudo mount /dev/nbd0p1 /mnt/android
