#!/bin/bash

# Update and install dependencies
sudo apt update && sudo apt install -y qemu-system-gui nbd-client lvm2 util-linux

# Load nbd kernel module
sudo modprobe nbd

# Move qcow2 file
if [ ! -f "/dev/android.qcow2" ]; then
    sudo cp /home/droid/P9Debian/android.qcow2 /dev/
fi

# Connect qcow2 image
if [ ! -b "/dev/nbd0" ]; then
    sudo qemu-nbd -c /dev/nbd0 /dev/android.qcow2
fi

# Partition the image
if ! sudo blkid /dev/nbd0p1 &> /dev/null; then
    sudo fdisk /dev/nbd0 <<EOF
n
p
1
""
""
w
EOF
fi

# Format the partition
if ! sudo blkid /dev/nbd0p1 &> /dev/null; then
    sudo mkfs.ext4 /dev/nbd0p1
fi

# Create mount point
sudo mkdir -p /media/android

# Mount the partition
if ! mountpoint -q /media/android; then
    sudo mount /dev/nbd0p1 /media/android
fi

# Create systemd service
cat <<EOF | sudo tee /etc/systemd/system/qcow2-mount.service
[Unit]
Description=Mount qcow2 image
After=network.target

[Service]
Type=oneshot
ExecStart=/etc/init.d/mount_qcow2.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

# Reload and enable systemd service
sudo systemctl daemon-reload
sudo systemctl enable qcow2-mount.service
sudo systemctl start qcow2-mount.service

# Change ownership of mount point
sudo chown droid:users /media/android

echo "qcow2 setup complete."
