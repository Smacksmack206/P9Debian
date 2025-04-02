#!/bin/bash

# ==============================================================================
# Script to update virtiofs systemd service files with specific content
# WARNING: This script overwrites existing service files.
# Run with sudo: sudo ./update_virtiofs_services.sh
# ==============================================================================

# Check for root privileges
if [[ $(id -u) -ne 0 ]]; then
  echo "ERROR: This script must be run as root (e.g., using sudo)." >&2
  exit 1
fi

# Define target file paths
INTERNAL_SERVICE_FILE="/etc/systemd/system/virtiofs_internal.service"
SHARED_SERVICE_FILE="/etc/systemd/system/virtiofs.service"

# --- Content for virtiofs_internal.service ---
# Uses the ExecStart line confirmed earlier for /mnt/internal
INTERNAL_CONTENT=$(cat <<'EOF'
[Unit]
Description=Mount virtiofs terminal app internal file path
After=network.target

[Service]
Type=oneshot
User=root
Group=root
ExecStart=/bin/bash -c 'mkdir -p /mnt/internal; mount -t virtiofs internal /mnt/internal; setfacl -m u:droid:rwx /mnt/internal; setfacl -m u:root:rwx /mnt/internal; setfacl -m g:users:rwx /mnt/internal; setfacl -d -m u:droid:rwx /mnt/internal; setfacl -d -m u:root:rwx /mnt/internal; setfacl -d -m g:users:rwx /mnt/internal; setfacl -m u:droid:rwx /mnt/internal/linux'
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
)

# --- Content for virtiofs.service ---
# Uses the ExecStart line styled like the internal one, but for /mnt/shared
SHARED_CONTENT=$(cat <<'EOF'
[Unit]
Description=Mount virtiofs terminal app shared file path
After=network.target

[Service]
Type=oneshot
User=root
Group=root
# This ExecStart line matches the requested style for /mnt/shared
ExecStart=/bin/bash -c 'mkdir -p /mnt/shared; mount -t virtiofs android /mnt/shared; setfacl -m u:droid:rwx /mnt/shared; setfacl -m u:root:rwx /mnt/shared; setfacl -m g:users:rwx /mnt/shared; setfacl -d -m u:droid:rwx /mnt/shared; setfacl -d -m u:root:rwx /mnt/shared; setfacl -d -m g:users:rwx /mnt/shared'
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
)

# Write the content to the files
echo "Updating ${INTERNAL_SERVICE_FILE}..."
if echo "${INTERNAL_CONTENT}" > "${INTERNAL_SERVICE_FILE}"; then
  echo "${INTERNAL_SERVICE_FILE} updated successfully."
else
  echo "ERROR: Failed to write to ${INTERNAL_SERVICE_FILE}" >&2
  exit 1
fi

echo "Updating ${SHARED_SERVICE_FILE}..."
if echo "${SHARED_CONTENT}" > "${SHARED_SERVICE_FILE}"; then
  echo "${SHARED_SERVICE_FILE} updated successfully."
else
  echo "ERROR: Failed to write to ${SHARED_SERVICE_FILE}" >&2
  exit 1
fi

# Reload systemd daemon to recognize changes
echo "Reloading systemd daemon..."
systemctl daemon-reload
echo "Systemd daemon reloaded."

# Optional: Ask to restart services
read -p "Do you want to attempt restarting the services now? (y/N) " -n 1 -r
echo # Move to a new line
if [[ $REPLY =~ ^[Yy]$ ]]; then
  echo "Attempting to restart virtiofs_internal.service..."
  systemctl restart virtiofs_internal.service
  echo "Attempting to restart virtiofs.service..."
  systemctl restart virtiofs.service
  echo "Service restart commands issued."
else
  echo "Services not restarted. Please restart them manually or reboot for changes to take full effect if they were running."
fi

echo "Script finished."
exit 0
