#!/bin/bash

# ==================================================================================
# Setup QEMU, binfmt, Docker, and QEMU Web Desktop on Debian (aarch64)
# ==================================================================================
#
# This script automates the following tasks:
#   1. Installs QEMU user-mode emulation and registers it with binfmt_misc
#      to allow transparent execution of x86_64 binaries on an ARM64 host.
#   2. Installs Docker Engine, the containerization platform.
#   3. Configures Docker permissions so the current user can run it without 'sudo'.
#   4. Deploys 'qemu-web-desktop', a Docker container that provides a full Linux
#      desktop environment (running under QEMU) accessible via a web browser.
#
# How to run:
#   1. Save this script as 'setup_full_qemu.sh'.
#   2. Make it executable: chmod +x setup_full_qemu.sh
#   3. Run it with sudo:   sudo ./setup_full_qemu.sh
#

# --- Configuration ---
# Exit immediately if a command exits with a non-zero status.
set -e

# --- Pre-flight Checks ---

echo "--- Running Pre-flight Checks ---"

# 1. Check for root privileges
if [ "$(id -u)" -ne 0 ]; then
  echo "Error: This script must be run as root." >&2
  echo "Please use 'sudo ./setup_full_qemu.sh' to run it." >&2
  exit 1
fi

# 2. Check for the correct architecture
if [ "$(uname -m)" != "aarch64" ]; then
  echo "Warning: This script is intended for aarch64 systems."
  echo "Your current architecture is '$(uname -m)'. The script may not work as expected."
fi

# 3. Get the original user who ran sudo
# This is crucial for adding the correct user to the docker group later.
ORIGINAL_USER=${SUDO_USER:-$(whoami)}
echo "--- Checks passed. Will perform installations and configurations for user: $ORIGINAL_USER ---"
echo ""

# --- Part 1: QEMU and binfmt Setup ---

echo "--> Part 1 of 4: Installing QEMU user static and binfmt support..."
apt-get update
apt-get install -y qemu-user-static binfmt-support
echo "--- QEMU and binfmt setup complete. ---"
echo ""

# --- Part 2: Docker Installation ---

echo "--> Part 2 of 4: Installing Docker Engine..."
if command -v docker &> /dev/null; then
    echo "Docker is already installed. Skipping installation."
else
    echo "Docker not found. Installing now..."
    # Add Docker's official GPG key:
    apt-get install -y ca-certificates curl
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc

    # Add the repository to Apt sources:
    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian \
      $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
      tee /etc/apt/sources.list.d/docker.list > /dev/null
    apt-get update

    # Install the latest version
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
echo "--- Docker installation complete. ---"
echo ""

# --- Part 3: Docker Post-Installation (Permissions) ---

echo "--> Part 3 of 4: Configuring Docker permissions..."
if getent group docker > /dev/null; then
    echo "Docker group already exists."
else
    echo "Creating docker group."
    groupadd docker
fi

echo "Adding user '$ORIGINAL_USER' to the 'docker' group."
usermod -aG docker "$ORIGINAL_USER"
echo "--- Docker permissions configured. ---"
echo ""

# --- Part 4: Deploy QEMU Web Desktop ---

echo "--> Part 4 of 4: Pulling and starting QEMU Web Desktop container..."
# This will pull a pre-built Docker image that runs a full desktop via QEMU,
# accessible through a web browser on port 8080.
docker run -d \
  -p 8080:8080 \
  --name qemu-web-desktop \
  --restart unless-stopped \
  scottyhardy/qemu-web-desktop:latest

echo ""
echo "--------------------------------------------------"
echo "✅ All Setup Complete!"
echo "--------------------------------------------------"
echo ""
echo "--- IMPORTANT NEXT STEPS ---"
echo ""
echo "1.  **LOG OUT AND LOG BACK IN!**"
echo "    You MUST log out and log back in for the Docker group permissions to take effect."
echo "    Until you do, you will still get 'permission denied' errors without 'sudo'."
echo ""
echo "2.  **Access Your Web Desktop:**"
echo "    Once you've logged back in, open a web browser and go to:"
echo "    http://localhost:8080"
echo "    (If accessing from another device on the network, use your machine's IP address)."
echo ""
echo "3.  **Manage the Container:**"
echo "    - To stop the desktop:  docker stop qemu-web-desktop"
echo "    - To start it again:   docker start qemu-web-desktop"
echo ""

