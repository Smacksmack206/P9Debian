#!/bin/bash

# Update and install necessary packages
sudo apt update
sudo apt upgrade
sudo apt install xserver-xorg gnup git wget curl

wget -qO - 'https://proget.makedeb.org/debian-feeds/makedeb.pub' | gpg --dearmor | sudo tee /usr/share/keyrings/makedeb-archive-keyring.gpg 1> /dev/null
echo 'deb [signed-by=/usr/share/keyrings/makedeb-archive-keyring.gpg arch=all] https://proget.makedeb.org/ makedeb main' | sudo tee /etc/apt/sources.list.d/makedeb.list


sudo apt update

# Add user to required groups
sudo usermod -aG input $(whoami)
sudo usermod -aG video $(whoami)
sudo usermod -aG tty $(whoami)

# Configure Xorg to allow anybody
echo "allowed_users=anybody" | sudo tee /etc/X11/Xwrapper.config

#installRust
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

source ~/.cargo/env

# Create and configure the Xorg systemd service
sudo tee /etc/systemd/system/xorg-server.service <<EOF
[Unit]
Description=Start Xorg on Display :0
After=network.target

[Service]
ExecStart=/usr/bin/Xorg :0
Restart=always
User=root

[Install]
WantedBy=multi-user.target
EOF

# Reload systemd, enable, and start the Xorg service
sudo systemctl daemon-reload
sudo systemctl enable xorg-server
sudo systemctl start xorg-server

# Create and configure the set-display systemd service
sudo tee /etc/systemd/system/set-display.service <<EOF
[Unit]
Description=Set DISPLAY Environment Variable
After=multi-user.target

[Service]
Type=oneshot
ExecStart=/bin/bash -c "echo 'export DISPLAY=:0' >> /etc/environment"
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

# Reload systemd, enable, and start the set-display service
sudo systemctl daemon-reload
sudo systemctl enable set-display
sudo systemctl start set-display to

echo "Installing core development tools..."
sudo apt install -y build-essential manpages-dev

echo "Installing version control and additional tools..."
sudo apt install -y git cmake ninja-build

echo "Installing Autotools..."
sudo apt install -y autoconf automake libtool

echo "Installing debugging and optimization tools..."
sudo apt install -y gdb valgrind strace ltrace

cargo install just

sudo apt install -y adduser apparmor apt apt-listchanges apt-utils avahi-daemon avahi-utils base-files base-passwd bash bash-completion bfh-gnome-desktop bind9-host bpfcc-tools bsdutils build-essential ca-certificates cargo coreutils curl dash dbus debconf debhelper debian-archive-keyring debianutils devscripts diffutils dpkg e2fsprogs ethtool fakeroot findutils forwarder-guest forwarder-guest-launcher gcc-12-base git gnome-shell gpgv grep grub-efi-arm64 grub-efi-arm64-signed gzip hostname init init-system-helpers iptables iputils-ping less libacl1 libapt-pkg6.0 libattr1 libaudit-common libaudit1 libblkid1 libbz2-1.0 libc-bin libc6 libcap-ng0 libcap2 libclang-dev libcom-err2 libcrypt1 libdb5.3 libdbus-1-dev libdebconfclient0 libexpat1-dev libext2fs2 libffi8 libfile-find-rule-perl libflatpak-dev libfontconfig-dev libfreetype-dev libgbm-dev libgcc-s1 libgcrypt20 libgdbm-compat4 libgdbm6 libglvnd-dev libgmp10 libgnutls30 libgpg-error0 libhogweed6 libidn2-0 libinput-dev liblz4-1 liblzma5 libmd0 libmount1 libnettle8 libnss-mdns libnss-myhostname libnumber-compare-perl libp11-kit0 libpam-modules libpam-modules-bin libpam-runtime libpam-systemd libpam0g libpam0g-dev libpcre2-8-0 libperl5.36 libpipewire-0.3-dev libpixman-1-dev libpulse-dev libseat-dev libseccomp2 libselinux1 libsemanage-common libsemanage2 libsepol2 libsmartcols1 libss2 libssl-dev libssl3 libstdc++6 libsystemd-dev libsystemd0 libtasn1-6 libtext-glob-perl libtinfo6 libudev1 libunistring2 libuuid1 libvulkan1 libwayland-dev libxkbcommon-dev libxxhash0 libzstd1 linux-headers-6.1.0-29-avf-arm64 linux-image-6.1.0-29-avf-arm64-unsigned login logsave makedeb man-db manpages mawk mesa-vulkan-drivers meson mold mount nano ncurses-base ncurses-bin netbase netplan.io ninja-build openssl passwd pciutils perl perl-base perl-modules-5.36 pkg-config polkitd procps psmisc python3-launchpadlib reportbug screen sed shim-signed shutdown-runner socat software-properties-common storage-balloon-agent sudo systemd-timesyncd sysvinit-utils tar tcpdump tigervnc-standalone-server traceroute tzdata udev unattended-upgrades usrmerge util-linux util-linux-extra uuid-runtime vim vim-tiny vulkan-tools weston whiptail xserver-xorg xwayland xz-utils zlib1g zstd 

#DNS fix for post gnome desktop environment 
sudo rm /etc/resolv.conf
sudo touch /etc/resolv.conf
sudo chmod 644 /etc/resolv.conf
echo "nameserver 8.8.8.8" | sudo tee /etc/resolv.conf
echo "nameserver 8.8.4.4" | sudo tee -a /etc/resolv.conf
