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
from rich.prompt import Confirm, Prompt

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
ZT_NETWORK_ID = "d3ecf5726d1e75fe" # Example ZeroTier Network ID
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
    "sudo",
    "podman",
    "systemctl",
]

# --- Setup Logging ---
current_timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
LOG_FILENAME = f"/var/log/setup_avf_interactive_{current_timestamp}.log"
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s',
    filename=LOG_FILENAME,
    filemode='w'
)
logger = logging.getLogger("AVFInstaller")

# --- Console for Rich Output ---
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
        cmd_str_display = command
        cmd_to_run = command
        if not shell:
            logger.warning(f"Command is a string ('{cmd_str_display}') but shell=False. This might not work as expected.")

    log_prefix = f"[User: {user}] " if user else ""
    logger.info(f"{log_prefix}Executing: {cmd_str_display}")
    # Avoid logging sensitive info if description implies it
    sensitive_desc = "password" in description.lower()
    console.log(f"{log_prefix}{description}: [dim]{'(command hidden)' if sensitive_desc else cmd_str_display}[/dim]")

    full_env = os.environ.copy()

    if user:
        try:
            pw_info = pwd.getpwnam(user)
            full_env['HOME'] = pw_info.pw_dir
            full_env['USER'] = user
            full_env['LOGNAME'] = user
            sudo_prefix = ['sudo', '-u', user, '-H', '--']
            if isinstance(cmd_to_run, list):
                 cmd_to_run = sudo_prefix + cmd_to_run
            else:
                 logger.warning("Running shell=True command as different user via sudo is complex. Prefer list-based commands.")
                 cmd_to_run = ' '.join(sudo_prefix) + ' ' + cmd_to_run
                 shell = True

            # Update display string after adding sudo
            cmd_str_display = ' '.join(shlex.quote(str(arg)) for arg in cmd_to_run) if isinstance(cmd_to_run, list) else cmd_to_run
            logger.info(f"Updated command with sudo: {'(command hidden)' if sensitive_desc else cmd_str_display}")

        except KeyError:
            logger.error(f"User '{user}' not found for run_command.")
            console.print(f"[bold red]Error:[/bold red] System user '{user}' not found.")
            return None

    if env:
        full_env.update(env)

    try:
        result = subprocess.run(
            cmd_to_run,
            check=check,
            shell=shell,
            capture_output=capture_output,
            text=text,
            cwd=cwd,
            env=full_env,
        )
        logger.debug(f"Command completed: {'(command hidden)' if sensitive_desc else cmd_str_display}")
        logger.debug(f"Return Code: {result.returncode}")
        if result.stdout: logger.debug(f"Stdout:\n{result.stdout.strip()}")
        if result.stderr: logger.debug(f"Stderr:\n{result.stderr.strip()}")

        if show_output and result.stdout:
             console.print(f"[dim]{result.stdout.strip()}[/dim]")
        if show_output and result.stderr:
             console.print(f"[yellow]Stderr:[/yellow] [dim]{result.stderr.strip()}[/dim]")

        if result.returncode == 0:
            console.log(f"[green]Success:[/green] {description}")
            return result
        else:
             console.print(f"[bold red]Error:[/bold red] Command failed (Code: {result.returncode}): [dim]{'(command hidden)' if sensitive_desc else cmd_str_display}[/dim]")
             if result.stderr: console.print(f"[yellow]Stderr:[/yellow] {result.stderr.strip()}")
             if result.stdout: console.print(f"[dim]Stdout:[/dim] {result.stdout.strip()}")
             logger.error(f"Command failed with return code {result.returncode}: {'(command hidden)' if sensitive_desc else cmd_str_display}")
             return None

    except subprocess.CalledProcessError as e:
        logger.error(f"Command failed: {'(command hidden)' if sensitive_desc else cmd_str_display}", exc_info=False)
        logger.error(f"Return code: {e.returncode}")
        if e.stdout: logger.error(f"Stdout:\n{e.stdout.strip()}")
        if e.stderr: logger.error(f"Stderr:\n{e.stderr.strip()}")
        console.print(f"[bold red]Error:[/bold red] Command failed (Code: {e.returncode}): [dim]{'(command hidden)' if sensitive_desc else cmd_str_display}[/dim]")
        if e.stderr: console.print(f"[yellow]Stderr:[/yellow] {e.stderr.strip()}")
        if e.stdout: console.print(f"[dim]Stdout:[/dim] {e.stdout.strip()}")
        return None
    except FileNotFoundError:
        cmd_exec = cmd_to_run[0] if isinstance(cmd_to_run, list) else cmd_to_run.split()[0]
        logger.error(f"Command executable not found: '{cmd_exec}' for command: {'(command hidden)' if sensitive_desc else cmd_str_display}")
        console.print(f"[bold red]Error:[/bold red] Command executable not found: '{cmd_exec}'. Check installation and PATH.")
        return None
    except Exception as e:
        logger.exception(f"An unexpected error occurred running command: {'(command hidden)' if sensitive_desc else cmd_str_display}")
        console.print(f"[bold red]Unexpected Error during command execution:[/bold red] {e}")
        console.print_exception(show_locals=False)
        return None


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

    if show_content:
        lang = "bash"
        if any(s in str(path) for s in [".service", ".mount", ".timer", "xstartup", ".profile"]): lang = "bash"
        elif str(path).endswith((".conf", ".cfg", ".ini")): lang = "ini"
        elif str(path).endswith(".json"): lang = "json"
        elif str(path).endswith(".xml"): lang = "xml"
        elif str(path).endswith((".yaml", ".yml")): lang = "yaml"
        else: lang = "text"

        syntax = Syntax(content, lang, theme="default", line_numbers=True, word_wrap=False)
        console.print(Panel(syntax, title=f"Content for {path.name}", border_style="dim"))

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        logger.debug(f"Ensured parent directory exists: {path.parent}")
        path.write_text(content)
        logger.info(f"Successfully wrote content to {path}")
        console.log(f"[green]✓[/green] File written: [cyan]{path}[/cyan]")

        if permissions:
            try:
                octal_perm = int(permissions, 8)
                os.chmod(path, octal_perm)
                logger.info(f"Set permissions {permissions} for {path}")
                console.log(f"  - Permissions set to [yellow]{permissions}[/yellow]")
            except ValueError:
                logger.error(f"Invalid permission format '{permissions}'. Should be octal string e.g., '0644'.")
                console.print(f"[bold red]Error:[/bold red] Invalid permission format '{permissions}'.")
                return False

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
                      return False

        return True

    except Exception as e:
        logger.exception(f"Failed to write or configure file {path}")
        console.print(f"[bold red]Error:[/bold red] Failed writing/configuring file {path}: {e}")
        return False

# --- Installer Steps Definition ---
installer_steps = []

def installer_step(title):
    """Decorator to register a function as an installer step."""
    def decorator(func):
        logger.debug(f"Registering installer step: {title}")
        installer_steps.append({"title": title, "func": func})
        return func
    return decorator
# --- END Installer Steps Definition ---


# --- Step Implementations ---

@installer_step("Prerequisite Checks")
def step_prereqs(progress, task_id):
    """Checks for root privileges, QCOW2 file (warns if missing), user/groups."""
    if os.geteuid() != 0:
        console.print("[bold red]Fatal Error:[/bold red] This script must be run with root privileges (e.g., using `sudo`).")
        logger.critical("Script not run as root. Aborting.")
        return False

    console.print(f"Checking for QCOW2 file at [cyan]{LOCAL_QCOW_PATH}[/cyan]...")
    logger.info(f"Checking for QCOW2 file: {LOCAL_QCOW_PATH}")
    if not LOCAL_QCOW_PATH.is_file():
        console.print(f"[yellow]Warning:[/yellow] QCOW2 file not found. Will attempt to create it in a later step.")
        logger.warning(f"QCOW2 file not found: {LOCAL_QCOW_PATH}. Will attempt creation after dependencies.")
    else:
        console.print(f"[green]✓[/green] QCOW2 file found.")

    if not check_user_exists(DEBIAN_USER):
         console.print(f"[bold red]Fatal Error:[/bold red] Required user '{DEBIAN_USER}' does not exist. Please create the user first.")
         logger.critical(f"User '{DEBIAN_USER}' not found. Aborting.")
         return False

    groups_to_check_create = [DEBIAN_GROUP, "disk"]
    for group_name in groups_to_check_create:
         if not check_group_exists(group_name):
             is_system_group = (group_name == "disk")
             console.print(f"[yellow]Warning:[/yellow] Group '{group_name}' not found. Creating...")
             logger.warning(f"Group '{group_name}' not found. Attempting creation.")
             groupadd_cmd = ['groupadd']
             if is_system_group:
                 groupadd_cmd.append('-r')
             groupadd_cmd.append(group_name)

             if run_command(groupadd_cmd, description=f"Creating group '{group_name}'"):
                 console.print(f"[green]✓[/green] Group '{group_name}' created.")
                 logger.info(f"Successfully created group '{group_name}'.")
             else:
                 console.print(f"[bold red]Error:[/bold red] Failed to create group '{group_name}'. This might cause issues later.")
                 logger.error(f"Failed to create group '{group_name}'.")
                 if group_name == "disk":
                      console.print("[bold red]Fatal Error:[/bold red] Failed to create essential 'disk' group. Cannot proceed.")
                      return False
                 console.print("[yellow]Continuing, but services using this group might fail.[/yellow]")

    logger.info("Prerequisite checks passed.")
    progress.update(task_id, advance=1)
    return True


@installer_step("Install Dependencies")
def step_install_deps(progress, task_id):
    """Updates apt, upgrades packages, installs required packages, and verifies key commands."""
    console.print("[cyan]Updating package lists (apt-get update)...[/cyan]")
    update_result = run_command(['apt-get', 'update', '-qq'], description="apt-get update", show_output=False)
    if not update_result:
        console.print("[bold yellow]Warning:[/bold yellow] 'apt-get update' failed. Check network and APT sources. Attempting to continue...")
        logger.warning("'apt-get update' failed. Proceeding cautiously.")

    console.print("[cyan]Upgrading existing packages (apt-get upgrade -y)...[/cyan]")
    upgrade_env = os.environ.copy()
    upgrade_env['DEBIAN_FRONTEND'] = 'noninteractive'
    # --- Removed console.status wrapper ---
    upgrade_result = run_command(['apt-get', 'upgrade', '-y'], description="apt-get upgrade", env=upgrade_env, show_output=False)
    if not upgrade_result:
        console.print("[bold yellow]Warning:[/bold yellow] 'apt-get upgrade' failed. System might not be fully up-to-date.")
        logger.warning("'apt-get upgrade' failed. Proceeding.")
    else:
        console.print("[green]✓[/green] Package upgrade completed.")

    console.print("[cyan]Installing required packages...[/cyan]")
    install_env = os.environ.copy()
    install_env['DEBIAN_FRONTEND'] = 'noninteractive'
    # --- Removed console.status wrapper ---
    install_result = run_command(['apt-get', 'install', '-y'] + REQUIRED_PACKAGES, description="apt-get install", env=install_env, show_output=False)

    if not install_result:
        console.print("[bold red]Error:[/bold red] Failed to install one or more required packages during initial attempt.")
        logger.error("Initial 'apt-get install' failed.")
        console.print("Attempting 'apt --fix-broken install' to resolve potential issues...")
        # --- Removed console.status wrapper ---
        fix_result = run_command(['apt-get', '--fix-broken', 'install', '-y'], description="apt --fix-broken install", env=install_env, show_output=False)

        if not fix_result:
             console.print("[bold red]Error:[/bold red] 'apt --fix-broken install' also failed. Unable to resolve dependencies.")
             logger.error("'apt --fix-broken install' failed.")
             return False

        console.print("Retrying package installation after fix attempt...")
        # --- Removed console.status wrapper ---
        install_result = run_command(['apt-get', 'install', '-y'] + REQUIRED_PACKAGES, description="apt-get install (retry)", env=install_env, show_output=False)

        if not install_result:
             console.print("[bold red]Fatal Error:[/bold red] Failed to install required packages even after attempting fix. Check APT logs and configuration.")
             logger.critical("Failed to install packages even after fix attempt. Aborting.")
             return False
        else:
             console.print("[green]✓[/green] Successfully installed packages after fix attempt.")
    else:
         console.print("[green]✓[/green] Required packages installed (or already present).")

    console.print("[cyan]Verifying key commands are available in PATH...[/cyan]")
    all_found = True
    missing_cmds = []
    for cmd in KEY_COMMANDS_TO_VALIDATE:
        logger.debug(f"Verifying command: {cmd}")
        cmd_path = shutil.which(cmd)
        if cmd_path is None:
            console.print(f"[bold red]✗ Error:[/bold red] Command '{cmd}' not found in PATH after installation.")
            logger.error(f"Verification failed: Command '{cmd}' not found in PATH.")
            missing_cmds.append(cmd)
            all_found = False
        else:
             logger.info(f"Verification success: Command '{cmd}' found at {cmd_path}")
             console.log(f"[green]✓[/green] Command '{cmd}' found at: [dim]{cmd_path}[/dim]")

    if not all_found:
         console.print(f"[bold red]Fatal Error:[/bold red] One or more essential commands are missing after installation: {missing_cmds}")
         console.print("This indicates a problem with package installation. Check APT logs (`/var/log/apt/term.log`) and network connection.")
         logger.critical(f"Essential commands missing after installation: {missing_cmds}. Aborting.")
         return False

    logger.info("Dependencies installed and verified successfully.")
    progress.update(task_id, advance=1)
    return True


@installer_step("Check/Create QCOW2 File")
def step_create_qcow(progress, task_id):
    """Checks for the QCOW2 file and creates it if missing and confirmed by user."""
    console.print(f"Verifying QCOW2 file existence: [cyan]{LOCAL_QCOW_PATH}[/cyan]")
    if LOCAL_QCOW_PATH.is_file():
        console.print(f"[green]✓[/green] QCOW2 file [cyan]{LOCAL_QCOW_PATH}[/cyan] already exists.")
        logger.info(f"QCOW2 file {LOCAL_QCOW_PATH} already exists.")
        progress.update(task_id, advance=1)
        return True

    console.print(f"[yellow]Warning:[/yellow] QCOW2 file [cyan]{LOCAL_QCOW_PATH}[/cyan] not found.")
    logger.warning(f"QCOW2 file {LOCAL_QCOW_PATH} not found at creation step.")

    if not Confirm.ask(f"Create a new [bold]{DEFAULT_QCOW_SIZE}[/bold] QCOW2 file at [cyan]{LOCAL_QCOW_PATH}[/cyan] now?", default=True):
        console.print("[bold red]Fatal Error:[/bold red] QCOW2 file creation aborted by user. Storage setup cannot proceed.")
        logger.critical("User aborted QCOW2 file creation. Aborting.")
        return False

    console.print(f"Creating {DEFAULT_QCOW_SIZE} QCOW2 file (this might take a moment)...")
    create_cmd = ['qemu-img', 'create', '-f', 'qcow2', str(LOCAL_QCOW_PATH), DEFAULT_QCOW_SIZE]
    # --- Removed console.status wrapper ---
    create_result = run_command(create_cmd, description=f"Creating {DEFAULT_QCOW_SIZE} QCOW2 file", show_output=True)

    if not create_result:
        console.print(f"[bold red]Fatal Error:[/bold red] Failed to create QCOW2 file at {LOCAL_QCOW_PATH} using qemu-img.")
        logger.critical(f"qemu-img create failed for {LOCAL_QCOW_PATH}")
        return False

    console.print(f"[green]✓[/green] Successfully created QCOW2 file: [cyan]{LOCAL_QCOW_PATH}[/cyan]")
    logger.info(f"Successfully created {DEFAULT_QCOW_SIZE} QCOW2 file at {LOCAL_QCOW_PATH}")

    console.print(f"Setting initial permissions (660, root:disk) for [cyan]{LOCAL_QCOW_PATH}[/cyan]...")
    try:
        if not check_group_exists("disk"):
             console.print("[bold red]Fatal Error:[/bold red] 'disk' group not found. Cannot set required permissions for QCOW2 file.")
             logger.critical("'disk' group missing during QCOW2 permission setting.")
             return False

        disk_gid = grp.getgrnam("disk").gr_gid
        os.chmod(LOCAL_QCOW_PATH, 0o660)
        os.chown(LOCAL_QCOW_PATH, 0, disk_gid)
        console.print("[green]✓[/green] Initial permissions set (root:disk, 660).")
        logger.info(f"Set initial permissions (660, root:disk) for {LOCAL_QCOW_PATH}")
        run_command(['ls', '-lh', str(LOCAL_QCOW_PATH)], description="Verifying file and permissions", show_output=True)

    except Exception as e:
        logger.exception(f"Failed to set initial permissions/ownership for newly created {LOCAL_QCOW_PATH}")
        console.print(f"[bold red]Fatal Error:[/bold red] Failed to set initial permissions for {LOCAL_QCOW_PATH}: {e}")
        return False

    progress.update(task_id, advance=1)
    return True


@installer_step("Set Timezone")
def step_set_timezone(progress, task_id):
    """Sets the system timezone."""
    timezone = "America/Los_Angeles"
    logger.info(f"Setting system timezone to {timezone}")
    if run_command(['timedatectl', 'set-timezone', timezone], description=f"Setting timezone to {timezone}"):
        run_command(['date'], description="Current date/time after timezone change", show_output=True)
        progress.update(task_id, advance=1)
        return True
    else:
         console.print(f"[bold yellow]Warning:[/bold yellow] Failed to set timezone to {timezone}. Check timedatectl.")
         logger.warning(f"Failed to set timezone using timedatectl set-timezone {timezone}")
         progress.update(task_id, advance=1)
         return True


@installer_step("Install/Configure ZeroTier")
def step_zerotier(progress, task_id):
    """Installs ZeroTier if needed, enables the service, and joins the specified network."""
    logger.info("Starting ZeroTier setup.")
    zt_check_result = shutil.which('zerotier-cli')
    if not zt_check_result:
        console.print("ZeroTier not found. [cyan]Installing ZeroTier via official script...[/cyan]")
        logger.info("zerotier-cli not found. Installing...")
        zt_install_cmd = "curl -s https://install.zerotier.com | bash"
        # --- Removed console.status wrapper ---
        install_result = run_command(zt_install_cmd, description="Downloading and running ZeroTier installer", shell=True, show_output=True)
        if not install_result:
            console.print("[bold red]Error:[/bold red] ZeroTier installation script failed.")
            logger.error("ZeroTier installation script failed.")
            return False
        console.print("[green]✓[/green] ZeroTier installed.")
        logger.info("ZeroTier installed successfully.")
    else:
        console.print(f"ZeroTier already installed ([dim]{zt_check_result}[/dim]).")
        logger.info(f"ZeroTier already installed at {zt_check_result}.")

    console.print("[cyan]Enabling and starting ZeroTier service (zerotier-one)...[/cyan]")
    enable_start_result = run_command(['systemctl', 'enable', '--now', 'zerotier-one'], description="Enabling and starting ZeroTier service")
    if not enable_start_result:
        status_result = run_command(['systemctl', 'is-active', '--quiet', 'zerotier-one'], check=False, description="Checking ZT service status")
        if not status_result or status_result.returncode != 0:
             console.print("[bold red]Error:[/bold red] Failed to enable or start ZeroTier service, and it's not active.")
             logger.error("Failed to enable/start zerotier-one and it's not active.")
             run_command(['journalctl', '-u', 'zerotier-one', '-n', '20', '--no-pager'], description="ZT service logs", show_output=True, check=False)
             return False
        else:
             console.print("[yellow]Warning:[/yellow] Enable/start command failed, but service seems active. Proceeding.")
             logger.warning("systemctl enable --now zerotier-one failed, but service is active.")
    else:
        console.print("[green]✓[/green] ZeroTier service enabled and started.")

    time.sleep(2)

    console.print(f"[cyan]Checking ZeroTier network status for [yellow]{ZT_NETWORK_ID}[/yellow]...[/cyan]")
    list_networks_result = run_command(['zerotier-cli', 'listnetworks'], description="Checking current networks", show_output=True)
    network_joined = False
    if list_networks_result and ZT_NETWORK_ID in list_networks_result.stdout:
         console.print(f"Already joined network [cyan]{ZT_NETWORK_ID}[/cyan].")
         logger.info(f"Already joined ZeroTier network {ZT_NETWORK_ID}.")
         network_joined = True

    if not network_joined:
         console.print(f"Joining ZeroTier Network [cyan]{ZT_NETWORK_ID}[/cyan]...")
         logger.info(f"Attempting to join ZeroTier network {ZT_NETWORK_ID}.")
         join_result = run_command(['zerotier-cli', 'join', ZT_NETWORK_ID], description="Joining network command")
         if not join_result:
              console.print(f"[bold yellow]Warning:[/bold yellow] ZeroTier join command failed for {ZT_NETWORK_ID}. May need authorization first.")
              logger.warning(f"zerotier-cli join {ZT_NETWORK_ID} command failed.")
         else:
              console.print(f"[green]✓[/green] Join request sent for network {ZT_NETWORK_ID}.")
              logger.info(f"Join request sent for network {ZT_NETWORK_ID}.")

         console.print(f"[bold yellow]Action Required:[/bold yellow] Authorize this device in ZeroTier Central for network [yellow]{ZT_NETWORK_ID}[/yellow].")
         time.sleep(3)

    run_command(['zerotier-cli', 'listnetworks'], description="Current ZeroTier Networks", show_output=True, check=False)
    run_command(['ip', '-brief', 'addr'], description="Current IP Addresses", show_output=True, check=False)

    logger.info("ZeroTier setup step finished.")
    progress.update(task_id, advance=1)
    return True


@installer_step("Prepare SSH Directory")
def step_ssh_prep(progress, task_id):
    """Ensures ~/.ssh directory exists with correct permissions for the target user."""
    logger.info(f"Starting SSH directory preparation for user {DEBIAN_USER}.")
    ssh_dir = Path(f"/home/{DEBIAN_USER}/.ssh")
    auth_keys_file = ssh_dir / "authorized_keys"

    console.print(f"Ensuring SSH directory [cyan]{ssh_dir}[/cyan] exists for user [yellow]{DEBIAN_USER}[/yellow] with 700 permissions...")
    mkdir_result = run_command(['mkdir', '-p', str(ssh_dir)], user=DEBIAN_USER, description="Creating .ssh directory")
    if not mkdir_result:
         logger.error(f"Failed to create directory {ssh_dir} as user {DEBIAN_USER}.")
         return False

    chmod_dir_result = run_command(['chmod', '700', str(ssh_dir)], user=DEBIAN_USER, description="Setting .ssh directory permissions")
    if not chmod_dir_result:
         logger.error(f"Failed to set permissions on {ssh_dir} as user {DEBIAN_USER}.")
         return False

    console.print(f"Ensuring authorized_keys file [cyan]{auth_keys_file}[/cyan] exists with 600 permissions...")
    touch_result = run_command(['touch', str(auth_keys_file)], user=DEBIAN_USER, description="Touching authorized_keys file")
    if not touch_result:
         logger.error(f"Failed to touch {auth_keys_file} as user {DEBIAN_USER}.")
         return False

    chmod_file_result = run_command(['chmod', '600', str(auth_keys_file)], user=DEBIAN_USER, description="Setting authorized_keys file permissions")
    if not chmod_file_result:
         logger.error(f"Failed to set permissions on {auth_keys_file} as user {DEBIAN_USER}.")
         return False

    console.print(f"[green]✓[/green] SSH directory and authorized_keys file prepared.")
    console.print(f"[bold yellow]Action Required:[/bold yellow] Add your public SSH key(s) to [cyan]{auth_keys_file}[/cyan]")
    logger.info(f"SSH directory preparation for {DEBIAN_USER} completed.")
    progress.update(task_id, advance=1)
    return True


@installer_step("Configure User Groups & Xorg Wrapper")
def step_groups_xorg(progress, task_id):
    """Adds target user to necessary groups and configures Xwrapper."""
    logger.info(f"Starting user group and Xwrapper configuration for {DEBIAN_USER}.")
    groups_to_add = ['input', 'video', 'tty', 'sudo', 'disk', DEBIAN_GROUP]
    console.print(f"Adding user [yellow]{DEBIAN_USER}[/yellow] to required groups: [cyan]{', '.join(groups_to_add)}[/cyan]...")

    usermod_result = run_command(['usermod', '-aG', ','.join(groups_to_add), DEBIAN_USER], description="Adding user to groups", check=False)
    if not usermod_result or usermod_result.returncode != 0:
        groups_check_result = run_command(['groups', DEBIAN_USER], description="Checking current groups")
        if groups_check_result:
            current_groups = set(groups_check_result.stdout.strip().split()[-1].split(':')[-1].split())
            logger.debug(f"Current groups for {DEBIAN_USER}: {current_groups}")
            missing_groups = [g for g in groups_to_add if g not in current_groups]
            if not missing_groups:
                console.print("[green]✓[/green] User already belongs to all required groups.")
                logger.info(f"User {DEBIAN_USER} already in required groups.")
            else:
                console.print(f"[bold yellow]Warning:[/bold yellow] 'usermod' command failed, and user is still missing groups: {missing_groups}. Check system logs.")
                logger.warning(f"usermod failed and user {DEBIAN_USER} is missing groups: {missing_groups}")
                if "sudo" in missing_groups or "disk" in missing_groups:
                    console.print(f"[bold red]Fatal Error:[/bold red] Failed to add user to critical group(s): {missing_groups}.")
                    return False
        else:
            console.print("[bold yellow]Warning:[/bold yellow] 'usermod' command failed, and could not verify current groups.")
            logger.warning(f"usermod failed for {DEBIAN_USER} and group verification failed.")
    else:
        console.print("[green]✓[/green] User added to groups (or already present).")
        logger.info(f"Successfully added {DEBIAN_USER} to groups {groups_to_add}.")
        run_command(['groups', DEBIAN_USER], description=f"Verifying groups for {DEBIAN_USER}", show_output=True)

    xwrapper_conf = Path("/etc/X11/Xwrapper.config")
    allowed_line = "allowed_users=anybody"
    console.print(f"Configuring Xorg session permissions in [cyan]{xwrapper_conf}[/cyan]...")
    logger.info(f"Configuring {xwrapper_conf} to set '{allowed_line}'.")
    needs_update = False
    content = ""
    try:
        if xwrapper_conf.exists():
            content = xwrapper_conf.read_text()
            line_found = any(line.strip() == allowed_line for line in content.splitlines() if line.strip() and not line.strip().startswith('#'))
            if not line_found:
                needs_update = True
                logger.info(f"'{allowed_line}' not found in existing {xwrapper_conf}.")
            else:
                 logger.info(f"'{allowed_line}' already present in {xwrapper_conf}.")
        else:
            needs_update = True
            logger.info(f"{xwrapper_conf} does not exist, creating.")

        if needs_update:
            console.print(f"Adding/Ensuring line '[yellow]{allowed_line}[/yellow]' in {xwrapper_conf}...")
            if content and not content.endswith('\n'):
                 content += "\n"
            content += f"{allowed_line}\n"
            if not write_file(xwrapper_conf, content, permissions="0644", show_content=False):
                 console.print(f"[bold red]Error:[/bold red] Failed to write updated {xwrapper_conf}.")
                 logger.error(f"Failed writing updated {xwrapper_conf}")
                 return False
            console.print(f"[green]✓[/green] {xwrapper_conf} updated.")
            logger.info(f"Successfully updated {xwrapper_conf}.")
        else:
             console.print(f"[green]✓[/green] Xwrapper config '{allowed_line}' already correctly set.")
             if xwrapper_conf.exists():
                  try:
                      os.chmod(xwrapper_conf, 0o644)
                  except OSError as e:
                      logger.warning(f"Could not ensure permissions on existing {xwrapper_conf}: {e}")

    except Exception as e:
         console.print(f"[bold red]Error:[/bold red] Failed during Xwrapper config read/write operation: {e}")
         logger.exception(f"Failed reading/writing Xwrapper config {xwrapper_conf}")
         return False

    logger.info("User group and Xwrapper configuration finished.")
    progress.update(task_id, advance=1)
    return True


@installer_step("Install Rust & 'just'")
def step_rust_just(progress, task_id):
    """Installs Rust via rustup and the 'just' command runner for the target user."""
    logger.info(f"Starting Rust and 'just' installation for user {DEBIAN_USER}.")
    cargo_path = Path(f"/home/{DEBIAN_USER}/.cargo/bin")
    profile_path = Path(f"/home/{DEBIAN_USER}/.profile")
    path_export_line = f'export PATH="{cargo_path}:$PATH"'

    console.print(f"Checking for Rust/Cargo installation for user [yellow]{DEBIAN_USER}[/yellow]...")
    cargo_check_cmd = f'command -v cargo'
    cargo_check_result = run_command(['bash', '-c', cargo_check_cmd], user=DEBIAN_USER, check=False, capture_output=True, description="Checking for cargo")
    cargo_exists = cargo_check_result is not None and cargo_check_result.returncode == 0

    if not cargo_exists:
        console.print("Rust (cargo) not found. [cyan]Installing Rust via rustup for user...[/cyan]")
        logger.info(f"Rust not found for user {DEBIAN_USER}. Installing via rustup.")
        rustup_cmd = "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --no-modify-path"
        # --- Removed console.status wrapper ---
        rustup_install_result = run_command(['bash', '-c', rustup_cmd], user=DEBIAN_USER, description="Running rustup installer", show_output=True)

        if not rustup_install_result:
            console.print("[bold red]Error:[/bold red] Rust installation via rustup failed.")
            logger.error(f"rustup installation failed for user {DEBIAN_USER}.")
            return False

        console.print(f"Adding Cargo bin directory to PATH in user's [cyan]{profile_path}[/cyan]...")
        logger.info(f"Attempting to add '{path_export_line}' to {profile_path} for user {DEBIAN_USER}.")
        try:
            if not run_command(['touch', str(profile_path)], user=DEBIAN_USER, description=f"Ensuring {profile_path} exists"):
                 logger.warning(f"Could not touch {profile_path} as user {DEBIAN_USER}.")

            grep_cmd = f"grep -qFx {shlex.quote(path_export_line)} {shlex.quote(str(profile_path))}"
            path_exists_result = run_command(['bash', '-c', grep_cmd], user=DEBIAN_USER, check=False, description="Checking if PATH export exists in .profile")

            if not path_exists_result or path_exists_result.returncode != 0:
                 append_cmd = f"echo {shlex.quote(path_export_line)} | tee -a {shlex.quote(str(profile_path))}"
                 append_result = run_command(['bash', '-c', append_cmd], user=DEBIAN_USER, description="Appending PATH to .profile")
                 if not append_result:
                      console.print(f"[bold red]Error:[/bold red] Failed to append PATH export to {profile_path}.")
                      logger.error(f"Failed to append PATH to {profile_path} for user {DEBIAN_USER}.")
                      return False
                 console.print("[green]✓[/green] Added PATH export to .profile.")
                 logger.info(f"Successfully added PATH export to {profile_path}.")
            else:
                 console.print("PATH export line already found in .profile.")
                 logger.info(f"PATH export line already exists in {profile_path}.")

        except Exception as e:
             console.print(f"[bold red]Error:[/bold red] Failed checking or modifying {profile_path}: {e}")
             logger.exception(f"Failed checking/modifying {profile_path} for user {DEBIAN_USER}.")
             return False

        console.print("[green]✓[/green] Rust installed and PATH configured in .profile.")
        logger.info(f"Rust successfully installed for {DEBIAN_USER}.")
    else:
        console.print("Rust (cargo) already installed for this user.")
        logger.info(f"Rust (cargo) already installed for user {DEBIAN_USER}.")

    console.print("Checking for 'just' command runner...")
    logger.info(f"Checking for 'just' for user {DEBIAN_USER}.")
    just_check_cmd = f'export PATH={shlex.quote(str(cargo_path))}:$PATH && command -v just'
    just_check_result = run_command(['bash', '-c', just_check_cmd], user=DEBIAN_USER, check=False, capture_output=True, description="Checking for just")
    just_exists = just_check_result is not None and just_check_result.returncode == 0

    if not just_exists:
        console.print("'just' not found. [cyan]Installing 'just' via cargo...[/cyan]")
        logger.info(f"'just' not found for user {DEBIAN_USER}. Installing via cargo.")
        just_install_cmd = f'export PATH={shlex.quote(str(cargo_path))}:$PATH && cargo install just'
        # --- Removed console.status wrapper ---
        install_just_result = run_command(['bash', '-c', just_install_cmd], user=DEBIAN_USER, description="Installing just via cargo", show_output=True)

        if not install_just_result:
            console.print("[bold red]Error:[/bold red] Failed to install 'just' using cargo.")
            logger.error(f"Failed to install 'just' for user {DEBIAN_USER} via cargo.")
            return False
        console.print("[green]✓[/green] 'just' installed successfully.")
        logger.info(f"'just' installed successfully for user {DEBIAN_USER}.")
    else:
        console.print("'just' command runner already installed.")
        logger.info(f"'just' already installed for user {DEBIAN_USER}.")

    logger.info("Rust and 'just' installation step finished.")
    progress.update(task_id, advance=1)
    return True


@installer_step("Join ZeroTier Network (Verification)")
def step_zt_join_verify(progress, task_id):
     """Verifies ZeroTier network status and reminds user to authorize."""
     logger.info("Verifying ZeroTier network join status.")
     console.print("[cyan]Verifying ZeroTier network status again...[/cyan]")
     run_command(['zerotier-cli', 'listnetworks'], description="Current ZeroTier Networks", show_output=True, check=False)
     run_command(['ip', '-brief', 'addr'], description="Current IP Addresses", show_output=True, check=False)
     console.print(f"[bold yellow]Reminder:[/bold yellow] Ensure this device is authorized on network [yellow]{ZT_NETWORK_ID}[/yellow] in your ZeroTier Central account (my.zerotier.com).")
     logger.info("ZeroTier verification step finished.")
     progress.update(task_id, advance=1)
     return True


@installer_step("Set/Verify QCOW2 Permissions")
def step_qcow_perms(progress, task_id):
    """Ensures the QCOW2 file has the correct permissions (660, root:disk)."""
    logger.info(f"Starting QCOW2 permission check/set for {LOCAL_QCOW_PATH}.")
    console.print(f"Ensuring correct permissions for [cyan]{LOCAL_QCOW_PATH}[/cyan] (Expected: 660, root:disk)...")

    if not LOCAL_QCOW_PATH.is_file():
         console.print(f"[bold red]Fatal Error:[/bold red] QCOW2 file {LOCAL_QCOW_PATH} not found at permission setting stage. Aborting.")
         logger.critical(f"QCOW2 file {LOCAL_QCOW_PATH} missing before setting permissions.")
         return False

    try:
        if not check_group_exists("disk"):
             console.print("[bold red]Fatal Error:[/bold red] Essential 'disk' group not found. Cannot set permissions.")
             logger.critical("'disk' group missing during QCOW2 permission setting.")
             return False

        disk_gid = grp.getgrnam("disk").gr_gid
        target_mode = 0o660
        target_uid = 0
        target_gid = disk_gid

        current_stat = LOCAL_QCOW_PATH.stat()
        current_mode = current_stat.st_mode & 0o777
        current_uid = current_stat.st_uid
        current_gid = current_stat.st_gid
        logger.debug(f"Current permissions for {LOCAL_QCOW_PATH}: {oct(current_mode)}, Owner: {current_uid}, Group: {current_gid}")

        needs_chmod = current_mode != target_mode
        if needs_chmod:
            os.chmod(LOCAL_QCOW_PATH, target_mode)
            console.print(f"  - Permissions set to [yellow]{oct(target_mode)}[/yellow].")
            logger.info(f"Set permissions {oct(target_mode)} for {LOCAL_QCOW_PATH}")
        else:
             console.print(f"  - Permissions already correct ({oct(target_mode)}).")

        needs_chown = (current_uid != target_uid) or (current_gid != target_gid)
        if needs_chown:
            os.chown(LOCAL_QCOW_PATH, target_uid, target_gid)
            console.print(f"  - Ownership set to [yellow]root:disk[/yellow].")
            logger.info(f"Set ownership to root:{target_gid} for {LOCAL_QCOW_PATH}")
        else:
             console.print(f"  - Ownership already correct (root:disk).")

        console.print("[green]✓[/green] QCOW2 Permissions verified/set.")
        if needs_chmod or needs_chown:
            run_command(['ls', '-lh', str(LOCAL_QCOW_PATH)], description="Verifying final permissions", show_output=True)

        logger.info(f"QCOW2 permission check/set finished for {LOCAL_QCOW_PATH}.")
        progress.update(task_id, advance=1)
        return True

    except Exception as e:
        logger.exception(f"Failed to set permissions/ownership for {LOCAL_QCOW_PATH}")
        console.print(f"[bold red]Fatal Error:[/bold red] Failed setting permissions for {LOCAL_QCOW_PATH}: {e}")
        return False


@installer_step("Define NBD Systemd Service")
def step_nbd_service(progress, task_id):
    """Creates the systemd service file for managing the QEMU NBD connection."""
    logger.info("Defining systemd service for QEMU NBD.")
    nbd_service_file = Path("/etc/systemd/system/qemu-nbd-connect.service")
    console.print(f"Defining NBD systemd service: [cyan]{nbd_service_file}[/cyan]")

    content = f"""[Unit]
Description=Connect QEMU NBD device {NBD_DEVICE} to {LOCAL_QCOW_PATH}
Documentation=man:qemu-nbd(8)
After=local-fs.target network-online.target systemd-modules-load.service
Wants=network-online.target
Before=lvm2-activation-early.service lvm2-activation.service lvm-activate-data-vg.service

[Service]
Type=simple
RemainAfterExit=yes
Restart=on-failure
RestartSec=5s
ExecStartPre=-/sbin/modprobe nbd nbds_max=16
ExecStartPre=-/usr/bin/qemu-nbd --disconnect {NBD_DEVICE}
ExecStart=/usr/bin/qemu-nbd --connect={NBD_DEVICE} --persistent {LOCAL_QCOW_PATH}
ExecStop=/usr/bin/qemu-nbd --disconnect {NBD_DEVICE}

[Install]
WantedBy=multi-user.target
"""
    if write_file(nbd_service_file, content, permissions="0644"):
        logger.info(f"Successfully wrote NBD systemd service file {nbd_service_file}.")
        progress.update(task_id, advance=1)
        return True
    else:
         logger.error(f"Failed to write NBD systemd service file {nbd_service_file}.")
         return False


@installer_step("Define LVM Activation Systemd Service")
def step_lvm_service(progress, task_id):
    """Creates the systemd service file for activating the LVM Volume Group."""
    logger.info(f"Defining systemd service for LVM activation ({VG_NAME}).")
    lvm_service_file = Path("/etc/systemd/system/lvm-activate-data-vg.service")
    console.print(f"Defining LVM activation systemd service: [cyan]{lvm_service_file}[/cyan]")

    content = f"""[Unit]
Description=Activate LVM Volume Group '{VG_NAME}' on NBD device {NBD_DEVICE}
Documentation=man:vgchange(8) man:lvchange(8)
Requires=qemu-nbd-connect.service
After=qemu-nbd-connect.service systemd-udev-settle.service
Before=local-fs.target remote-fs.target

[Service]
Type=oneshot
RemainAfterExit=yes
Environment="PATH=/usr/sbin:/usr/bin:/sbin:/bin"
ExecStartPre=/bin/sleep 1
ExecStartPre=/usr/bin/udevadm settle --timeout=30
ExecStartPre=/bin/bash -c 'tries=30; while [ $tries -gt 0 ]; do if [ -b {NBD_DEVICE} ]; then echo "NBD device {NBD_DEVICE} found. Testing read..."; if dd if={NBD_DEVICE} of=/dev/null bs=1k count=1 status=none; then echo "NBD Read OK."; exit 0; else echo "NBD Read FAILED."; sleep 0.5; fi; fi; echo "Waiting for {NBD_DEVICE}..."; sleep 1; tries=$((tries-1)); done; echo "NBD device {NBD_DEVICE} did not become ready/readable"; exit 1'
ExecStartPre=/usr/sbin/lvm pvscan --cache {NBD_DEVICE}
ExecStart=/usr/sbin/lvm vgchange -ay {VG_NAME}
ExecStartPost=/bin/bash -c 'tries=30; while ! [ -b {LV_DEVICE_PATH} ]; do echo "Waiting for LV {LV_DEVICE_PATH}..."; sleep 1; tries=$((tries-1)); if [ "$tries" -le 0 ]; then echo "LV node {LV_DEVICE_PATH} did not appear"; exit 1; fi; done; echo "LV node {LV_DEVICE_PATH} appeared."'
ExecStop=/usr/sbin/lvm vgchange -an {VG_NAME}

[Install]
WantedBy=multi-user.target
"""
    if write_file(lvm_service_file, content, permissions="0644"):
        logger.info(f"Successfully wrote LVM activation systemd service file {lvm_service_file}.")
        progress.update(task_id, advance=1)
        return True
    else:
        logger.error(f"Failed to write LVM activation systemd service file {lvm_service_file}.")
        return False


@installer_step("Configure LVM (Create if Needed)")
def step_lvm_setup(progress, task_id):
    """Checks if the LVM LV exists, performs first-time setup (PV, VG, LV, format) if not."""
    logger.info(f"Starting LVM configuration check/setup for {LV_DEVICE_PATH}.")
    console.print(f"Checking if LVM logical volume [cyan]{LV_DEVICE_PATH}[/cyan] exists...")

    if LV_DEVICE_PATH.is_block_device():
        console.print("[green]✓[/green] LVM LV already exists. Skipping creation.")
        logger.info(f"LVM LV {LV_DEVICE_PATH} already exists.")
        run_command(['lvm', 'vgchange', '-ay', VG_NAME], description="Ensuring VG is active", check=False)
        progress.update(task_id, advance=1)
        return True

    console.print("LVM LV not found. [cyan]Performing one-time LVM setup...[/cyan]")
    logger.info(f"LVM LV {LV_DEVICE_PATH} not found. Starting LVM creation process.")

    console.print("Reloading systemd daemon (required before starting transient services)...")
    if not run_command(['systemctl', 'daemon-reload'], description="Daemon reload"):
        logger.error("daemon-reload failed before LVM setup.")
        return False

    console.print(f"Temporarily starting NBD service ([green]qemu-nbd-connect.service[/green]) to access QCOW2 as [cyan]{NBD_DEVICE}[/cyan]...")
    start_nbd_result = run_command(['systemctl', 'start', 'qemu-nbd-connect.service'], description="Starting NBD service transiently")
    if not start_nbd_result:
         console.print(f"[bold red]Error:[/bold red] Failed to start NBD service ({'qemu-nbd-connect.service'}) for LVM setup.")
         logger.error("Failed to start qemu-nbd-connect.service transiently for LVM setup.")
         run_command(['journalctl', '-u', 'qemu-nbd-connect.service', '-n', '50', '--no-pager'], description="NBD service logs", show_output=True, check=False)
         return False
    logger.info("NBD service started transiently.")
    time.sleep(2)

    console.print(f"Checking if NBD device [cyan]{NBD_DEVICE}[/cyan] is ready...")
    nbd_check_cmd = f'[ -b {NBD_DEVICE} ] && dd if={NBD_DEVICE} of=/dev/null bs=1k count=1 status=none'
    nbd_ready_result = run_command(['bash', '-c', nbd_check_cmd], description="Checking NBD device readiness", check=False)
    if not nbd_ready_result or nbd_ready_result.returncode != 0:
        console.print(f"[bold red]Error:[/bold red] NBD device {NBD_DEVICE} did not become ready or readable after starting service.")
        logger.error(f"NBD device {NBD_DEVICE} check failed after transient start.")
        run_command(['lsblk'], description="Current block devices", show_output=True, check=False)
        run_command(['journalctl', '-u', 'qemu-nbd-connect.service', '-n', '50', '--no-pager'], description="NBD service logs", show_output=True, check=False)
        run_command(['systemctl', 'stop', 'qemu-nbd-connect.service'], description="Attempting NBD service stop", check=False)
        return False
    console.print(f"[green]✓[/green] NBD device {NBD_DEVICE} connected and readable.")
    logger.info(f"NBD device {NBD_DEVICE} check passed.")

    lvm_success = True
    # --- Removed console.status wrapper ---
    logger.info(f"Running pvcreate -f {NBD_DEVICE}")
    if not run_command(['pvcreate', '-f', NBD_DEVICE], description="Creating LVM PV (forced)"): lvm_success = False

    if lvm_success:
        logger.info(f"Running vgcreate {VG_NAME} {NBD_DEVICE}")
        if not run_command(['vgcreate', VG_NAME, NBD_DEVICE], description="Creating LVM VG"): lvm_success = False

    if lvm_success:
        logger.info(f"Running lvcreate -l 100%FREE -n {LV_NAME} {VG_NAME}")
        if not run_command(['lvcreate', '-l', '100%FREE', '-n', LV_NAME, VG_NAME], description="Creating LVM LV"): lvm_success = False

    if lvm_success:
        logger.info(f"Waiting for LV device node {LV_DEVICE_PATH} to appear...")
        node_appeared = False
        for attempt in range(15):
            if LV_DEVICE_PATH.is_block_device():
                node_appeared = True
                logger.info(f"LV device node {LV_DEVICE_PATH} appeared.")
                break
            time.sleep(1)
        if not node_appeared:
            console.print(f"[bold red]Error:[/bold red] LV device node [cyan]{LV_DEVICE_PATH}[/cyan] did not appear after creation.")
            logger.error(f"LV device node {LV_DEVICE_PATH} did not appear.")
            run_command(['lsblk'], description="Current block devices", show_output=True, check=False)
            lvm_success = False
        else:
             run_command(['udevadm', 'settle'], description="Settling udev", check=False)

    if lvm_success:
        logger.info(f"Formatting {LV_DEVICE_PATH} with ext4...")
        if not run_command(['mkfs.ext4', '-F', str(LV_DEVICE_PATH)], description="Formatting LV with ext4"): lvm_success = False

    console.print("Stopping temporary NBD service...")
    logger.info("Stopping transient NBD service.")
    run_command(['systemctl', 'stop', 'qemu-nbd-connect.service'], description="Stopping NBD service", check=False)
    time.sleep(2)

    if lvm_success:
        console.print("[green]✓[/green] LVM setup (PV, VG, LV, Format) successful.")
        logger.info("LVM one-time setup completed successfully.")
        progress.update(task_id, advance=1)
        return True
    else:
        console.print("[bold red]Fatal Error:[/bold red] LVM setup failed during PV/VG/LV creation or formatting.")
        logger.critical("LVM one-time setup failed.")
        console.print(f"Check LVM status manually (`lsblk`, `pvs`, `vgs`, `lvs`) and logs ({LOG_FILENAME}).")
        return False


@installer_step("Configure Mount Point & fstab")
def step_fstab(progress, task_id):
    """Creates the mount point, sets ownership, adds fstab entry, and attempts initial mount."""
    logger.info(f"Starting mount point and fstab configuration for {LVM_MOUNT_POINT}.")
    console.print(f"Ensuring mount point [cyan]{LVM_MOUNT_POINT}[/cyan] exists with correct ownership ([yellow]{DEBIAN_USER}:{DEBIAN_GROUP}[/yellow])...")

    try:
        LVM_MOUNT_POINT.mkdir(parents=True, exist_ok=True)
        logger.debug(f"Ensured directory {LVM_MOUNT_POINT} exists.")
        user_info = pwd.getpwnam(DEBIAN_USER)
        group_info = grp.getgrnam(DEBIAN_GROUP)
        target_uid = user_info.pw_uid
        target_gid = group_info.gr_gid
        os.chown(LVM_MOUNT_POINT, target_uid, target_gid)
        logger.info(f"Set ownership of {LVM_MOUNT_POINT} to {target_uid}:{target_gid} ({DEBIAN_USER}:{DEBIAN_GROUP}).")
        console.print(f"  - Ownership set to [yellow]{DEBIAN_USER}:{DEBIAN_GROUP}[/yellow].")

    except Exception as e:
        logger.exception(f"Failed to create or set ownership for mount point {LVM_MOUNT_POINT}")
        console.print(f"[bold red]Error:[/bold red] Failed configuring mount point directory {LVM_MOUNT_POINT}: {e}")
        return False

    fstab_file = Path("/etc/fstab")
    fstab_entry_line = f"{LV_DEVICE_PATH}   {LVM_MOUNT_POINT}   ext4   defaults,nofail,_netdev   0   2"
    fstab_comment_line = f"# Entry added by AVF installer for LVM data volume ({VG_NAME}/{LV_NAME})"
    console.print(f"Checking fstab entry for [cyan]{LVM_MOUNT_POINT}[/cyan] in [cyan]{fstab_file}[/cyan]...")
    logger.info(f"Checking {fstab_file} for entry: {fstab_entry_line}")

    try:
        content = fstab_file.read_text()
        entry_exists = False
        for line in content.splitlines():
            if line.strip().startswith('#'): continue
            parts = line.split()
            if len(parts) >= 2 and parts[0] == str(LV_DEVICE_PATH) and parts[1] == str(LVM_MOUNT_POINT):
                entry_exists = True
                logger.info(f"Found existing fstab entry: {line.strip()}")
                break
            if len(parts) >= 2 and parts[1] == str(LVM_MOUNT_POINT) and parts[0] != str(LV_DEVICE_PATH):
                 console.print(f"[bold yellow]Warning:[/bold yellow] Mount point {LVM_MOUNT_POINT} found in fstab but configured for a different device ({parts[0]})! Check {fstab_file}.")
                 logger.warning(f"fstab conflict: {LVM_MOUNT_POINT} used by different device {parts[0]}.")

        if not entry_exists:
            console.print("Adding fstab entry...")
            logger.info(f"Adding fstab entry: {fstab_entry_line}")
            if content and not content.endswith('\n'):
                content += "\n"
            new_content = content + f"{fstab_comment_line}\n{fstab_entry_line}\n"
            fstab_file.write_text(new_content)
            console.print("[green]✓[/green] fstab entry added.")
            logger.info("Successfully added fstab entry.")
        else:
            console.print("[green]✓[/green] fstab entry already seems to exist.")

        console.print(f"Attempting to mount [cyan]{LVM_MOUNT_POINT}[/cyan] using fstab configuration...")
        logger.info(f"Attempting to mount {LVM_MOUNT_POINT}.")
        vg_activate_result = run_command(['lvm', 'vgchange', '-ay', VG_NAME], description=f"Ensuring VG '{VG_NAME}' is active", check=False)
        if not vg_activate_result or vg_activate_result.returncode != 0:
             logger.warning(f"vgchange -ay {VG_NAME} failed or returned non-zero. Mount might fail if LV is inactive.")
             run_command(['lvs', f'{VG_NAME}/{LV_NAME}'], description="Checking LV status", check=False, show_output=True)

        mount_result = run_command(['mount', str(LVM_MOUNT_POINT)], description=f"Mounting {LVM_MOUNT_POINT}")
        if not mount_result:
             console.print(f"[bold red]Error:[/bold red] Failed to mount {LVM_MOUNT_POINT} after configuring fstab.")
             logger.error(f"Failed to mount {LVM_MOUNT_POINT}.")
             run_command(['mountpoint', '-q', str(LVM_MOUNT_POINT)], check=False, description="Checking mount status code")
             run_command(['lsblk', str(LV_DEVICE_PATH)], description=f"Block device info for {LV_DEVICE_PATH}", check=False, show_output=True)
             run_command(['tail', '-n', '5', str(fstab_file)], description=f"Last 5 lines of {fstab_file}", check=False, show_output=True)
             run_command(['dmesg', '|', 'tail', '-n', '20'], shell=True, description="Last 20 kernel messages", check=False, show_output=True)
             return False
        else:
             console.print(f"[green]✓[/green] Successfully mounted [cyan]{LVM_MOUNT_POINT}[/cyan].")
             logger.info(f"Successfully mounted {LVM_MOUNT_POINT}.")

        logger.info("Mount point and fstab configuration finished.")
        progress.update(task_id, advance=1)
        return True

    except Exception as e:
        logger.exception(f"Failed during fstab configuration or mount attempt for {LVM_MOUNT_POINT}")
        console.print(f"[bold red]Error:[/bold red] Failed during fstab/mount configuration: {e}")
        return False


@installer_step("Enable Storage Persistence Services")
def step_enable_storage_services(progress, task_id):
    """Reloads systemd daemon and enables NBD and LVM activation services for boot."""
    logger.info("Enabling storage persistence services (NBD, LVM activation).")
    console.print("[cyan]Reloading systemd daemon (to recognize new/modified service units)...[/cyan]")
    if not run_command(['systemctl', 'daemon-reload'], description="Daemon reload"):
        logger.error("daemon-reload failed before enabling services.")
        console.print("[yellow]Warning:[/yellow] systemctl daemon-reload failed. Service enablement might not work correctly.")

    console.print("[cyan]Enabling NBD ([green]qemu-nbd-connect.service[/green]) and LVM activation ([green]lvm-activate-data-vg.service[/green]) services for boot...[/cyan]")
    nbd_enabled_ok = run_command(['systemctl', 'enable', 'qemu-nbd-connect.service'], description="Enabling NBD service")
    lvm_enabled_ok = run_command(['systemctl', 'enable', 'lvm-activate-data-vg.service'], description="Enabling LVM activation service")

    if nbd_enabled_ok and lvm_enabled_ok:
        console.print("[green]✓[/green] Storage persistence services enabled for boot.")
        logger.info("NBD and LVM activation services enabled successfully.")
        progress.update(task_id, advance=1)
        return True
    else:
        if not nbd_enabled_ok: logger.error("Failed to enable qemu-nbd-connect.service.")
        if not lvm_enabled_ok: logger.error("Failed to enable lvm-activate-data-vg.service.")
        console.print("[bold red]Error:[/bold red] Failed to enable one or both storage services. Check systemctl status and journalctl for details.")
        run_command(['systemctl', 'status', 'qemu-nbd-connect.service', 'lvm-activate-data-vg.service', '--no-pager'], description="Storage service status", check=False, show_output=True)
        return False


@installer_step("Install Brave Browser")
def step_install_brave(progress, task_id):
    """Installs Brave Browser from its official APT repository."""
    logger.info("Starting Brave Browser installation step.")
    brave_path = shutil.which('brave-browser')
    if brave_path:
          console.print(f"Brave Browser already installed ([dim]{brave_path}[/dim]). Skipping installation.")
          logger.info(f"Brave Browser already installed at {brave_path}.")
          progress.update(task_id, advance=1)
          return True

    console.print("[cyan]Installing Brave Browser (adding repository and package)...[/cyan]")
    logger.info("Brave Browser not found. Proceeding with installation.")
    # --- Removed console.status wrapper ---
    keyring_dir = Path("/etc/apt/keyrings")
    keyring_file = keyring_dir / "brave-browser-archive-keyring.gpg"
    sources_file = Path("/etc/apt/sources.list.d/brave-browser-release.list")
    key_url = "https://brave-browser-apt-release.s3.brave.com/brave-browser-archive-keyring.gpg"

    arch_result = run_command(['dpkg', '--print-architecture'], description="Getting system architecture")
    if not arch_result:
         console.print("[bold red]Error:[/bold red] Could not determine system architecture using dpkg.")
         logger.error("Failed to determine system architecture.")
         return False
    arch = arch_result.stdout.strip()
    logger.info(f"System architecture detected as: {arch}")

    repo_line = f"deb [arch={arch} signed-by={keyring_file}] https://brave-browser-apt-release.s3.brave.com/ stable main"
    logger.debug(f"Brave repository line: {repo_line}")

    success = True
    try:
         keyring_dir.mkdir(mode=0o755, parents=True, exist_ok=True)
         logger.debug(f"Ensured keyring directory exists: {keyring_dir}")
    except Exception as e:
         console.print(f"[bold red]Error:[/bold red] Failed creating keyring directory {keyring_dir}: {e}")
         logger.exception(f"Failed creating keyring directory {keyring_dir}")
         success = False

    if success:
        curl_cmd = ['curl', '-fsSLo', str(keyring_file), key_url]
        if not run_command(curl_cmd, description="Downloading Brave GPG key"):
            logger.error(f"Failed to download Brave GPG key from {key_url}")
            success = False

    if success:
        if not write_file(sources_file, repo_line + "\n", permissions="0644", show_content=True):
            logger.error(f"Failed to write Brave sources file {sources_file}")
            success = False

    if success:
        if not run_command(['apt-get', 'update', '-qq'], description="apt update after adding Brave repo", show_output=False):
            logger.error("apt-get update failed after adding Brave repository.")
            success = False

    if success:
        install_env = os.environ.copy()
        install_env['DEBIAN_FRONTEND'] = 'noninteractive'
        if not run_command(['apt-get', 'install', '-y', 'brave-browser'], description="Installing brave-browser package", env=install_env, show_output=False):
            logger.error("Failed to install brave-browser package.")
            success = False

    if success:
        brave_path_final = shutil.which('brave-browser')
        if brave_path_final:
            console.print(f"[green]✓[/green] Brave Browser installed successfully ([dim]{brave_path_final}[/dim]).")
            logger.info(f"Brave Browser installed successfully at {brave_path_final}.")
            progress.update(task_id, advance=1)
            return True
        else:
             console.print("[bold red]Error:[/bold red] Brave installation commands seemed successful, but 'brave-browser' command is still not found.")
             logger.error("Brave installation reported success, but verification failed.")
             return False
    else:
        console.print("[bold red]Error:[/bold red] Failed during Brave Browser installation process.")
        logger.error("Brave Browser installation process failed.")
        console.print("[cyan]Attempting to clean up Brave repository files...[/cyan]")
        keyring_file.unlink(missing_ok=True)
        sources_file.unlink(missing_ok=True)
        logger.info("Attempted cleanup of Brave repository files.")
        console.print("Consider running 'sudo apt-get update && sudo apt-get --fix-broken install -y' manually.")
        return False


@installer_step("Setup VNC (xstartup & systemd)")
def step_setup_vnc(progress, task_id):
    """Configures the VNC server xstartup script and systemd service."""
    logger.info(f"Starting VNC setup for user {DEBIAN_USER} on display {VNC_DISPLAY}.")
    console.print(f"Configuring VNC xstartup script: [cyan]{VNC_XSTARTUP_PATH}[/cyan]...")

    xstartup_content = f"""#!/bin/sh
unset SESSION_MANAGER
unset DBUS_SESSION_BUS_ADDRESS
export XDG_SESSION_TYPE=x11
export XDG_CURRENT_DESKTOP=GNOME
export GNOME_SHELL_SESSION_MODE=debian
[ -r $HOME/.Xresources ] && xrdb $HOME/.Xresources
gnome-session &
"""
    if not write_file(VNC_XSTARTUP_PATH, xstartup_content, owner=DEBIAN_USER, group=DEBIAN_GROUP, permissions="0755"):
        console.print("[bold red]Error:[/bold red] Failed to write VNC xstartup script.")
        logger.error(f"Failed writing VNC xstartup script {VNC_XSTARTUP_PATH}")
        return False
    console.print("[green]✓[/green] VNC xstartup script configured.")
    logger.info(f"VNC xstartup script {VNC_XSTARTUP_PATH} configured successfully.")

    vnc_service_file = Path(f"/etc/systemd/system/vncserver@.service")
    console.print(f"Defining VNC systemd service file: [cyan]{vnc_service_file}[/cyan]")
    try:
        vnc_user_info = pwd.getpwnam(DEBIAN_USER)
        vnc_group_info = grp.getgrnam(DEBIAN_GROUP)
        logger.debug(f"Using UID {vnc_user_info.pw_uid}, GID {vnc_group_info.gr_gid} for VNC service.")
    except KeyError as e:
        console.print(f"[bold red]Error:[/bold red] Cannot find VNC user '{DEBIAN_USER}' or group '{DEBIAN_GROUP}': {e}")
        logger.critical(f"VNC user '{DEBIAN_USER}' or group '{DEBIAN_GROUP}' not found.")
        return False

    vnc_service_content = f"""[Unit]
Description=TigerVNC per-display remote desktop service for user {DEBIAN_USER}
Documentation=man:vncserver(1) man:Xvnc(1)
After=syslog.target network-online.target graphical.target lvm-activate-data-vg.service
Wants=network-online.target

[Service]
Type=forking
User={DEBIAN_USER}
Group={DEBIAN_GROUP}
WorkingDirectory=/home/{DEBIAN_USER}
ExecStart=/usr/bin/vncserver :%i -fg \\
    -desktop DebianGNOMEonVNC \\
    -geometry {VNC_GEOMETRY} \\
    -depth {VNC_DEPTH} \\
    -localhost no \\
    -alwaysshared \\
    -SecurityTypes VncAuth,TLSVnc \\
    -xstartup {VNC_XSTARTUP_PATH}
PIDFile=/home/{DEBIAN_USER}/.vnc/%H:%i.pid
ExecStop=/usr/bin/vncserver -kill :%i

[Install]
WantedBy=multi-user.target
"""
    if not write_file(vnc_service_file, vnc_service_content, permissions="0644"):
        console.print("[bold red]Error:[/bold red] Failed to write VNC systemd service file.")
        logger.error(f"Failed writing VNC systemd service file {vnc_service_file}")
        return False
    console.print("[green]✓[/green] VNC systemd service file defined.")
    logger.info(f"VNC systemd service file {vnc_service_file} defined successfully.")

    console.print("[cyan]Reloading systemd daemon...[/cyan]")
    if not run_command(['systemctl', 'daemon-reload'], description="Daemon reload"):
        console.print("[yellow]Warning:[/yellow] systemctl daemon-reload failed. Service enablement might require manual reload.")
        logger.warning("daemon-reload failed after writing VNC service file.")

    vnc_instance_service = f"vncserver@{VNC_DISPLAY_NUM}.service"
    console.print(f"Enabling VNC service instance [cyan]{vnc_instance_service}[/cyan] for boot...")
    logger.info(f"Enabling VNC service instance {vnc_instance_service}.")
    if not run_command(['systemctl', 'enable', vnc_instance_service], description=f"Enabling {vnc_instance_service}"):
        console.print(f"[bold red]Error:[/bold red] Failed to enable VNC service instance {vnc_instance_service}.")
        logger.error(f"Failed to enable VNC service instance {vnc_instance_service}.")
        run_command(['systemctl', 'status', vnc_instance_service, '--no-pager'], description="VNC service status", check=False, show_output=True)
        return False

    console.print(f"[green]✓[/green] VNC service ({vnc_instance_service}) configured and enabled.")
    console.print(f"[bold yellow]Action Required:[/bold yellow] Set VNC password for user '{DEBIAN_USER}' before starting the service:")
    console.print(f"  Run: [white on black] sudo -u {DEBIAN_USER} vncpasswd [/white on black]")
    logger.info("VNC setup step finished successfully.")
    progress.update(task_id, advance=1)
    return True


@installer_step("Configure Samba Server")
def step_configure_samba(progress, task_id):
    """Configures Samba server for sharing the LVM data volume."""
    logger.info(f"Starting Samba configuration for share '{SAMBA_SHARE_NAME}' -> {SAMBA_SHARE_PATH}")
    smb_conf_file = Path("/etc/samba/smb.conf")
    console.print(f"Configuring Samba server share '[{SAMBA_SHARE_NAME}]' -> [cyan]{SAMBA_SHARE_PATH}[/cyan] in [cyan]{smb_conf_file}[/cyan]...")

    if smb_conf_file.exists():
         backup_file = smb_conf_file.with_suffix(f".bak-{current_timestamp}")
         try:
              shutil.move(str(smb_conf_file), str(backup_file))
              console.print(f"Backed up existing smb.conf to [cyan]{backup_file}[/cyan]")
              logger.info(f"Backed up {smb_conf_file} to {backup_file}")
         except Exception as e:
              console.print(f"[bold yellow]Warning:[/bold yellow] Could not back up {smb_conf_file}: {e}")
              logger.warning(f"Could not back up {smb_conf_file}: {e}")

    share_path_obj = Path(SAMBA_SHARE_PATH)
    if not share_path_obj.is_dir():
         console.print(f"[bold red]Error:[/bold red] Samba share path '{SAMBA_SHARE_PATH}' does not exist or is not a directory. Ensure LVM volume is mounted.")
         logger.error(f"Samba share path {SAMBA_SHARE_PATH} is not a valid directory.")
         return False

    smb_conf_content = f"""# Samba configuration generated by AVF installer ({current_timestamp})
[global]
   workgroup = WORKGROUP
   server string = %h AVF VM Share (Samba)
   netbios name = debian-avf-vm
   security = user
   map to guest = Bad User
   encrypt passwords = yes
   log file = /var/log/samba/log.%m
   max log size = 1000
   logging = file
   server role = standalone server

[${SAMBA_SHARE_NAME}]
   comment = Shared Data Volume ({SAMBA_SHARE_PATH})
   path = {SAMBA_SHARE_PATH}
   browseable = yes
   read only = no
   guest ok = yes
   force user = {DEBIAN_USER}
   force group = {DEBIAN_GROUP}
   create mask = 0664
   directory mask = 0775
"""
    if not write_file(smb_conf_file, smb_conf_content, permissions="0644"):
        console.print("[bold red]Error:[/bold red] Failed to write Samba configuration file.")
        logger.error(f"Failed writing Samba configuration {smb_conf_file}")
        return False
    console.print("[green]✓[/green] Samba configuration file written.")
    logger.info(f"Samba configuration {smb_conf_file} written successfully.")

    console.print("[cyan]Verifying Samba configuration using 'testparm'...[/cyan]")
    testparm_result = run_command(['testparm', '-s'], description="Running testparm", show_output=True)
    if not testparm_result:
        console.print("[bold red]Error:[/bold red] 'testparm' reported critical errors in the Samba configuration. Check output above.")
        logger.error("'testparm' reported critical errors.")
        return False
    if testparm_result and "Load smb config files from" not in testparm_result.stdout:
         console.print("[yellow]Warning:[/yellow] 'testparm' output seems unusual. Configuration might have non-critical issues.")
         logger.warning("'testparm' output format unexpected.")

    console.print("[cyan]Enabling and restarting Samba services (smbd, nmbd)...[/cyan]")
    logger.info("Enabling and restarting Samba services.")
    enable_start_result = run_command(['systemctl', 'enable', '--now', 'smbd', 'nmbd'], description="Enabling and starting smbd/nmbd")
    if not enable_start_result:
         console.print("[yellow]Warning:[/yellow] 'enable --now' failed for Samba services, attempting explicit restart...")
         logger.warning("'systemctl enable --now smbd nmbd' failed. Attempting restart.")
         restart_result = run_command(['systemctl', 'restart', 'smbd', 'nmbd'], description="Restarting smbd/nmbd")
         if not restart_result:
              console.print("[bold red]Error:[/bold red] Failed to enable or restart Samba services.")
              logger.error("Failed to enable or restart Samba services.")
              run_command(['systemctl', 'status', 'smbd', 'nmbd', '--no-pager'], description="Samba service status", check=False, show_output=True)
              return False

    time.sleep(3)

    smbd_active = run_command(['systemctl', 'is-active', '--quiet', 'smbd'], check=False).returncode == 0
    nmbd_active = run_command(['systemctl', 'is-active', '--quiet', 'nmbd'], check=False).returncode == 0
    logger.info(f"Samba service status check: smbd active = {smbd_active}, nmbd active = {nmbd_active}")
    if smbd_active and nmbd_active:
        console.print("[green]✓[/green] Samba services (smbd, nmbd) configured and are active.")
    else:
        failed_services = []
        if not smbd_active: failed_services.append('smbd')
        if not nmbd_active: failed_services.append('nmbd')
        console.print(f"[bold yellow]Warning:[/bold yellow] Samba configuration applied, but service(s) [{', '.join(failed_services)}] are not active. Check status and logs.")
        logger.warning(f"Samba service(s) not active after configuration: {failed_services}")
        run_command(['systemctl', 'status', 'smbd', 'nmbd', '--no-pager'], description="Samba service status", check=False, show_output=True)

    logger.info("Samba configuration step finished.")
    progress.update(task_id, advance=1)
    return True


@installer_step("Setup Podman")
def step_setup_podman(progress, task_id):
    """Configures subordinate UIDs/GIDs, enables linger, and sets rootful storage path."""
    logger.info(f"Starting Podman setup (subids, linger, rootful storage).")
    sub_uid_start = 100000
    sub_gid_start = 100000
    sub_id_count = 65536
    sub_uid_file = Path("/etc/subuid")
    sub_gid_file = Path("/etc/subgid")

    console.print(f"Configuring subordinate UIDs/GIDs for rootless user [yellow]{DEBIAN_USER}[/yellow]...")
    logger.info(f"Configuring subuids/subgids for {DEBIAN_USER} in {sub_uid_file}, {sub_gid_file}.")
    try:
        sub_uid_content = sub_uid_file.read_text() if sub_uid_file.exists() else ""
        if f"{DEBIAN_USER}:" not in sub_uid_content:
            sub_uid_entry = f"{DEBIAN_USER}:{sub_uid_start}:{sub_id_count}"
            logger.info(f"Adding subuid entry: {sub_uid_entry}")
            with open(sub_uid_file, "a") as f:
                f.write(f"{sub_uid_entry}\n")
            console.print(f"  - Added subuid entry to {sub_uid_file}.")
        else:
             logger.info(f"Subuid entry for {DEBIAN_USER} already exists in {sub_uid_file}.")
             console.print(f"  - Subuid entry already exists for {DEBIAN_USER}.")

        sub_gid_content = sub_gid_file.read_text() if sub_gid_file.exists() else ""
        if f"{DEBIAN_USER}:" not in sub_gid_content:
            sub_gid_entry = f"{DEBIAN_USER}:{sub_gid_start}:{sub_id_count}"
            logger.info(f"Adding subgid entry: {sub_gid_entry}")
            with open(sub_gid_file, "a") as f:
                f.write(f"{sub_gid_entry}\n")
            console.print(f"  - Added subgid entry to {sub_gid_file}.")
        else:
             logger.info(f"Subgid entry for {DEBIAN_USER} already exists in {sub_gid_file}.")
             console.print(f"  - Subgid entry already exists for {DEBIAN_USER}.")

        console.print("[green]✓[/green] Subordinate IDs configured.")
        logger.info("Subordinate ID configuration finished.")

    except Exception as e:
         console.print(f"[bold yellow]Warning:[/bold yellow] Could not automatically configure /etc/subuid or /etc/subgid: {e}")
         console.print("  Rootless Podman might not work correctly. Manual configuration may be needed.")
         logger.warning(f"Error configuring subid files: {e}", exc_info=True)

    console.print(f"Enabling session lingering for user [yellow]{DEBIAN_USER}[/yellow]...")
    logger.info(f"Enabling linger for user {DEBIAN_USER}.")
    linger_result = run_command(['loginctl', 'enable-linger', DEBIAN_USER], description="Enable linger", check=False)
    if not linger_result or linger_result.returncode != 0:
        linger_status_cmd = ['loginctl', 'show-user', DEBIAN_USER, '-p', 'Linger', '--value']
        status_result = run_command(linger_status_cmd, description="Checking linger status", check=False, capture_output=True)
        if status_result and status_result.stdout.strip() == "yes":
             console.print("  - Linger already enabled.")
             logger.info(f"Linger was already enabled for {DEBIAN_USER}.")
        else:
             console.print(f"[bold yellow]Warning:[/bold yellow] Failed to enable linger for {DEBIAN_USER}. Rootless containers might stop on logout.")
             logger.warning(f"Failed to enable linger for {DEBIAN_USER}, and status check failed or shows 'no'.")
    else:
         console.print("[green]✓[/green] Session lingering enabled.")
         logger.info(f"Successfully enabled linger for {DEBIAN_USER}.")

    console.print(f"Configuring rootful Podman storage location -> [cyan]{PODMAN_ROOTFUL_STORAGE_PATH}[/cyan]...")
    storage_conf_file = Path("/etc/containers/storage.conf")
    storage_driver = "overlay"
    logger.info(f"Configuring Podman rootful storage in {storage_conf_file} using driver '{storage_driver}' and path {PODMAN_ROOTFUL_STORAGE_PATH}.")

    if storage_conf_file.exists():
         backup_file = storage_conf_file.with_suffix(f".bak-{current_timestamp}")
         try:
              shutil.move(str(storage_conf_file), str(backup_file))
              console.print(f"Backed up existing storage.conf to [cyan]{backup_file}[/cyan]")
              logger.info(f"Backed up {storage_conf_file} to {backup_file}")
         except Exception as e:
              console.print(f"[bold yellow]Warning:[/bold yellow] Could not back up {storage_conf_file}: {e}")
              logger.warning(f"Could not back up {storage_conf_file}: {e}")

    mount_check = run_command(['mountpoint', '-q', str(LVM_MOUNT_POINT)], check=False, description=f"Checking if {LVM_MOUNT_POINT} is mounted")
    if not mount_check or mount_check.returncode != 0:
         console.print(f"[bold red]Error:[/bold red] Mount point {LVM_MOUNT_POINT} (parent of Podman storage) is not mounted. Cannot configure storage there.")
         logger.error(f"Mount point {LVM_MOUNT_POINT} not mounted. Cannot configure Podman storage.")
         return False

    try:
         PODMAN_ROOTFUL_STORAGE_PATH.mkdir(parents=True, exist_ok=True)
         os.chown(PODMAN_ROOTFUL_STORAGE_PATH, 0, 0)
         logger.info(f"Ensured Podman rootful storage directory exists: {PODMAN_ROOTFUL_STORAGE_PATH} (owned by root:root).")
         console.print(f"  - Ensured directory exists: [cyan]{PODMAN_ROOTFUL_STORAGE_PATH}[/cyan]")
    except Exception as e:
         console.print(f"[bold red]Error:[/bold red] Failed to create Podman storage directory {PODMAN_ROOTFUL_STORAGE_PATH}: {e}")
         logger.exception(f"Failed to create Podman storage dir {PODMAN_ROOTFUL_STORAGE_PATH}")
         return False

    storage_conf_content = f"""# Podman storage configuration (/etc/containers/storage.conf)
# Managed by AVF installer script ({current_timestamp})

[storage]
driver = "{storage_driver}"
graphroot = "{PODMAN_ROOTFUL_STORAGE_PATH}"
runroot = "/run/containers/storage"

[storage.options]
# size = ""

[storage.options.{storage_driver}]
# ignore_chown_errors = "false"
# mount_program = "/usr/bin/fuse-overlayfs"
# mount_opt = "nodev,metacopy=on"

"""
    if not write_file(storage_conf_file, storage_conf_content, permissions="0644"):
        console.print("[bold red]Error:[/bold red] Failed to write Podman storage configuration file.")
        logger.error(f"Failed writing Podman storage configuration {storage_conf_file}")
        return False

    console.print(f"[green]✓[/green] Podman rootful storage configured in [cyan]{storage_conf_file}[/cyan].")
    console.print(f"  Rootless users (like '{DEBIAN_USER}') will use their home directory by default.")
    logger.info("Podman setup step finished successfully.")
    progress.update(task_id, advance=1)
    return True


@installer_step("Final Cleanup")
def step_cleanup(progress, task_id):
    """Performs final cleanup tasks like clearing apt cache."""
    logger.info("Starting final cleanup step.")
    console.print("[cyan]Cleaning up APT package cache...[/cyan]")
    clean_env = os.environ.copy()
    clean_env['DEBIAN_FRONTEND'] = 'noninteractive'
    if run_command(['apt-get', 'clean'], env=clean_env, description="apt-get clean"):
        console.print("[green]✓[/green] APT cache cleaned.")
        logger.info("APT cache cleaned successfully.")
    else:
        console.print("[yellow]Warning:[/yellow] 'apt-get clean' command failed.")
        logger.warning("'apt-get clean' failed.")

    logger.info("Final cleanup step finished.")
    progress.update(task_id, advance=1)
    return True


# --- Main Execution Logic ---
def main():
    """Main function to orchestrate the installation process."""
    start_time = datetime.datetime.now()
    console.print(Panel(
        Text("🚀 Ultimate AVF Debian Interactive Setup 🚀", justify="center", style="bold cyan"),
        title="Welcome!",
        subtitle=f"Started: {start_time.strftime('%Y-%m-%d %H:%M:%S %Z')}",
        border_style="blue"
    ))
    console.print(f"Logging detailed output to: [dim]{LOG_FILENAME}[/dim]")
    logger.info(f"Installer script started at {start_time}")
    logger.info(f"Effective UID: {os.geteuid()}, Effective GID: {os.getegid()}")
    logger.info(f"Script path: {__file__}")
    logger.info(f"Python version: {sys.version}")

    if not Confirm.ask(f"\nThis script will perform system setup steps including:\n"
                      f" - Installing packages ({len(REQUIRED_PACKAGES)} specified)\n"
                      f" - Configuring storage ([cyan]{LOCAL_QCOW_PATH}[/cyan] -> [cyan]{NBD_DEVICE}[/cyan] -> LVM -> [cyan]{LVM_MOUNT_POINT}[/cyan])\n"
                      f" - Setting up services (ZeroTier, SSH, VNC, Samba, Podman)\n"
                      f" - Target User: [yellow]{DEBIAN_USER}[/yellow]\n\n"
                      f"[bold]Please review the script and ensure backups before proceeding.[/bold]\n\n"
                      f"Proceed with installation?", default=False):
        console.print("\n[yellow]Installation aborted by user at initial prompt.[/yellow]")
        logger.warning("Installation aborted by user at initial prompt.")
        sys.exit(1)

    total_steps = len(installer_steps)
    console.print(f"\n[bold green]Starting installation process ({total_steps} steps)...[/bold green]")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    ) as progress:
        overall_task = progress.add_task("[bold green]Overall Progress[/bold green]", total=total_steps)
        all_steps_successful = True

        for i, step_info in enumerate(installer_steps):
            step_title = step_info['title']
            step_func = step_info['func']
            step_number = i + 1
            task_description = f"Step {step_number}/{total_steps}: {step_title}"
            step_task = progress.add_task(task_description, total=1, start=False, visible=True)

            console.print(Rule(f"[bold cyan]Starting: {step_title}[/bold cyan] ({step_number}/{total_steps})"))
            logger.info(f"Starting step ({step_number}/{total_steps}): {step_title}")
            progress.start_task(step_task)

            step_success = False
            try:
                step_success = step_func(progress, step_task)
            except Exception as step_exception:
                 logger.exception(f"Critical error occurred within step: {step_title}")
                 console.print(f"[bold red]Fatal Error:[/bold red] Unexpected error during step '{step_title}':")
                 console.print_exception(show_locals=False, word_wrap=True)
                 step_success = False

            if step_success:
                progress.update(step_task, completed=1, description=f"[green]✓ {task_description}[/green]")
                progress.update(overall_task, advance=1)
                logger.info(f"Successfully completed step: {step_title}")
                console.print(Rule(f"[bold green]Finished: {step_title}[/bold green]"))
            else:
                progress.update(step_task, description=f"[bold red]✗ Failed: {task_description}[/bold red]")
                progress.stop_task(step_task)
                console.print(Panel(
                    f"[bold red]Error during step: '{step_title}'.[/bold red]\nInstallation cannot continue.\nPlease check the output above and logs for details:\n[dim]{LOG_FILENAME}[/dim]",
                    title="Installation Failed",
                    border_style="red",
                    expand=False
                ))
                logger.critical(f"Failed step: {step_title}. Aborting installation.")
                all_steps_successful = False
                progress.update(overall_task, description="[bold red]Overall Progress (Failed)[/bold red]")
                progress.stop()
                break

            time.sleep(0.5)

        if not all_steps_successful:
             try:
                  html_log = f"installer_error_console_{current_timestamp}.html"
                  console.save_html(html_log)
                  console.print(f"\n[yellow]Tip:[/yellow] Detailed console output saved to [dim]'{html_log}'[/dim] for review.")
             except Exception as save_err:
                  logger.warning(f"Could not save console HTML log on failure: {save_err}")
             sys.exit(1)

    end_time = datetime.datetime.now()
    duration = end_time - start_time
    console.print(Panel(
        Text("✅ Installation Completed Successfully! ✅", justify="center", style="bold green"),
        title="Finished!",
        subtitle=f"Completed: {end_time.strftime('%Y-%m-%d %H:%M:%S %Z')} (Duration: {str(duration).split('.')[0]})",
        border_style="green",
        expand=False
    ))
    logger.info(f"Installation completed successfully at {end_time}. Duration: {duration}")

    console.print(Rule("[bold yellow]Post-Installation Actions & Reminders[/bold yellow]"))
    console.print(f"- [key] SSH Key:[/key] Ensure your public SSH key is added to [cyan]/home/{DEBIAN_USER}/.ssh/authorized_keys[/cyan]")
    console.print(f"- [key] VNC Password:[/key] Set the VNC password for user [yellow]{DEBIAN_USER}[/yellow]: Run [white on black] sudo -u {DEBIAN_USER} vncpasswd [/white on black]")
    console.print(f"- [network] ZeroTier Auth:[/network] Authorize this device in ZeroTier Central ([dim]my.zerotier.com[/dim]) for network [cyan]{ZT_NETWORK_ID}[/cyan].")
    console.print(f"- [desktop] VNC Service:[/desktop] Status: [white on black] sudo systemctl status vncserver@{VNC_DISPLAY_NUM}.service [/white on black]. Start/Stop: [white on black] ... start ... [/white on black] / [white on black] ... stop ... [/white on black]")
    console.print(f"- [storage] LVM Volume:[/storage] Mounted at [cyan]{LVM_MOUNT_POINT}[/cyan]. Check with [white on black] df -h {LVM_MOUNT_POINT} [/white on black].")
    console.print(f"- [storage] Podman Storage:[/storage] Rootful storage path: [cyan]{PODMAN_ROOTFUL_STORAGE_PATH}[/cyan].")
    console.print(f"- [docker] Podman Test:[/docker] Login as [yellow]{DEBIAN_USER}[/yellow] (SSH/VNC) & run [white on black] podman info [/white on black] / [white on black] podman run hello-world [/white on black].")
    console.print("- [system] Reboot:[/system] A [bold]reboot[/bold] is recommended to ensure all services start correctly: [white on black] sudo reboot [/white on black]")
    console.print(f"- [log] Log File:[/log] Detailed installation logs are in [dim]{LOG_FILENAME}[/dim].")
    console.print(Rule())

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[bold yellow]\nInstallation interrupted by user (Ctrl+C).[/bold yellow]")
        logger.warning("Installation interrupted by user (KeyboardInterrupt).")
        sys.exit(130)
    except Exception as e:
         console.print(f"\n[bold red]An unexpected critical error occurred outside of step execution:[/bold red]")
         logger.critical("Unexpected critical error during main execution.", exc_info=True)
         console.print_exception(show_locals=False, word_wrap=True)
         console.print(f"\nPlease check the log file for details: [dim]{LOG_FILENAME}[/dim]")
         sys.exit(2)
