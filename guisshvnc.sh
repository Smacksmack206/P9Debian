#!/bin/bash

# ==============================================================================
# SSH & VNC Key and Connection Setup Script
#
# Description:
# This script provides a simple graphical interface (GUI) to guide users
# through creating SSH keys and configuring SSH and VNC connections between
# a macOS computer and a device running Debian (e.g., a Pixel phone)
# connected via a service like ZeroTier.
#
# Dependencies:
# - zenity: A tool for displaying GTK+ dialogs in command-line scripts.
#   On macOS, it can be installed via Homebrew: `brew install zenity`
#
# Author: Gemini
# Version: 2.0
# ==============================================================================

# --- Pre-flight Check: Verify zenity is installed ---
if ! command -v zenity &> /dev/null; then
    echo "Error: zenity is not installed."
    echo "This script requires zenity for the graphical interface."
    echo "On macOS, you can install it using Homebrew:"
    echo "brew install zenity"
    exit 1
fi

# --- Step 1: Gather Information using a GUI Form ---
# Use zenity to create a form to input the required details.
input=$(zenity --forms --title="SSH & VNC Connection Setup" --text="Enter device details" \
    --add-entry="Pixel (Debian) ZeroTier IP:" \
    --add-entry="Username for Pixel (Debian):" \
    --add-entry="SSH Port (default 22):" "22" \
    --separator=":")

# Exit if the user cancels the dialog
if [ -z "$input" ]; then
    zenity --error --text="Setup cancelled by user."
    exit 1
fi

# Parse the input into separate variables
pixel_ip=$(echo "$input" | cut -d: -f1)
pixel_user=$(echo "$input" | cut -d: -f2)
ssh_port=$(echo "$input" | cut -d: -f3)

# Validate that all fields were filled
if [ -z "$pixel_ip" ] || [ -z "$pixel_user" ] || [ -z "$ssh_port" ]; then
    zenity --error --text="All fields are required. Please run the script again."
    exit 1
fi

# --- Step 2: Generate SSH Keys ---
zenity --question --title="Generate SSH Keys" --text="A new SSH key pair (id_rsa_pixel_debian) will be created in your ~/.ssh directory. \n\nThis will not overwrite your existing keys. \n\nDo you want to continue?" --width=400
if [ $? -ne 0 ]; then
    zenity --info --text="Key generation cancelled. Exiting script."
    exit 1
fi

# Define the path for the new key
KEY_PATH="$HOME/.ssh/id_rsa_pixel_debian"
mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"

# Generate the key pair without a passphrase
ssh-keygen -t rsa -b 4096 -f "$KEY_PATH" -N ""

if [ $? -ne 0 ]; then
    zenity --error --text="Failed to generate SSH keys. Please check permissions for ~/.ssh"
    exit 1
fi

# --- Step 3: Display the Public Key ---
# Display the public key so the user can copy it.
zenity --text-info --title="Your New Public Key" \
    --filename="${KEY_PATH}.pub" \
    --width=600 --height=250 \
    --font="monospace" \
    --checkbox="I have copied the entire public key above."

if [ $? -ne 0 ]; then
    zenity --info --text="Setup process stopped. You can find the public key at ${KEY_PATH}.pub"
    exit 1
fi

# --- Step 4: Provide Instructions for the Debian Device ---
# Display a clear, step-by-step guide for the Debian part of the setup.
INSTRUCTIONS="
<b>Next Steps: Configure Your Debian System (on the Pixel)</b>

You need to add the public key and set up the servers. Open a terminal on your Debian device and follow these steps.

<span size='large'><b>Part A: Configure SSH Server</b></span>

<b>1. Install OpenSSH Server:</b>
   <tt>sudo apt update &amp;&amp; sudo apt upgrade -y</tt>
   <tt>sudo apt install openssh-server -y</tt>

<b>2. Enable &amp; Start the SSH Service:</b>
   <tt>sudo systemctl enable ssh --now</tt>
   <i>(You can check its status with: sudo systemctl status ssh)</i>

<b>3. Add Your Public Key:</b>
   <tt>mkdir -p ~/.ssh</tt>
   <tt>echo 'PASTE_YOUR_PUBLIC_KEY_HERE' >> ~/.ssh/authorized_keys</tt>
   <i>(Replace PASTE_YOUR_PUBLIC_KEY_HERE with the key you copied)</i>

<b>4. Set Correct Permissions:</b>
   <tt>chmod 700 ~/.ssh</tt>
   <tt>chmod 600 ~/.ssh/authorized_keys</tt>

<span size='large'><b>Part B: Configure VNC Server</b></span>

<b>1. Install a Desktop Environment (Optional, but recommended):</b>
   <i>If you don't have one, XFCE is a good lightweight choice.</i>
   <tt>sudo apt install xfce4 xfce4-goodies -y</tt>

<b>2. Install TightVNC Server:</b>
   <tt>sudo apt install tightvncserver -y</tt>

<b>3. Run VNC Server Once to Set Password:</b>
   <tt>vncserver</tt>
   <i>It will prompt you to create a password. This is what you'll use to connect. Enter 'n' when asked for a view-only password.</i>

<b>4. Configure the VNC Startup File:</b>
   <tt>echo '#!/bin/bash\nxrdb \$HOME/.Xresources\nstartxfce4 &amp;' > ~/.vnc/xstartup</tt>
   <tt>sudo chmod +x ~/.vnc/xstartup</tt>
   <i>This tells VNC to start the XFCE desktop.</i>

<b>5. Start the VNC Server:</b>
   <i>Kill the temporary server and start a new one on display :1</i>
   <tt>vncserver -kill :1</tt>
   <tt>vncserver :1 -geometry 1280x720 -depth 24</tt>
"
zenity --info --title="Instructions for Debian Setup" --width=750 --height=500 --no-wrap --text="$INSTRUCTIONS"


# --- Step 5: Final SSH Connection Command ---
FINAL_SSH_COMMAND="ssh -i $KEY_PATH -p ${ssh_port} ${pixel_user}@${pixel_ip}"

zenity --info --title="SSH Connection Ready" --width=600 --text="<b>SSH Setup Complete!</b>\n\nOnce you have finished the steps on your Debian device, open a <b>new Terminal window</b> on your Mac and run the following command to connect via SSH:\n\n<tt><b>$FINAL_SSH_COMMAND</b></tt>"

# --- Step 6: Final VNC Connection Command ---
FINAL_VNC_COMMAND="vnc://${pixel_ip}:5901"
zenity --info --title="VNC Connection Ready" --width=600 --text="<b>VNC Setup Complete!</b>\n\nTo connect to your device's graphical desktop, use the built-in VNC client on your Mac:\n\n1. Open <b>Finder</b>.\n2. Click <b>Go -> Connect to Server...</b> (or press Cmd+K).\n3. Enter the following address and click Connect:\n\n<tt><b>$FINAL_VNC_COMMAND</b></tt>\n\nUse the password you created when you first ran 'vncserver'."


exit 0

