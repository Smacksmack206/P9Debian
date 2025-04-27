#!/usr/bin/env python3

import subprocess
import os
import sys
import pwd
import grp
import time
import datetime
import shlex
import logging
from pathlib import Path
import shutil # <--- Import for shutil.which()

# --- Rich TUI Imports ---
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn
from rich.panel import Panel
from rich.text import Text
from rich.syntax import Syntax
from rich.rule import Rule
from rich.prompt import Confirm, Prompt # Added Prompt for potential future use

# --- Configuration ---
LOCAL_QCOW_PATH = Path("/android.qcow2")
DEFAULT_QCOW_SIZE = "126G" # Default size if creating the QCOW2 file
NBD_DEVICE = "/dev/nbd0"
VG_NAME = "data_vg"
LV_NAME = "data_lv"
LVM_MOUNT_POINT = Path("/mnt/data")
LV_DEVICE_PATH = Path(f"/dev/{VG_NAME}/{LV_NAME}")
DEBIAN_USER = "droid"
DEBIAN_GROUP = "users"  # Group for mount point/Samba/VNC
ZT_NETWORK_ID = "d3ecf5726d1e75fe"
VNC_DISPLAY_NUM = "1"
VNC_DISPLAY = f":{VNC_DISPLAY_NUM}"
VNC_GEOMETRY = "2424x1080" # Example geometry, adjust as needed
VNC_DEPTH = "24" # Example depth, adjust as needed
VNC_XSTARTUP_PATH = Path(f"/home/{DEBIAN_USER}/.vnc/xstartup")
SAMBA_SHARE_NAME = "DataShare"
SAMBA_SHARE_PATH = str(LVM_MOUNT_POINT) # Samba config needs string
PODMAN_ROOTFUL_STORAGE_PATH = LVM_MOUNT_POINT / "podman_storage"

REQUIRED_PACKAGES = [
    "sudo", "gnupg", "apt-transport-https", "ca-certificates", "curl", "wget", "git",
    "qemu-utils", # Provides qemu-img, qemu-nbd
    "lvm2", # Provides vgs, pvcreate, etc.
    "e2fsprogs", # Provides mkfs.ext4
    "acl",
    "gnome-core", "gnome-session", "gnome-shell", "gnome-terminal", "nautilus", "firefox-esr",
    "tigervnc-standalone-server", # Provides vncserver
    "xserver-xorg-core", "xwayland", "weston", "dbus-x11",
    "openssh-client", # Provides ssh
    "openssh-server",
    "samba", "samba-common-bin", # Provides smbd
    "build-essential", "python3-pip", "python3-venv", "nodejs", "npm",
    "htop", "neofetch",
    "dselect", "tzdata", "members",
    "psmisc", "lsof",
    "podman", # Provides podman
    "uidmap", "fuse-overlayfs", "slirp4netns", "dbus-user-session",
    # systemctl is part of systemd, which should always be present on Debian >= 8
]

KEY_COMMANDS_TO_VALIDATE = [
    "qemu-img",
    "qemu-nbd",
    "vgs",
    "vncserver",
    "ssh",
    "smbd",
    "gnome-shell",
    "curl",
    "sudo", # Should exist if script runs with sudo
    "podman",
    "systemctl", # Should always exist
]

# --- Setup Logging ---
# Use a timestamp in the log file name
current_timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
LOG_FILENAME = f"/var/log/setup_avf_interactive_{current_timestamp}.log"
logging.basicConfig(
    level=logging.DEBUG, # Log DEBUG level and above to file
    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s',
    filename=LOG_FILENAME,
    filemode='w' # Overwrite log file each run
)
# Define a logger for this script
logger = logging.getLogger("AVFInstaller")

# --- Console for Rich Output ---
# Record=True allows saving console output later if needed
console = Console(record=True, log_time_format="[%Y-%m-%d %H:%M:%S]")

# --- Helper Functions ---

def run_command(command, description="Running command", check=True, shell=False, capture_output=True, text=True, user=None, cwd=None, env=None, show_output=False):
    """
    Runs a command using subprocess.run, logs execution details, and handles errors.
    Uses sudo -u USER -H -- command for running as another user.
    Returns the subprocess.CompletedProcess object on success (return code 0), None on failure.
    """
    if isinstance(command, list):
        cmd_str_display = ' '.join(shlex.quote(str(arg)) for arg in command)
        cmd_to_run = command
    else:
        # If command is a string, assume shell=True is intended by caller
        cmd_str_display = command
        cmd_to_run = command
        if not shell:
            logger.warning(f"Command is a string ('{cmd_str_display}') but shell=False. This might not work as expected.")

    log_prefix = f"[User: {user}] " if user else ""
    logger.info(f"{log_prefix}Executing: {cmd_str_display}")
    console.log(f"{log_prefix}{description}: [dim]{cmd_str_display}[/dim]")

    full_env = os.environ.copy()

    # Modify command and environment if running as a specific user
    if user:
        try:
            pw_info = pwd.getpwnam(user)
            # Set HOME, USER, LOGNAME for the target user's environment if sudo doesn't handle it well enough
            full_env['HOME'] = pw_info.pw_dir
            full_env['USER'] = user
            full_env['LOGNAME'] = user
            # Prepend sudo arguments to run as the target user
            # Using -H ensures home directory is set correctly
            # Using -- ensures arguments are passed to the command, not sudo itself
            sudo_prefix = ['sudo', '-u', user, '-H', '--']
            if isinstance(cmd_to_run, list):
                 cmd_to_run = sudo_prefix + cmd_to_run
            else:
                 # Handling shell=True with sudo is complex and less safe; avoid if possible
                 logger.warning("Running shell=True command as different user via sudo is complex. Prefer list-based commands.")
                 # This might not correctly handle complex shell scripts passed as a string
                 cmd_to_run = ' '.join(sudo_prefix) + ' ' + cmd_to_run
                 shell = True # Force shell=True if we constructed a string

            cmd_str_display = ' '.join(shlex.quote(str(arg)) for arg in cmd_to_run) if isinstance(cmd_to_run, list) else cmd_to_run
            logger.info(f"Updated command with sudo: {cmd_str_display}")

        except KeyError:
            logger.error(f"User '{user}' not found for run_command.")
            console.print(f"[bold red]Error:[/bold red] System user '{user}' not found.")
            return None # Indicate failure

    # Update environment variables if provided
    if env:
        full_env.update(env)

    # Execute the command
    try:
        result = subprocess.run(
            cmd_to_run,
            check=check, # If True, throws CalledProcessError on non-zero exit
            shell=shell,
            capture_output=capture_output,
            text=text, # Decode stdout/stderr as text
            cwd=cwd,
            env=full_env,
        )
        # Log detailed results for debugging
        logger.debug(f"Command completed: {cmd_str_display}")
        logger.debug(f"Return Code: {result.returncode}")
        # Log stdout/stderr only if they are not empty
        if result.stdout: logger.debug(f"Stdout:\n{result.stdout.strip()}")
        if result.stderr: logger.debug(f"Stderr:\n{result.stderr.strip()}")

        # Optionally print output to console
        if show_output and result.stdout:
             console.print(f"[dim]{result.stdout.strip()}[/dim]")
        if show_output and result.stderr:
             # Show stderr clearly marked
             console.print(f"[yellow]Stderr:[/yellow] [dim]{result.stderr.strip()}[/dim]")

        # Explicitly check return code even if check=False
        if result.returncode == 0:
            console.log(f"[green]Success:[/green] {description}")
            return result # Return the CompletedProcess object on success
        else:
             # This block handles non-zero return codes when check=False
             console.print(f"[bold red]Error:[/bold red] Command failed (Code: {result.returncode}): [dim]{cmd_str_display}[/dim]")
             if result.stderr: console.print(f"[yellow]Stderr:[/yellow] {result.stderr.strip()}")
             # Only show stdout on error if it contains something potentially useful
             if result.stdout: console.print(f"[dim]Stdout:[/dim] {result.stdout.strip()}")
             logger.error(f"Command failed with return code {result.returncode}: {cmd_str_display}")
             return None # Indicate failure

    except subprocess.CalledProcessError as e:
        # This catches non-zero exit codes only when check=True
        logger.error(f"Command failed: {cmd_str_display}", exc_info=False) # Log exception trace if needed exc_info=True
        logger.error(f"Return code: {e.returncode}")
        if e.stdout: logger.error(f"Stdout:\n{e.stdout.strip()}")
        if e.stderr: logger.error(f"Stderr:\n{e.stderr.strip()}")
        console.print(f"[bold red]Error:[/bold red] Command failed (Code: {e.returncode}): [dim]{cmd_str_display}[/dim]")
        if e.stderr: console.print(f"[yellow]Stderr:[/yellow] {e.stderr.strip()}")
        if e.stdout: console.print(f"[dim]Stdout:[/dim] {e.stdout.strip()}")
        return None # Indicate failure
    except FileNotFoundError:
        # This occurs if the command executable itself is not found
        cmd_exec = cmd_to_run[0] if isinstance(cmd_to_run, list) else cmd_to_run.split()[0]
        logger.error(f"Command executable not found: '{cmd_exec}' for command: {cmd_str_display}")
        console.print(f"[bold red]Error:[/bold red] Command executable not found: '{cmd_exec}'. Check installation and PATH.")
        return None # Indicate failure
    except Exception as e:
        # Catch any other unexpected exceptions during subprocess execution
        logger.exception(f"An unexpected error occurred running command: {cmd_str_display}") # Log full traceback
        console.print(f"[bold red]Unexpected Error during command execution:[/bold red] {e}")
        console.print_exception(show_locals=False) # Print traceback to console
        return None # Indicate failure


def check_group_exists(group_name):
    """Checks if a system group exists."""
    try:
        grp.getgrnam(group_name)
        logger.debug(f"Group '{group_name}' found.")
        return True
    except KeyError:
        logger.debug(f"Group '{group_name}' not found.")
        return False

def check_user_exists(user_name):
    """Checks if a system user exists."""
    try:
        pwd.getpwnam(user_name)
        logger.debug(f"User '{user_name}' found.")
        return True
    except KeyError:
        logger.debug(f"User '{user_name}' not found.")
        return False

def write_file(path, content, owner=None, group=None, permissions=None, show_content=True):
    """
    Writes content to a file, creating parent directories if needed.
    Optionally sets owner, group, and permissions (as octal string like "0644").
    Returns True on success, False on failure.
    """
    path = Path(path)
    logger.info(f"Attempting to write file: {path}")
    console.log(f"Preparing file: [cyan]{path}[/cyan]")

    # Display content with syntax highlighting before writing
    if show_content:
        # Determine language for syntax highlighting based on filename/extension
        lang = "bash" # Default
        if any(s in str(path) for s in [".service", ".mount", ".timer", "xstartup", ".profile"]): lang = "bash"
        elif str(path).endswith((".conf", ".cfg", ".ini")): lang = "ini" # Adjust if TOML etc.
        elif str(path).endswith(".json"): lang = "json"
        elif str(path).endswith(".xml"): lang = "xml"
        elif str(path).endswith(".yaml") or str(path).endswith(".yml"): lang = "yaml"
        else: lang = "text" # Fallback

        syntax = Syntax(content, lang, theme="default", line_numbers=True, word_wrap=False)
        console.print(Panel(syntax, title=f"Content for {path.name}", border_style="dim"))

    try:
        # Create parent directories if they don't exist
        path.parent.mkdir(parents=True, exist_ok=True)
        logger.debug(f"Ensured parent directory exists: {path.parent}")

        # Write the file content
        path.write_text(content)
        logger.info(f"Successfully wrote content to {path}")
        console.log(f"[green]✓[/green] File written: [cyan]{path}[/cyan]")

        # Set permissions first, then owner/group
        if permissions:
            try:
                octal_perm = int(permissions, 8) # Convert string "0644" to octal integer
                os.chmod(path, octal_perm)
                logger.info(f"Set permissions {permissions} for {path}")
                console.log(f"  - Permissions set to [yellow]{permissions}[/yellow]")
            except ValueError:
                logger.error(f"Invalid permission format '{permissions}'. Should be octal string e.g., '0644'.")
                console.print(f"[bold red]Error:[/bold red] Invalid permission format '{permissions}'.")
                return False # Fail if permissions are invalid

        # Set ownership if specified
        if owner or group:
            uid = pwd.getpwnam(owner).pw_uid if owner else -1
            gid = grp.getgrnam(group).gr_gid if group else -1
            if uid != -1 or gid != -1:
                 try:
                    os.chown(path, uid, gid)
                    owner_str = owner or '(current)'
                    group_str = group or '(current)'
                    logger.info(f"Set owner={owner_str}({uid}), group={group_str}({gid}) for {path}")
                    console.log(f"  - Ownership set to [yellow]{owner_str}:{group_str}[/yellow]")
                 except Exception as chown_err:
                      logger.error(f"Failed to set ownership {owner}:{group} for {path}: {chown_err}")
                      console.print(f"[bold red]Error:[/bold red] Failed to set ownership for {path}: {chown_err}")
                      return False # Fail if ownership change fails

        return True # Indicate success

    except Exception as e:
        # Catch any other errors during file writing/configuration
        logger.exception(f"Failed to write or configure file {path}") # Log full traceback
        console.print(f"[bold red]Error:[/bold red] Failed writing/configuring file {path}: {e}")
        return False # Indicate failure

# --- Installer Steps Definition ---
installer_steps = []

# Decorator to register steps in the order they are defined
def installer_step(title):
    def decorator(func):
        logger.debug(f"Registering installer step: {title}")
        installer_steps.append({"title": title, "func": func})
        return func
    return decorator

# --- Step Implementations ---

@installer_step("Prerequisite Checks")
def step_prereqs(progress, task_id):
    """Checks for root privileges, QCOW2 file (warns if missing), user/groups."""
    if os.geteuid() != 0:
        console.print("[bold red]Fatal Error:[/bold red] This script must be run with root privileges (e.g., using `sudo`).")
        logger.critical("Script not run as root. Aborting.")
        return False # Fatal error

    # --- QCOW2 Check (Warn if Missing) ---
    console.print(f"Checking for QCOW2 file at [cyan]{LOCAL_QCOW_PATH}[/cyan]...")
    logger.info(f"Checking for QCOW2 file: {LOCAL_QCOW_PATH}")
    if not LOCAL_QCOW_PATH.is_file():
        console.print(f"[yellow]Warning:[/yellow] QCOW2 file not found. Will attempt to create it in a later step.")
        logger.warning(f"QCOW2 file not found: {LOCAL_QCOW_PATH}. Will attempt creation after dependencies.")
    else:
        console.print(f"[green]✓[/green] QCOW2 file found.")
    # --- End QCOW2 Check ---

    # --- User Check ---
    if not check_user_exists(DEBIAN_USER):
         console.print(f"[bold red]Fatal Error:[/bold red] Required user '{DEBIAN_USER}' does not exist. Please create the user first.")
         logger.critical(f"User '{DEBIAN_USER}' not found. Aborting.")
         return False # Fatal error

    # --- Group Checks (Create if Missing) ---
    groups_to_check_create = [DEBIAN_GROUP, "disk"]
    for group_name in groups_to_check_create:
         if not check_group_exists(group_name):
             is_system_group = (group_name == "disk") # Example: 'disk' is often a system group
             console.print(f"[yellow]Warning:[/yellow] Group '{group_name}' not found. Creating...")
             logger.warning(f"Group '{group_name}' not found. Attempting creation.")
             groupadd_cmd = ['groupadd']
             if is_system_group:
                 groupadd_cmd.append('-r') # Use -r for system groups if appropriate
             groupadd_cmd.append(group_name)

             if run_command(groupadd_cmd, description=f"Creating group '{group_name}'"):
                 console.print(f"[green]✓[/green] Group '{group_name}' created.")
                 logger.info(f"Successfully created group '{group_name}'.")
             else:
                 console.print(f"[bold red]Error:[/bold red] Failed to create group '{group_name}'. This might cause issues later.")
                 logger.error(f"Failed to create group '{group_name}'.")
                 # Decide if fatal. 'disk' group is important for NBD/QCOW permissions.
                 if group_name == "disk":
                      console.print("[bold red]Fatal Error:[/bold red] Failed to create essential 'disk' group. Cannot proceed.")
                      return False
                 # Allow continuing if DEBIAN_GROUP creation failed, but warn heavily.
                 console.print("[yellow]Continuing, but services using this group might fail.[/yellow]")

    logger.info("Prerequisite checks passed.")
    progress.update(task_id, advance=1)
    return True


@installer_step("Install Dependencies")
def step_install_deps(progress, task_id):
    """Updates apt, upgrades packages, installs required packages, and verifies key commands."""
    console.print("[cyan]Updating package lists (apt-get update)...[/cyan]")
    update_result = run_command(['apt-get', 'update', '-qq'], description="apt-get update", show_output=False) # -qq for quieter output
    if not update_result:
        console.print("[bold yellow]Warning:[/bold yellow] 'apt-get update' failed. Check network and APT sources. Attempting to continue...")
        logger.warning("'apt-get update' failed. Proceeding cautiously.")
        # Optionally ask user if they want to continue here if update fails

    console.print("[cyan]Upgrading existing packages (apt-get upgrade -y)...[/cyan]")
    # Use non-interactive frontend for upgrades
    upgrade_env = os.environ.copy()
    upgrade_env['DEBIAN_FRONTEND'] = 'noninteractive'
    # Show output for upgrade as it can take time and provide info
    with console.status("[bold cyan]Running apt-get upgrade...", spinner="dots"):
        upgrade_result = run_command(['apt-get', 'upgrade', '-y'], description="apt-get upgrade", env=upgrade_env, show_output=False) # Keep show_output=False if too verbose
    if not upgrade_result:
        console.print("[bold yellow]Warning:[/bold yellow] 'apt-get upgrade' failed. System might not be fully up-to-date.")
        logger.warning("'apt-get upgrade' failed. Proceeding.")
    else:
        console.print("[green]✓[/green] Package upgrade completed.")


    console.print("[cyan]Installing required packages...[/cyan]")
    install_env = os.environ.copy()
    install_env['DEBIAN_FRONTEND'] = 'noninteractive'
    with console.status("[bold cyan]Running apt-get install...", spinner="dots"):
        install_result = run_command(['apt-get', 'install', '-y'] + REQUIRED_PACKAGES, description="apt-get install", env=install_env, show_output=False) # Keep show_output=False

    if not install_result:
        console.print("[bold red]Error:[/bold red] Failed to install one or more required packages during initial attempt.")
        logger.error("Initial 'apt-get install' failed.")
        console.print("Attempting 'apt --fix-broken install' to resolve potential issues...")
        with console.status("[bold cyan]Running apt --fix-broken install...", spinner="dots"):
             fix_result = run_command(['apt-get', '--fix-broken', 'install', '-y'], description="apt --fix-broken install", env=install_env, show_output=False)

        if not fix_result:
             console.print("[bold red]Error:[/bold red] 'apt --fix-broken install' also failed. Unable to resolve dependencies.")
             logger.error("'apt --fix-broken install' failed.")
             return False # Cann