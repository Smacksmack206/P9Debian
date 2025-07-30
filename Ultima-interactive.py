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
import argparse # <--- Import argparse

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
ZT_NETWORK_ID = "INSERT Zerotier Network ID" # Example ZeroTier Network ID
VNC_DISPLAY_NUM = "1"
VNC_DISPLAY = f":{VNC_DISPLAY_NUM}"
VNC_GEOMETRY = "2424x1080" # Example geometry, adjust as needed
VNC_DEPTH = "24" # Example depth, adjust as needed
VNC_XSTARTUP_PATH = Path(f"/home/{DEBIAN_USER}/.vnc/xstartup")
SAMBA_SHARE_NAME = "DataShare"
SAMBA_SHARE_PATH = str(LVM_MOUNT_POINT) # Samba config needs string
# PODMAN_ROOTFUL_STORAGE_PATH = LVM_MOUNT_POINT / "podman_storage" # Original - removed

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
    # Enhanced packages for new features
    "qemu-user-static", "binfmt-support", "qemu-system-x86", # x86 emulation support
    "tasksel", "aptitude", "apt-file", "deborphan", "localepurge", # Package management tools
    "zsh", "bash-completion", # Shell enhancements
    "lsb-release", # For Docker installation
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
    # Enhanced commands for new features
    "docker", # Will be installed separately
    "qemu-x86_64-static",
    "tasksel",
    "aptitude",
    "update-binfmts",
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

def run_command(command, description="Running command", check=True, shell=False, capture_output=True, text=True, user=None, cwd=None, env=None, show_output=False, timeout=None):
    """
    Runs a command using subprocess.run, logs execution details, and handles errors including timeout.
    Uses sudo -u USER -H -- command for running as another user.
    Returns the subprocess.CompletedProcess object on success (return code 0), None on failure or timeout.
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
    sensitive_desc = "password" in description.lower()
    console.log(f"{log_prefix}{description}: [dim]{'(command hidden)' if sensitive_desc else cmd_str_display}[/dim]")

    full_env = os.environ.copy()
    if user:
        try:
            pw_info = pwd.getpwnam(user)
            full_env['HOME'] = pw_info.pw_dir
            full_env['USER'] = user
            full_env['LOGNAME'] = user
            # Ensure XDG_RUNTIME_DIR is set if the user has one (important for podman rootless)
            xdg_runtime_dir = f"/run/user/{pw_info.pw_uid}"
            if Path(xdg_runtime_dir).is_dir():
                 full_env['XDG_RUNTIME_DIR'] = xdg_runtime_dir
                 logger.debug(f"Setting XDG_RUNTIME_DIR={xdg_runtime_dir} for user {user}")

            sudo_prefix = ['sudo', '-u', user, '-H', '--'] # Using -H to set HOME

            # Prepend sudo prefix and ensure PATH is reasonable
            # We might need to explicitly pass PATH if sudo resets it too much
            # sudo_prefix.extend(['env', f'PATH={full_env.get("PATH", os.defpath)}']) # More robust? Maybe too complex.

            if isinstance(cmd_to_run, list):
                 cmd_to_run = sudo_prefix + cmd_to_run
            else:
                 logger.warning("Running shell=True command as different user via sudo is complex. Prefer list-based commands.")
                 cmd_to_run = ' '.join(sudo_prefix) + ' ' + cmd_to_run
                 shell = True # Must use shell if original was string
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
            timeout=timeout
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
             # Only raise CalledProcessError if check=True was intended (which it defaults to)
             if check:
                 # Raise the error to be caught below, simplifying error handling logic
                 result.check_returncode()
             else:
                 # If check=False, failure is not exceptional, just return None
                 console.print(f"[yellow]Command Failed (Code: {result.returncode}, check=False):[/yellow] [dim]{'(command hidden)' if sensitive_desc else cmd_str_display}[/dim]")
                 if result.stderr: console.print(f"[yellow]Stderr:[/yellow] {result.stderr.strip()}")
                 if result.stdout: console.print(f"[dim]Stdout:[/dim] {result.stdout.strip()}")
                 logger.warning(f"Command failed with return code {result.returncode} (check=False): {'(command hidden)' if sensitive_desc else cmd_str_display}")
                 return None # Return None for non-zero exit when check=False

    except subprocess.TimeoutExpired as e:
        logger.error(f"Command timed out after {timeout} seconds: {'(command hidden)' if sensitive_desc else cmd_str_display}")
        stdout_cap = e.stdout.strip() if e.stdout and isinstance(e.stdout, str) else "(no stdout captured or not text)"
        stderr_cap = e.stderr.strip() if e.stderr and isinstance(e.stderr, str) else "(no stderr captured or not text)"
        logger.error(f"Timeout Stdout: {stdout_cap}")
        logger.error(f"Timeout Stderr: {stderr_cap}")
        console.print(f"[bold red]Error:[/bold red] Command timed out after {timeout} seconds: [dim]{'(command hidden)' if sensitive_desc else cmd_str_display}[/dim]")
        if e.stderr: console.print(f"[yellow]Timeout Stderr:[/yellow] {stderr_cap}")
        if e.stdout: console.print(f"[dim]Timeout Stdout:[/dim] {stdout_cap}")
        return None
    except subprocess.CalledProcessError as e:
        # This block now handles failures when check=True
        logger.error(f"Command failed: {'(command hidden)' if sensitive_desc else cmd_str_display}", exc_info=False)
        logger.error(f"Return code: {e.returncode}")
        stdout_cap = e.stdout.strip() if e.stdout and isinstance(e.stdout, str) else "(no stdout captured or not text)"
        stderr_cap = e.stderr.strip() if e.stderr and isinstance(e.stderr, str) else "(no stderr captured or not text)"
        if e.stdout: logger.error(f"Stdout:\n{stdout_cap}")
        if e.stderr: logger.error(f"Stderr:\n{stderr_cap}")
        console.print(f"[bold red]Error:[/bold red] Command failed (Code: {e.returncode}): [dim]{'(command hidden)' if sensitive_desc else cmd_str_display}[/dim]")
        if e.stderr: console.print(f"[yellow]Stderr:[/yellow] {stderr_cap}")
        if e.stdout: console.print(f"[dim]Stdout:[/dim] {stdout_cap}")
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
    Assumes this function is run with sufficient privileges (e.g., root)
    to create files and change ownership/permissions.
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

        # Set Permissions FIRST (before ownership potentially restricts root)
        if permissions:
            try:
                octal_perm = int(permissions, 8)
                os.chmod(path, octal_perm)
                logger.info(f"Set permissions {permissions} for {path}")
                console.log(f"  - Permissions set to [yellow]{permissions}[/yellow]")
            except ValueError:
                logger.error(f"Invalid permission format '{permissions}'. Should be octal string e.g., '0644'.")
                console.print(f"[bold red]Error:[/bold red] Invalid permission format '{permissions}'.")
                # Attempt to remove potentially wrongly permissioned file? Or just fail? Fail for safety.
                try: path.unlink()
                except OSError: pass
                return False
            except OSError as chmod_err:
                 logger.error(f"Failed to set permissions {permissions} for {path}: {chmod_err}")
                 console.print(f"[bold red]Error:[/bold red] Failed to set permissions {permissions} for {path}: {chmod_err}")
                 try: path.unlink()
                 except OSError: pass
                 return False


        # Set Ownership LAST
        if owner or group:
            uid = -1
            gid = -1
            owner_str = owner or '(current)'
            group_str = group or '(current)'

            try:
                if owner:
                    uid = pwd.getpwnam(owner).pw_uid
                if group:
                    gid = grp.getgrnam(group).gr_gid

                if uid != -1 or gid != -1:
                    os.chown(path, uid, gid)
                    logger.info(f"Set owner={owner_str}({uid}), group={group_str}({gid}) for {path}")
                    console.log(f"  - Ownership set to [yellow]{owner_str}:{group_str}[/yellow]")

            except KeyError as e:
                 logger.error(f"Owner '{owner}' or group '{group}' not found: {e}")
                 console.print(f"[bold red]Error:[/bold red] Owner '{owner}' or group '{group}' not found. Cannot set ownership.")
                 try: path.unlink()
                 except OSError: pass
                 return False
            except Exception as chown_err:
                logger.error(f"Failed to set ownership {owner}:{group} for {path}: {chown_err}")
                console.print(f"[bold red]Error:[/bold red] Failed to set ownership for {path}: {chown_err}")
                try: path.unlink()
                except OSError: pass
                return False

        return True

    except Exception as e:
        logger.exception(f"Failed to write or configure file {path}")
        console.print(f"[bold red]Error:[/bold red] Failed writing/configuring file {path}: {e}")
        # Attempt cleanup
        try: path.unlink()
        except OSError: pass
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


# --- Step Implementations (Ensure order reflects dependencies) ---

# All step functions now accept 'args' as the last parameter
@installer_step("Prerequisite Checks")
def step_prereqs(progress, task_id, args):
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
def step_install_deps(progress, task_id, args):
    """Updates apt, upgrades packages, installs required packages, and verifies key commands."""
    console.print("[cyan]Updating package lists (apt-get update)...[/cyan]")
    update_result = run_command(['apt-get', 'update', '-qq'], description="apt-get update", show_output=False)
    if not update_result:
        console.print("[bold yellow]Warning:[/bold yellow] 'apt-get update' failed. Check network and APT sources. Attempting to continue...")
        logger.warning("'apt-get update' failed. Proceeding cautiously.")

    console.print("[cyan]Upgrading existing packages (apt-get upgrade -y)...[/cyan]")
    upgrade_env = os.environ.copy()
    upgrade_env['DEBIAN_FRONTEND'] = 'noninteractive'
    upgrade_result = run_command(['apt-get', 'upgrade', '-y'], description="apt-get upgrade", env=upgrade_env, show_output=False)
    if not upgrade_result:
        console.print("[bold yellow]Warning:[/bold yellow] 'apt-get upgrade' failed. System might not be fully up-to-date.")
        logger.warning("'apt-get upgrade' failed. Proceeding.")
    else:
        console.print("[green]✓[/green] Package upgrade completed.")

    console.print("[cyan]Installing required packages...[/cyan]")
    install_env = os.environ.copy()
    install_env['DEBIAN_FRONTEND'] = 'noninteractive'
    install_result = run_command(['apt-get', 'install', '-y'] + REQUIRED_PACKAGES, description="apt-get install", env=install_env, show_output=False)

    if not install_result:
        console.print("[bold red]Error:[/bold red] Failed to install one or more required packages during initial attempt.")
        logger.error("Initial 'apt-get install' failed.")
        console.print("Attempting 'apt --fix-broken install' to resolve potential issues...")
        fix_result = run_command(['apt-get', '--fix-broken', 'install', '-y'], description="apt --fix-broken install", env=install_env, show_output=False)

        if not fix_result:
             console.print("[bold red]Error:[/bold red] 'apt --fix-broken install' also failed. Unable to resolve dependencies.")
             logger.error("'apt --fix-broken install' failed.")
             return False

        console.print("Retrying package installation after fix attempt...")
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
def step_create_qcow(progress, task_id, args): # Added args parameter
    """Checks for the QCOW2 file and creates it if missing and confirmed by user."""
    console.print(f"Verifying QCOW2 file existence: [cyan]{LOCAL_QCOW_PATH}[/cyan]")
    if LOCAL_QCOW_PATH.is_file():
        console.print(f"[green]✓[/green] QCOW2 file [cyan]{LOCAL_QCOW_PATH}[/cyan] already exists.")
        logger.info(f"QCOW2 file {LOCAL_QCOW_PATH} already exists.")
        progress.update(task_id, advance=1)
        return True

    console.print(f"[yellow]Warning:[/yellow] QCOW2 file [cyan]{LOCAL_QCOW_PATH}[/cyan] not found.")
    logger.warning(f"QCOW2 file {LOCAL_QCOW_PATH} not found at creation step.")

    # --- Modified Confirmation ---
    create_confirmed = False
    if args.non_interactive:
        console.print(f"[yellow]Non-interactive mode: Assuming 'yes' to create {DEFAULT_QCOW_SIZE} QCOW2 file.[/yellow]")
        logger.info(f"Non-interactive mode: Creating QCOW2 file {LOCAL_QCOW_PATH}")
        create_confirmed = True
    else:
        # Only ask if interactive
        create_confirmed = Confirm.ask(f"Create a new [bold]{DEFAULT_QCOW_SIZE}[/bold] QCOW2 file at [cyan]{LOCAL_QCOW_PATH}[/cyan] now?", default=True)
    # --- End Modified Confirmation ---

    if not create_confirmed:
        console.print("[bold red]Fatal Error:[/bold red] QCOW2 file creation aborted by user/script. Cannot proceed.")
        logger.error("QCOW2 file creation aborted.")
        return False

    console.print(f"Creating {DEFAULT_QCOW_SIZE} QCOW2 file (this might take a moment)...")
    create_cmd = ['qemu-img', 'create', '-f', 'qcow2', str(LOCAL_QCOW_PATH), DEFAULT_QCOW_SIZE]
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
        # Attempt cleanup of potentially unusable file
        try: LOCAL_QCOW_PATH.unlink()
        except OSError: pass
        return False

    progress.update(task_id, advance=1)
    return True


@installer_step("Set Timezone")
def step_set_timezone(progress, task_id, args): # Added args
    """Sets the system timezone."""
    timezone = "America/Los_Angeles" # TODO: Consider making this configurable or auto-detect
    logger.info(f"Setting system timezone to {timezone}")
    if run_command(['timedatectl', 'set-timezone', timezone], description=f"Setting timezone to {timezone}"):
        run_command(['date'], description="Current date/time after timezone change", show_output=True)
        progress.update(task_id, advance=1)
        return True
    else:
         console.print(f"[bold yellow]Warning:[/bold yellow] Failed to set timezone to {timezone}. Check timedatectl.")
         logger.warning(f"Failed to set timezone using timedatectl set-timezone {timezone}")
         progress.update(task_id, advance=1) # Don't fail the whole install for timezone
         return True # Continue installation


@installer_step("Install/Configure ZeroTier")
def step_zerotier(progress, task_id, args): # Added args
    """Installs ZeroTier if needed, enables the service, and joins the specified network."""
    logger.info("Starting ZeroTier setup.")
    zt_check_result = shutil.which('zerotier-cli')
    if not zt_check_result:
        console.print("ZeroTier not found. [cyan]Installing ZeroTier via official script...[/cyan]")
        logger.info("zerotier-cli not found. Installing...")
        zt_install_cmd = "curl -s https://install.zerotier.com | bash"
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

    time.sleep(2) # Give service time to fully initialize

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
              # Join often shows an error initially if not authorized, but might still succeed later. Don't fail here.
              console.print(f"[bold yellow]Warning/Info:[/bold yellow] ZeroTier join command failed or returned non-zero. This is OK if the node just needs authorization.")
              logger.warning(f"zerotier-cli join {ZT_NETWORK_ID} command failed (might need authorization).")
         else:
              console.print(f"[green]✓[/green] Join request sent for network {ZT_NETWORK_ID}.")
              logger.info(f"Join request sent for network {ZT_NETWORK_ID}.")

         console.print(f"[bold yellow]Action Required:[/bold yellow] Authorize this device in ZeroTier Central for network [yellow]{ZT_NETWORK_ID}[/yellow].")
         time.sleep(3) # Pause to let user read

    run_command(['zerotier-cli', 'listnetworks'], description="Current ZeroTier Networks", show_output=True, check=False)
    run_command(['ip', '-brief', 'addr'], description="Current IP Addresses", show_output=True, check=False)

    logger.info("ZeroTier setup step finished.")
    progress.update(task_id, advance=1)
    return True


@installer_step("Prepare SSH Directory")
def step_ssh_prep(progress, task_id, args): # Added args
    """Ensures ~/.ssh directory exists with correct permissions for the target user."""
    logger.info(f"Starting SSH directory preparation for user {DEBIAN_USER}.")
    ssh_dir = Path(f"/home/{DEBIAN_USER}/.ssh")
    auth_keys_file = ssh_dir / "authorized_keys"

    console.print(f"Ensuring SSH directory [cyan]{ssh_dir}[/cyan] exists for user [yellow]{DEBIAN_USER}[/yellow] with 700 permissions...")
    # Create directory AS THE USER to ensure correct ownership from the start
    mkdir_result = run_command(['mkdir', '-p', str(ssh_dir)], user=DEBIAN_USER, description="Creating .ssh directory")
    if not mkdir_result:
        logger.error(f"Failed to create directory {ssh_dir} as user {DEBIAN_USER}.")
        # Check if dir exists but failed for other reason (e.g., perms)
        if not ssh_dir.is_dir():
             console.print(f"[bold red]Error:[/bold red] Could not create SSH directory {ssh_dir}")
             return False
        else:
             console.print(f"[yellow]Warning:[/yellow] mkdir failed, but {ssh_dir} exists. Proceeding to check permissions.")


    # Set permissions AS THE USER
    chmod_dir_result = run_command(['chmod', '700', str(ssh_dir)], user=DEBIAN_USER, description="Setting .ssh directory permissions")
    if not chmod_dir_result:
        # Verify permissions manually if command failed
        try:
             current_mode = ssh_dir.stat().st_mode & 0o777
             if current_mode == 0o700:
                 console.print(f"[yellow]Warning:[/yellow] chmod command failed, but permissions on {ssh_dir} seem correct (700). Continuing.")
                 logger.warning(f"chmod 700 on {ssh_dir} failed, but mode is already 700.")
             else:
                 console.print(f"[bold red]Error:[/bold red] Failed to set 700 permissions on {ssh_dir} (current: {oct(current_mode)}).")
                 logger.error(f"Failed to set permissions on {ssh_dir} as user {DEBIAN_USER}. Current mode: {oct(current_mode)}.")
                 return False
        except Exception as stat_err:
             console.print(f"[bold red]Error:[/bold red] Failed to set 700 permissions on {ssh_dir} and could not verify existing permissions: {stat_err}")
             logger.error(f"Failed to set permissions on {ssh_dir} as user {DEBIAN_USER} and stat failed: {stat_err}.")
             return False

    console.print(f"Ensuring authorized_keys file [cyan]{auth_keys_file}[/cyan] exists with 600 permissions...")
    # Touch file AS THE USER
    touch_result = run_command(['touch', str(auth_keys_file)], user=DEBIAN_USER, description="Touching authorized_keys file")
    if not touch_result:
        # Check if file exists but failed for other reason
        if not auth_keys_file.is_file():
             console.print(f"[bold red]Error:[/bold red] Could not create authorized_keys file {auth_keys_file}")
             logger.error(f"Failed to touch {auth_keys_file} as user {DEBIAN_USER}.")
             return False
        else:
            console.print(f"[yellow]Warning:[/yellow] touch failed, but {auth_keys_file} exists. Proceeding to check permissions.")


    # Set permissions AS THE USER
    chmod_file_result = run_command(['chmod', '600', str(auth_keys_file)], user=DEBIAN_USER, description="Setting authorized_keys file permissions")
    if not chmod_file_result:
         # Verify permissions manually if command failed
        try:
             current_mode = auth_keys_file.stat().st_mode & 0o777
             if current_mode == 0o600:
                 console.print(f"[yellow]Warning:[/yellow] chmod command failed, but permissions on {auth_keys_file} seem correct (600). Continuing.")
                 logger.warning(f"chmod 600 on {auth_keys_file} failed, but mode is already 600.")
             else:
                 console.print(f"[bold red]Error:[/bold red] Failed to set 600 permissions on {auth_keys_file} (current: {oct(current_mode)}).")
                 logger.error(f"Failed to set permissions on {auth_keys_file} as user {DEBIAN_USER}. Current mode: {oct(current_mode)}.")
                 return False
        except Exception as stat_err:
             console.print(f"[bold red]Error:[/bold red] Failed to set 600 permissions on {auth_keys_file} and could not verify existing permissions: {stat_err}")
             logger.error(f"Failed to set permissions on {auth_keys_file} as user {DEBIAN_USER} and stat failed: {stat_err}.")
             return False


    console.print(f"[green]✓[/green] SSH directory and authorized_keys file prepared.")
    console.print(f"[bold yellow]Action Required:[/bold yellow] Add your public SSH key(s) to [cyan]{auth_keys_file}[/cyan]")
    logger.info(f"SSH directory preparation for {DEBIAN_USER} completed.")
    progress.update(task_id, advance=1)
    return True


@installer_step("Configure User Groups & Xorg Wrapper")
def step_groups_xorg(progress, task_id, args): # Added args
    """Adds target user to necessary groups and configures Xwrapper."""
    logger.info(f"Starting user group and Xwrapper configuration for {DEBIAN_USER}.")
    groups_to_add = ['input', 'video', 'tty', 'sudo', 'disk', DEBIAN_GROUP] # DEBIAN_GROUP is 'users' by default
    console.print(f"Adding user [yellow]{DEBIAN_USER}[/yellow] to required groups: [cyan]{', '.join(groups_to_add)}[/cyan]...")

    # Ensure all groups actually exist before adding the user
    missing_system_groups = []
    for group_name in groups_to_add:
        if not check_group_exists(group_name):
            console.print(f"[bold red]Error:[/bold red] Required group '{group_name}' does not exist. Cannot add user.")
            logger.error(f"Prerequisite group '{group_name}' missing.")
            missing_system_groups.append(group_name)
    if missing_system_groups:
        # This should ideally not happen if prereq step ran correctly, but check defensively.
        return False


    usermod_result = run_command(['usermod', '-aG', ','.join(groups_to_add), DEBIAN_USER], description="Adding user to groups", check=False) # Don't fail immediately if usermod returns non-zero

    # Verify group membership after running usermod
    groups_check_result = run_command(['groups', DEBIAN_USER], description="Checking final groups")
    groups_successfully_added = True
    if groups_check_result:
        current_groups = set(groups_check_result.stdout.strip().split(':')[-1].strip().split())
        logger.debug(f"Current groups for {DEBIAN_USER} after usermod: {current_groups}")
        missing_groups = [g for g in groups_to_add if g not in current_groups]
        if not missing_groups:
            console.print("[green]✓[/green] User belongs to all required groups.")
            logger.info(f"User {DEBIAN_USER} successfully verified in required groups.")
        else:
            console.print(f"[bold yellow]Warning:[/bold yellow] User is still missing required groups after usermod: {missing_groups}. Check system logs.")
            logger.warning(f"User {DEBIAN_USER} is missing groups after usermod: {missing_groups}")
            if "sudo" in missing_groups or "disk" in missing_groups:
                 console.print(f"[bold red]Fatal Error:[/bold red] Failed to add user to critical group(s): {missing_groups}. Cannot proceed.")
                 groups_successfully_added = False # Fail the step
            # Otherwise, just warn and continue
    else:
        console.print("[bold yellow]Warning:[/bold yellow] Could not verify user groups after 'usermod'. Check manually.")
        logger.warning(f"Could not verify groups for {DEBIAN_USER} after usermod failed or command error.")
        # Let's not fail the whole install for verification failure, but log it.

    if not groups_successfully_added:
        return False # Exit if critical groups were missing

    xwrapper_conf = Path("/etc/X11/Xwrapper.config")
    allowed_line = "allowed_users=anybody"
    needs_anybody_line = True # Assume we need it unless found otherwise

    console.print(f"Configuring Xorg session permissions in [cyan]{xwrapper_conf}[/cyan]...")
    logger.info(f"Configuring {xwrapper_conf} to ensure '{allowed_line}' is set.")

    current_content_lines = []
    if xwrapper_conf.is_file():
        try:
            current_content_lines = xwrapper_conf.read_text().splitlines()
            found = False
            for line in current_content_lines:
                if line.strip() == allowed_line:
                    found = True
                    needs_anybody_line = False
                    logger.info(f"'{allowed_line}' already present in {xwrapper_conf}.")
                    break
                elif line.strip().startswith("allowed_users="):
                     # If a different allowed_users line exists, we should warn or decide policy
                     console.print(f"[yellow]Warning:[/yellow] Found existing but different '{line.strip()}' in {xwrapper_conf}. Keeping existing setting.")
                     logger.warning(f"Found existing '{line.strip()}' in {xwrapper_conf}. Not adding '{allowed_line}'.")
                     needs_anybody_line = False # Don't overwrite existing setting
                     break
            if found:
                console.print(f"[green]✓[/green] Xwrapper config '{allowed_line}' already correctly set.")

        except Exception as e:
            console.print(f"[bold red]Error:[/bold red] Failed reading existing {xwrapper_conf}: {e}")
            logger.exception(f"Failed reading {xwrapper_conf}")
            return False # Cannot safely modify if read fails
    else:
        logger.info(f"{xwrapper_conf} does not exist. Will create with '{allowed_line}'.")


    if needs_anybody_line:
        console.print(f"Adding/Ensuring line '[yellow]{allowed_line}[/yellow]' in {xwrapper_conf}...")
        # Create new content or append to existing
        new_content = "\n".join(current_content_lines)
        if new_content and not new_content.endswith('\n'):
            new_content += "\n"
        new_content += f"{allowed_line}\n"

        if not write_file(xwrapper_conf, new_content, permissions="0644", show_content=False): # Don't show full file content
            console.print(f"[bold red]Error:[/bold red] Failed to write updated {xwrapper_conf}.")
            logger.error(f"Failed writing updated {xwrapper_conf}")
            return False
        console.print(f"[green]✓[/green] {xwrapper_conf} updated.")
        logger.info(f"Successfully updated {xwrapper_conf} with '{allowed_line}'.")
    elif xwrapper_conf.is_file(): # If line wasn't needed but file exists, ensure perms
         try:
              os.chmod(xwrapper_conf, 0o644)
         except OSError as e:
              logger.warning(f"Could not ensure permissions on existing {xwrapper_conf}: {e}")


    logger.info("User group and Xwrapper configuration finished.")
    progress.update(task_id, advance=1)
    return True


@installer_step("Install Rust & 'just'")
def step_rust_just(progress, task_id, args): # Added args
    """Installs Rust via rustup and the 'just' command runner for the target user."""
    logger.info(f"Starting Rust and 'just' installation for user {DEBIAN_USER}.")
    # Get user's home dynamically
    try:
        user_info = pwd.getpwnam(DEBIAN_USER)
        user_home = Path(user_info.pw_dir)
        cargo_path = user_home / ".cargo/bin"
        profile_path = user_home / ".profile"
        path_export_line = f'export PATH="{cargo_path}:$PATH"'
    except KeyError:
         console.print(f"[bold red]Error:[/bold red] Cannot find user {DEBIAN_USER} to determine home directory.")
         logger.error(f"User {DEBIAN_USER} not found when getting home directory.")
         return False


    console.print(f"Checking for Rust/Cargo installation for user [yellow]{DEBIAN_USER}[/yellow]...")
    # Check for cargo in the expected user path using run_command
    cargo_check_cmd_list = [str(cargo_path / 'cargo'), '--version']
    cargo_check_result = run_command(cargo_check_cmd_list, user=DEBIAN_USER, check=False, description="Checking for cargo via direct path")
    cargo_exists = cargo_check_result is not None and cargo_check_result.returncode == 0

    if not cargo_exists:
        console.print("Rust (cargo) not found. [cyan]Installing Rust via rustup for user...[/cyan]")
        logger.info(f"Rust not found for user {DEBIAN_USER}. Installing via rustup.")
        # rustup-init.sh needs to be run as the target user
        rustup_cmd = "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --no-modify-path"
        # Use bash -c within run_command to handle the pipe
        rustup_install_result = run_command(['bash', '-c', rustup_cmd], user=DEBIAN_USER, description="Running rustup installer", show_output=True, timeout=600) # Increase timeout for potential downloads/builds

        if not rustup_install_result:
            console.print("[bold red]Error:[/bold red] Rust installation via rustup failed.")
            logger.error(f"rustup installation failed for user {DEBIAN_USER}.")
            return False

        console.print(f"Adding Cargo bin directory to PATH in user's [cyan]{profile_path}[/cyan]...")
        logger.info(f"Attempting to add '{path_export_line}' to {profile_path} for user {DEBIAN_USER}.")
        try:
            # Ensure .profile exists (as user)
            if not run_command(['touch', str(profile_path)], user=DEBIAN_USER, description=f"Ensuring {profile_path} exists"):
                logger.warning(f"Could not touch {profile_path} as user {DEBIAN_USER}.") # Continue anyway

            # Check if line already exists (as user)
            # Use grep -qFx for exact match, quoting carefully
            grep_cmd = f"grep -qFx {shlex.quote(path_export_line)} {shlex.quote(str(profile_path))}"
            path_exists_result = run_command(['bash', '-c', grep_cmd], user=DEBIAN_USER, check=False, description="Checking if PATH export exists in .profile")

            if not path_exists_result or path_exists_result.returncode != 0: # 0 means found, 1 means not found, >1 is error
                # Append the line (as user) using tee -a
                # Quoting is tricky here, especially with the inner quotes for PATH
                append_cmd = f"echo {shlex.quote(path_export_line)} >> {shlex.quote(str(profile_path))}"
                append_result = run_command(['bash', '-c', append_cmd], user=DEBIAN_USER, description="Appending PATH to .profile")

                if not append_result:
                     console.print(f"[bold red]Error:[/bold red] Failed to append PATH export to {profile_path}.")
                     logger.error(f"Failed to append PATH to {profile_path} for user {DEBIAN_USER}.")
                     return False # Fail if we can't update PATH
                console.print("[green]✓[/green] Added PATH export to .profile.")
                logger.info(f"Successfully added PATH export to {profile_path}.")
            else:
                console.print("PATH export line already found in .profile.")
                logger.info(f"PATH export line already exists in {profile_path}.")

        except Exception as e:
             console.print(f"[bold red]Error:[/bold red] Failed checking or modifying {profile_path}: {e}")
             logger.exception(f"Failed checking/modifying {profile_path} for user {DEBIAN_USER}.")
             return False # Fail if profile modification fails

        console.print("[green]✓[/green] Rust installed and PATH configured in .profile.")
        logger.info(f"Rust successfully installed for {DEBIAN_USER}.")
    else:
        console.print("Rust (cargo) already installed for this user.")
        logger.info(f"Rust (cargo) already installed for user {DEBIAN_USER}.")


    console.print("Checking for 'just' command runner...")
    logger.info(f"Checking for 'just' for user {DEBIAN_USER}.")
    # Check for 'just' in the cargo bin path as the user
    just_check_cmd_list = [str(cargo_path / 'just'), '--version']
    just_check_result = run_command(just_check_cmd_list, user=DEBIAN_USER, check=False, description="Checking for just via direct path")
    just_exists = just_check_result is not None and just_check_result.returncode == 0

    if not just_exists:
        console.print("'just' not found. [cyan]Installing 'just' via cargo...[/cyan]")
        logger.info(f"'just' not found for user {DEBIAN_USER}. Installing via cargo.")
        # Install 'just' using the user's cargo
        # Ensure PATH includes the cargo bin dir for this command
        # Using direct path to cargo might be more reliable than relying on .profile being sourced
        just_install_cmd_list = [str(cargo_path / 'cargo'), 'install', 'just']
        install_just_result = run_command(just_install_cmd_list, user=DEBIAN_USER, description="Installing just via cargo", show_output=True, timeout=300)

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
def step_zt_join_verify(progress, task_id, args): # Added args
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
def step_qcow_perms(progress, task_id, args): # Added args
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
        target_uid = 0 # root
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
def step_nbd_service(progress, task_id, args): # Added args
    """Creates the systemd service file for managing the QEMU NBD connection."""
    logger.info("Defining systemd service for QEMU NBD.")
    nbd_service_file = Path("/etc/systemd/system/qemu-nbd-connect.service")
    console.print(f"Defining NBD systemd service: [cyan]{nbd_service_file}[/cyan]")

    # Use Type=oneshot with RemainAfterExit=yes, include ExecStartPost check
    # Ensure modprobe happens before trying to disconnect/connect
    # Add retry/check loop in ExecStartPost for robustness
    content = f"""[Unit]
Description=Set up QEMU NBD device {NBD_DEVICE} for {LOCAL_QCOW_PATH}
Documentation=man:qemu-nbd(8)
After=local-fs.target network-online.target systemd-modules-load.service
Wants=network-online.target systemd-modules-load.service
Before=lvm2-activation-early.service lvm2-activation.service lvm-activate-data-vg.service

[Service]
Type=oneshot
RemainAfterExit=yes
# Load nbd module if not already loaded
ExecStartPre=/sbin/modprobe nbd nbds_max=16
# Attempt disconnect first in case it was left connected
ExecStartPre=-/usr/bin/qemu-nbd --disconnect {NBD_DEVICE}
# Connect the NBD device
ExecStart=/usr/bin/qemu-nbd --connect={NBD_DEVICE} {LOCAL_QCOW_PATH}
# Wait for the device to appear and be readable (basic check)
ExecStartPost=/bin/bash -c 'tries=60; delay=0.5; while [ $tries -gt 0 ]; do if [ -b {NBD_DEVICE} ]; then size=$(/usr/bin/lsblk -bno SIZE {NBD_DEVICE} 2>/dev/null || echo 0); if [ "$size" -gt 0 ]; then echo "NBD Size OK ($size), testing read..."; if dd if={NBD_DEVICE} of=/dev/null bs=1k count=1 status=none; then echo "NBD Read OK."; exit 0; else echo "NBD Read FAILED ($?), retrying..."; sleep $delay; fi; else echo "NBD Size is 0, waiting..."; sleep $delay; fi; else echo "Waiting for {NBD_DEVICE}..."; sleep $delay; fi; tries=$((tries-1)); done; echo "NBD device {NBD_DEVICE} did not become ready (exist/size/read test failed)"; exit 1'
# Disconnect on service stop
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
def step_lvm_service(progress, task_id, args): # Added args
    """Creates the systemd service file for activating the LVM Volume Group."""
    logger.info(f"Defining systemd service for LVM activation ({VG_NAME}).")
    lvm_service_file = Path("/etc/systemd/system/lvm-activate-data-vg.service")
    console.print(f"Defining LVM activation systemd service: [cyan]{lvm_service_file}[/cyan]")

    # Add checks and waits for NBD device and LV node
    content = f"""[Unit]
Description=Activate LVM Volume Group '{VG_NAME}' on NBD device {NBD_DEVICE}
Documentation=man:vgchange(8) man:lvchange(8)
Requires=qemu-nbd-connect.service
After=qemu-nbd-connect.service systemd-udev-settle.service
Before=local-fs.target remote-fs.target mnt-data.mount # Ensure it runs before trying to mount

[Service]
Type=oneshot
RemainAfterExit=yes
Environment="PATH=/usr/sbin:/usr/bin:/sbin:/bin"
# Wait briefly after NBD connect service reports success
ExecStartPre=/bin/sleep 1
# Settle udev rules
ExecStartPre=/usr/bin/udevadm settle --timeout=30
# Verify NBD device readiness again before activating VG
ExecStartPre=/bin/bash -c 'tries=30; delay=1; while [ $tries -gt 0 ]; do if [ -b {NBD_DEVICE} ]; then echo "NBD device {NBD_DEVICE} found. Testing read..."; if dd if={NBD_DEVICE} of=/dev/null bs=1k count=1 status=none; then echo "NBD Read OK."; exit 0; else echo "NBD Read FAILED ($?), retrying..."; sleep $delay; fi; fi; echo "Waiting for {NBD_DEVICE}..."; sleep $delay; tries=$((tries-1)); done; echo "NBD device {NBD_DEVICE} did not become ready/readable"; exit 1'
# Activate the Volume Group
ExecStart=/usr/sbin/lvm vgchange -ay {VG_NAME}
# Wait for the Logical Volume device node to appear
ExecStartPost=/bin/bash -c 'tries=30; delay=1; while ! [ -b {LV_DEVICE_PATH} ]; do echo "Waiting for LV {LV_DEVICE_PATH}..."; sleep $delay; tries=$((tries-1)); if [ "$tries" -le 0 ]; then echo "LV node {LV_DEVICE_PATH} did not appear"; exit 1; fi; done; echo "LV node {LV_DEVICE_PATH} appeared."'
# Deactivate on service stop
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
def step_lvm_setup(progress, task_id, args): # Added args
    """Checks if the LVM LV exists, performs first-time setup (PV, VG, LV, format) if not, using transient NBD."""
    logger.info(f"Starting LVM configuration check/setup for {LV_DEVICE_PATH}.")
    console.print(f"Checking if LVM logical volume [cyan]{LV_DEVICE_PATH}[/cyan] exists...")

    # Check using lvs command first, as device node might not exist even if LV is defined but inactive
    lvs_check_cmd = ['lvs', '--noheadings', '-o', 'lv_path', f'{VG_NAME}/{LV_NAME}']
    lvs_result = run_command(lvs_check_cmd, description="Checking if LV exists via lvs", check=False, capture_output=True)

    # If lvs succeeds and output matches the expected path (or just succeeds meaning LV exists)
    if lvs_result and lvs_result.returncode == 0 and str(LV_DEVICE_PATH) in lvs_result.stdout:
        console.print("[green]✓[/green] LVM LV already exists (according to lvs). Skipping creation.")
        logger.info(f"LVM LV {LV_DEVICE_PATH} already exists based on lvs output.")
        # Ensure VG is active for subsequent steps (like fstab mount testing)
        run_command(['lvm', 'vgchange', '-ay', VG_NAME], description="Ensuring VG is active", check=False)
        progress.update(task_id, advance=1)
        return True
    elif LV_DEVICE_PATH.is_block_device():
         # Fallback check if lvs failed but device exists somehow
        console.print("[yellow]Warning:[/yellow] lvs check failed, but block device exists. Assuming LVM is set up.")
        logger.warning(f"LVM LV check via lvs failed, but {LV_DEVICE_PATH} exists. Assuming setup is complete.")
        run_command(['lvm', 'vgchange', '-ay', VG_NAME], description="Ensuring VG is active", check=False)
        progress.update(task_id, advance=1)
        return True


    console.print("LVM LV not found. [cyan]Performing one-time LVM setup...[/cyan]")
    logger.info(f"LVM LV {LV_DEVICE_PATH} not found. Starting LVM creation process.")

    console.print("Reloading systemd daemon (to ensure NBD service unit is known)...")
    if not run_command(['systemctl', 'daemon-reload'], description="Daemon reload"):
        logger.error("daemon-reload failed before transient NBD start.")
        # Non-fatal, service file might still be loadable
        console.print("[yellow]Warning:[/yellow] daemon-reload failed. Attempting to start NBD anyway.")

    console.print(f"Starting NBD connection temporarily ([green]qemu-nbd-connect.service[/green])...")
    # Start the service. Its ExecStartPost should handle waiting/checking.
    # Add a timeout to the start command itself in case the ExecStartPost script hangs badly
    start_nbd_result = run_command(['systemctl', 'start', 'qemu-nbd-connect.service'],
                                   description="Starting NBD service transiently (blocks until ready/failed)",
                                   timeout=90) # 90 seconds timeout for start + readiness check

    if not start_nbd_result:
         console.print(f"[bold red]Error:[/bold red] Failed to start NBD service transiently or its readiness check failed.")
         logger.error("systemctl start qemu-nbd-connect.service failed (likely ExecStartPost check or timeout).")
         run_command(['journalctl', '-u', 'qemu-nbd-connect.service', '-n', '50', '--no-pager'], description="NBD service logs", show_output=True, check=False)
         # Try to stop it just in case it's stuck partially
         run_command(['systemctl', 'stop', 'qemu-nbd-connect.service'], description="Attempting NBD service stop", check=False)
         return False
    logger.info("Transient NBD service started successfully (includes readiness check).")
    # Add a small extra delay just in case device nodes need more time in userspace
    time.sleep(1)

    # Double-check the device node exists *after* the start command succeeded
    if not Path(NBD_DEVICE).is_block_device():
        console.print(f"[bold red]Error:[/bold red] NBD device [cyan]{NBD_DEVICE}[/cyan] not found after service start reported success.")
        logger.error(f"NBD device {NBD_DEVICE} missing after successful service start report.")
        run_command(['lsblk'], description="Current block devices", show_output=True, check=False)
        run_command(['systemctl', 'stop', 'qemu-nbd-connect.service'], description="Attempting NBD service stop", check=False)
        return False
    console.print(f"[green]✓[/green] NBD device {NBD_DEVICE} seems ready.")
    logger.info(f"NBD device {NBD_DEVICE} check passed after transient start.")

    # --- LVM Creation Steps ---
    lvm_success = True
    try:
        logger.info(f"Running pvcreate -vvv -ff -y {NBD_DEVICE}")
        if not run_command(['pvcreate', '-vvv', '-ff', '-y', NBD_DEVICE], description="Creating LVM PV (verbose, double-forced, non-interactive)", show_output=True, timeout=60):
            raise RuntimeError("pvcreate failed")

        logger.info(f"Running vgcreate -y {VG_NAME} {NBD_DEVICE}")
        if not run_command(['vgcreate', '-y', VG_NAME, NBD_DEVICE], description="Creating LVM VG (non-interactive)", timeout=30):
             raise RuntimeError("vgcreate failed")

        logger.info(f"Running lvcreate -y -l 100%FREE -n {LV_NAME} {VG_NAME}")
        if not run_command(['lvcreate', '-y', '-l', '100%FREE', '-n', LV_NAME, VG_NAME], description="Creating LVM LV (non-interactive)", timeout=30):
             raise RuntimeError("lvcreate failed")

        # Wait for the LV device node to appear
        logger.info(f"Waiting for LV device node {LV_DEVICE_PATH} to appear...")
        node_appeared = False
        for attempt in range(15): # Increase attempts slightly
             if LV_DEVICE_PATH.is_block_device():
                 node_appeared = True
                 logger.info(f"LV device node {LV_DEVICE_PATH} appeared after {attempt+1}s.")
                 break
             console.log(f"Waiting for {LV_DEVICE_PATH}... ({attempt+1}/15)")
             time.sleep(1)
        if not node_appeared:
            run_command(['lsblk'], description="Current block devices", show_output=True, check=False)
            raise RuntimeError(f"LV device node {LV_DEVICE_PATH} did not appear after creation.")
        else:
            # Settle udev again after LV creation
             run_command(['udevadm', 'settle'], description="Settling udev after LV creation", check=False)
             time.sleep(1) # Small extra delay

        # Format the LV
        logger.info(f"Formatting {LV_DEVICE_PATH} with ext4...")
        if not run_command(['mkfs.ext4', '-F', str(LV_DEVICE_PATH)], description="Formatting LV with ext4", timeout=300): # Allow time for large FS format
             raise RuntimeError("mkfs.ext4 failed")

    except Exception as lvm_err:
         console.print(f"[bold red]Error during LVM setup:[/bold red] {lvm_err}")
         logger.exception("Error during LVM PV/VG/LV/mkfs steps.")
         lvm_success = False
         # Show LVM status on failure
         run_command(['pvs'], description="LVM PV Status", show_output=True, check=False)
         run_command(['vgs'], description="LVM VG Status", show_output=True, check=False)
         run_command(['lvs'], description="LVM LV Status", show_output=True, check=False)


    # --- Cleanup Transient NBD ---
    console.print("Stopping temporary NBD service used for LVM setup...")
    logger.info("Stopping transient NBD service used for LVM creation.")
    # Don't check result, just try to stop it
    run_command(['systemctl', 'stop', 'qemu-nbd-connect.service'], description="Stopping transient NBD service", check=False)
    time.sleep(2) # Give time for disconnect

    # --- Final Result ---
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
def step_fstab(progress, task_id, args): # Added args
    """Creates the mount point, sets ownership, adds fstab entry."""
    logger.info(f"Starting mount point and fstab configuration for {LVM_MOUNT_POINT}.")
    console.print(f"Ensuring mount point [cyan]{LVM_MOUNT_POINT}[/cyan] exists with correct ownership ([yellow]{DEBIAN_USER}:{DEBIAN_GROUP}[/yellow])...")

    try:
        # Create directory if it doesn't exist
        LVM_MOUNT_POINT.mkdir(parents=True, exist_ok=True)
        logger.debug(f"Ensured directory {LVM_MOUNT_POINT} exists.")

        # Get UID/GID for ownership
        user_info = pwd.getpwnam(DEBIAN_USER)
        group_info = grp.getgrnam(DEBIAN_GROUP)
        target_uid = user_info.pw_uid
        target_gid = group_info.gr_gid

        # Set ownership
        os.chown(LVM_MOUNT_POINT, target_uid, target_gid)
        logger.info(f"Set ownership of {LVM_MOUNT_POINT} to {target_uid}:{target_gid} ({DEBIAN_USER}:{DEBIAN_GROUP}).")
        console.print(f"  - Ownership set to [yellow]{DEBIAN_USER}:{DEBIAN_GROUP}[/yellow].")
        # Optionally set permissions (e.g., 775) if needed, but default might be fine
        # os.chmod(LVM_MOUNT_POINT, 0o775)

    except KeyError as e:
         console.print(f"[bold red]Error:[/bold red] Cannot find user '{DEBIAN_USER}' or group '{DEBIAN_GROUP}' to set mount point ownership: {e}")
         logger.error(f"User/group lookup failed for mount point ownership: {e}")
         return False
    except Exception as e:
        logger.exception(f"Failed to create or set ownership for mount point {LVM_MOUNT_POINT}")
        console.print(f"[bold red]Error:[/bold red] Failed configuring mount point directory {LVM_MOUNT_POINT}: {e}")
        return False

    # --- fstab Configuration ---
    fstab_file = Path("/etc/fstab")
    # Added _netdev because activation depends on qemu-nbd service which might involve network if qcow is remote (though not in this script's default)
    # Added nofail so system boots even if the mount fails
    fstab_entry_line = f"{LV_DEVICE_PATH}    {LVM_MOUNT_POINT}    ext4    defaults,nofail,_netdev    0    2"
    fstab_comment_line = f"# Entry added by AVF installer for LVM data volume ({VG_NAME}/{LV_NAME})"
    console.print(f"Checking fstab entry for [cyan]{LVM_MOUNT_POINT}[/cyan] in [cyan]{fstab_file}[/cyan]...")
    logger.info(f"Checking {fstab_file} for entry mounting {LV_DEVICE_PATH} at {LVM_MOUNT_POINT}")

    try:
        if not fstab_file.is_file():
             console.print(f"[bold red]Error:[/bold red] fstab file {fstab_file} not found!")
             logger.error(f"fstab file {fstab_file} not found.")
             return False

        content = fstab_file.read_text()
        entry_exists = False
        conflict_exists = False
        for line in content.splitlines():
            clean_line = line.strip()
            if not clean_line or clean_line.startswith('#'): continue
            parts = clean_line.split()
            if len(parts) >= 2:
                fstab_device = parts[0]
                fstab_mountpoint = parts[1]
                # Check if our exact device and mountpoint match
                if fstab_device == str(LV_DEVICE_PATH) and fstab_mountpoint == str(LVM_MOUNT_POINT):
                    entry_exists = True
                    logger.info(f"Found existing fstab entry matching device and mountpoint: {clean_line}")
                    break
                # Check if our mountpoint is used by a *different* device
                elif fstab_mountpoint == str(LVM_MOUNT_POINT) and fstab_device != str(LV_DEVICE_PATH):
                    console.print(f"[bold yellow]Warning:[/bold yellow] Mount point {LVM_MOUNT_POINT} found in fstab but configured for a different device ({fstab_device})! Check {fstab_file}.")
                    logger.warning(f"fstab conflict: {LVM_MOUNT_POINT} used by different device {fstab_device}.")
                    conflict_exists = True
                    break
                # Check if our device is mounted *elsewhere*
                elif fstab_device == str(LV_DEVICE_PATH) and fstab_mountpoint != str(LVM_MOUNT_POINT):
                    console.print(f"[bold yellow]Warning:[/bold yellow] Device {LV_DEVICE_PATH} found in fstab but mounted elsewhere ({fstab_mountpoint})! Check {fstab_file}.")
                    logger.warning(f"fstab conflict: {LV_DEVICE_PATH} mounted elsewhere at {fstab_mountpoint}.")
                    conflict_exists = True
                    break

        if conflict_exists:
            console.print("[bold red]Error:[/bold red] fstab conflict detected. Please resolve manually before proceeding.")
            return False # Fail if there's a conflict

        if not entry_exists:
            console.print("Adding fstab entry...")
            logger.info(f"Adding fstab entry: {fstab_entry_line}")
            # Ensure newline before adding comment/entry
            if content and not content.endswith('\n'):
                content += "\n"
            new_content = content + f"\n{fstab_comment_line}\n{fstab_entry_line}\n"
            # Write back - consider making a backup first
            try:
                 backup_fstab = fstab_file.with_suffix(fstab_file.suffix + f".bak-{current_timestamp}")
                 shutil.copy2(fstab_file, backup_fstab)
                 logger.info(f"Backed up fstab to {backup_fstab}")
                 fstab_file.write_text(new_content)
            except Exception as write_err:
                 console.print(f"[bold red]Error:[/bold red] Failed to write fstab file {fstab_file}: {write_err}")
                 logger.exception(f"Failed writing fstab file {fstab_file}")
                 return False

            console.print("[green]✓[/green] fstab entry added.")
            logger.info("Successfully added fstab entry.")
        else:
            console.print("[green]✓[/green] fstab entry already seems to exist.")

        # Test the mount immediately if the device is active
        # Activate VG first just in case it wasn't active from LVM step
        vg_active_check = run_command(['lvm', 'vgchange', '-ay', VG_NAME], description="Ensuring VG is active before mount test", check=False)
        if vg_active_check and LV_DEVICE_PATH.is_block_device():
             console.print(f"Attempting to mount [cyan]{LVM_MOUNT_POINT}[/cyan] using new fstab entry...")
             # Use mount -a which reads fstab, but target the specific mountpoint
             # Use mount --target to be safer than mount -a
             mount_result = run_command(['mount', str(LVM_MOUNT_POINT)], description=f"Testing mount {LVM_MOUNT_POINT}", check=False)
             if mount_result and mount_result.returncode == 0:
                 console.print(f"[green]✓[/green] Successfully mounted {LVM_MOUNT_POINT}.")
                 logger.info(f"Successfully mounted {LVM_MOUNT_POINT} via 'mount' command.")
                 # Check ownership again after mount? Filesystem options might override.
                 try:
                     mount_stat = LVM_MOUNT_POINT.stat()
                     if mount_stat.st_uid != target_uid or mount_stat.st_gid != target_gid:
                          console.print(f"[yellow]Warning:[/yellow] Ownership of mounted {LVM_MOUNT_POINT} is {mount_stat.st_uid}:{mount_stat.st_gid}, expected {target_uid}:{target_gid}. Filesystem mount options might be overriding.")
                          logger.warning(f"Ownership mismatch after mount: {mount_stat.st_uid}:{mount_stat.st_gid} vs {target_uid}:{target_gid}")
                 except Exception as stat_err:
                      logger.warning(f"Could not stat mounted directory {LVM_MOUNT_POINT}: {stat_err}")

             else:
                 # Check if already mounted (mount returns 32 if already mounted)
                 mountpoint_check = run_command(['mountpoint', '-q', str(LVM_MOUNT_POINT)], check=False, description="Checking if already mounted")
                 if mountpoint_check and mountpoint_check.returncode == 0:
                      console.print(f"[green]✓[/green] {LVM_MOUNT_POINT} appears to be already mounted.")
                      logger.info(f"{LVM_MOUNT_POINT} already mounted.")
                 else:
                      console.print(f"[bold yellow]Warning:[/bold yellow] Failed to mount {LVM_MOUNT_POINT} using 'mount' command (Code: {mount_result.returncode if mount_result else 'N/A'}). It should mount on next boot via fstab.")
                      logger.warning(f"Failed to mount {LVM_MOUNT_POINT} using mount command.")
                      if mount_result and mount_result.stderr:
                           logger.warning(f"Mount stderr: {mount_result.stderr.strip()}")
        else:
            console.print(f"[yellow]Skipping immediate mount test:[/yellow] VG '{VG_NAME}' not active or LV device node missing.")
            logger.info(f"Skipping mount test as VG {VG_NAME} not active or LV device missing.")


        logger.info("Mount point and fstab configuration finished.")
        progress.update(task_id, advance=1)
        return True

    except Exception as e:
        logger.exception(f"Failed during fstab configuration for {LVM_MOUNT_POINT}")
        console.print(f"[bold red]Error:[/bold red] Failed during fstab configuration: {e}")
        return False


@installer_step("Enable Storage Persistence Services")
def step_enable_storage_services(progress, task_id, args): # Added args
    """Reloads systemd daemon and enables NBD and LVM activation services for boot."""
    logger.info("Enabling storage persistence services (NBD, LVM activation).")
    console.print("[cyan]Reloading systemd daemon (to recognize new/modified service units)...[/cyan]")
    if not run_command(['systemctl', 'daemon-reload'], description="Daemon reload"):
        logger.error("daemon-reload failed before enabling services.")
        # This is usually serious, might prevent enabling
        console.print("[bold red]Error:[/bold red] systemctl daemon-reload failed. Service enablement might fail. Check 'systemctl status' manually.")
        return False # Fail the step if daemon-reload fails

    console.print("[cyan]Enabling NBD ([green]qemu-nbd-connect.service[/green]) and LVM activation ([green]lvm-activate-data-vg.service[/green]) services for boot...[/cyan]")
    nbd_enabled_ok = run_command(['systemctl', 'enable', 'qemu-nbd-connect.service'], description="Enabling NBD service")
    lvm_enabled_ok = run_command(['systemctl', 'enable', 'lvm-activate-data-vg.service'], description="Enabling LVM activation service")

    if nbd_enabled_ok and lvm_enabled_ok:
        console.print("[green]✓[/green] Storage persistence services enabled for boot.")
        logger.info("NBD and LVM activation services enabled successfully.")
        # Also try starting them now if not already running (idempotent)
        console.print("[cyan]Attempting to start storage services now...[/cyan]")
        run_command(['systemctl', 'start', 'qemu-nbd-connect.service'], description="Starting NBD service", check=False) # Allow failure if already running
        run_command(['systemctl', 'start', 'lvm-activate-data-vg.service'], description="Starting LVM activation service", check=False) # Allow failure if already running

        progress.update(task_id, advance=1)
        return True
    else:
        if not nbd_enabled_ok: logger.error("Failed to enable qemu-nbd-connect.service.")
        if not lvm_enabled_ok: logger.error("Failed to enable lvm-activate-data-vg.service.")
        console.print("[bold red]Error:[/bold red] Failed to enable one or both storage services. Check systemctl status and journalctl for details.")
        run_command(['systemctl', 'status', 'qemu-nbd-connect.service', 'lvm-activate-data-vg.service', '--no-pager'], description="Storage service status", check=False, show_output=True)
        return False


@installer_step("Install Docker CE with Multi-Architecture Support")
def step_install_docker(progress, task_id, args):
    """Installs Docker CE with ARM64 and x86 emulation support."""
    logger.info("Starting Docker CE installation with multi-architecture support.")
    
    # Check if Docker is already installed
    docker_path = shutil.which('docker')
    if docker_path:
        console.print(f"Docker already installed ([dim]{docker_path}[/dim]). Checking version...")
        version_result = run_command(['docker', '--version'], description="Checking Docker version", show_output=True)
        if version_result:
            logger.info(f"Docker already installed at {docker_path}.")
            progress.update(task_id, advance=1)
            return True
    
    console.print("[cyan]Installing Docker CE with multi-architecture support...[/cyan]")
    logger.info("Docker not found. Proceeding with installation.")
    
    # Remove old Docker packages if they exist
    old_packages = ["docker", "docker-engine", "docker.io", "containerd", "runc"]
    for package in old_packages:
        run_command(['apt-get', 'remove', '-y', package], description=f"Removing old {package}", check=False)
    
    # Install prerequisites (most should already be installed)
    prereq_packages = ["ca-certificates", "curl", "gnupg", "lsb-release"]
    install_env = os.environ.copy()
    install_env['DEBIAN_FRONTEND'] = 'noninteractive'
    
    if not run_command(['apt-get', 'install', '-y'] + prereq_packages, description="Installing Docker prerequisites", env=install_env):
        console.print("[bold red]Error:[/bold red] Failed to install Docker prerequisites.")
        logger.error("Failed to install Docker prerequisites.")
        return False
    
    # Add Docker's official GPG key
    keyring_dir = Path("/etc/apt/keyrings")
    keyring_file = keyring_dir / "docker.gpg"
    docker_key_url = "https://download.docker.com/linux/debian/gpg"
    
    try:
        keyring_dir.mkdir(mode=0o755, parents=True, exist_ok=True)
        logger.debug(f"Ensured keyring directory exists: {keyring_dir}")
    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] Failed creating keyring directory {keyring_dir}: {e}")
        logger.exception(f"Failed creating keyring directory {keyring_dir}")
        return False
    
    # Download and install GPG key
    curl_cmd = ['curl', '-fsSL', docker_key_url]
    gpg_cmd = ['gpg', '--dearmor', '-o', str(keyring_file)]
    
    # Use pipe to combine curl and gpg
    download_result = run_command(f"curl -fsSL {docker_key_url} | gpg --dearmor -o {keyring_file}", 
                                  description="Downloading Docker GPG key", shell=True)
    if not download_result:
        logger.error(f"Failed to download Docker GPG key from {docker_key_url}")
        return False
    
    # Set correct permissions on keyring file
    try:
        os.chmod(keyring_file, 0o644)
        logger.info(f"Set permissions 644 on {keyring_file}")
    except OSError as e:
        console.print(f"[bold red]Error:[/bold red] Failed setting permissions on GPG key {keyring_file}: {e}")
        logger.error(f"Failed setting permissions on {keyring_file}: {e}")
        return False
    
    # Get system architecture
    arch_result = run_command(['dpkg', '--print-architecture'], description="Getting system architecture")
    if not arch_result:
        console.print("[bold red]Error:[/bold red] Could not determine system architecture.")
        logger.error("Failed to determine system architecture.")
        return False
    arch = arch_result.stdout.strip()
    
    # Get distribution codename
    codename_result = run_command(['lsb_release', '-cs'], description="Getting distribution codename")
    if not codename_result:
        console.print("[bold red]Error:[/bold red] Could not determine distribution codename.")
        logger.error("Failed to determine distribution codename.")
        return False
    codename = codename_result.stdout.strip()
    
    # Add Docker repository
    sources_file = Path("/etc/apt/sources.list.d/docker.list")
    repo_line = f"deb [arch={arch} signed-by={keyring_file}] https://download.docker.com/linux/debian {codename} stable"
    
    if not write_file(sources_file, repo_line + "\n", permissions="0644", show_content=True):
        logger.error(f"Failed to write Docker sources file {sources_file}")
        return False
    
    # Update package lists
    if not run_command(['apt-get', 'update', '-qq'], description="Updating package lists after adding Docker repo"):
        logger.error("apt-get update failed after adding Docker repository.")
        return False
    
    # Install Docker CE packages
    docker_packages = [
        "docker-ce",
        "docker-ce-cli", 
        "containerd.io",
        "docker-buildx-plugin",
        "docker-compose-plugin"
    ]
    
    if not run_command(['apt-get', 'install', '-y'] + docker_packages, 
                       description="Installing Docker CE packages", env=install_env):
        logger.error("Failed to install Docker CE packages.")
        return False
    
    # Add user to docker group
    if not run_command(['usermod', '-aG', 'docker', DEBIAN_USER], description=f"Adding {DEBIAN_USER} to docker group"):
        logger.error(f"Failed to add {DEBIAN_USER} to docker group.")
        return False
    
    # Configure Docker daemon for multi-arch support
    docker_config_dir = Path("/etc/docker")
    docker_config_file = docker_config_dir / "daemon.json"
    
    docker_config = {
        "experimental": True,
        "features": {
            "buildkit": True
        },
        "default-address-pools": [
            {
                "base": "172.17.0.0/12",
                "size": 24
            }
        ]
    }
    
    try:
        docker_config_dir.mkdir(mode=0o755, parents=True, exist_ok=True)
        import json
        docker_config_content = json.dumps(docker_config, indent=2)
        if not write_file(docker_config_file, docker_config_content, permissions="0644"):
            logger.error(f"Failed to write Docker daemon configuration {docker_config_file}")
            return False
    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] Failed to configure Docker daemon: {e}")
        logger.exception("Failed to configure Docker daemon")
        return False
    
    # Enable and start Docker service
    if not run_command(['systemctl', 'enable', 'docker'], description="Enabling Docker service"):
        logger.error("Failed to enable Docker service.")
        return False
    
    if not run_command(['systemctl', 'start', 'docker'], description="Starting Docker service"):
        logger.error("Failed to start Docker service.")
        return False
    
    # Restart Docker to apply configuration
    if not run_command(['systemctl', 'restart', 'docker'], description="Restarting Docker with new configuration"):
        logger.error("Failed to restart Docker with new configuration.")
        return False
    
    # Verify Docker installation
    time.sleep(3)  # Give Docker time to start
    docker_path_final = shutil.which('docker')
    if docker_path_final:
        console.print(f"[green]✓[/green] Docker CE installed successfully ([dim]{docker_path_final}[/dim]).")
        logger.info(f"Docker CE installed successfully at {docker_path_final}.")
        
        # Test Docker
        test_result = run_command(['docker', 'version'], description="Testing Docker installation", show_output=True, check=False)
        if test_result and test_result.returncode == 0:
            console.print("[green]✓[/green] Docker is running and accessible.")
        else:
            console.print("[yellow]Warning:[/yellow] Docker installed but may not be fully functional yet.")
        
        progress.update(task_id, advance=1)
        return True
    else:
        console.print("[bold red]Error:[/bold red] Docker installation commands succeeded, but 'docker' command not found.")
        logger.error("Docker installation reported success, but verification failed.")
        return False


@installer_step("Configure QEMU User Static & BFMT Support")
def step_configure_qemu_binfmt(progress, task_id, args):
    """Configures QEMU user static and binfmt support for x86 emulation on ARM64."""
    logger.info("Starting QEMU user static and binfmt configuration for x86 emulation.")
    
    console.print("[cyan]Configuring QEMU user static and binfmt support for x86 emulation...[/cyan]")
    
    # Verify qemu-user-static is installed (should be from package installation step)
    qemu_x86_path = shutil.which('qemu-x86_64-static')
    if not qemu_x86_path:
        console.print("[bold red]Error:[/bold red] qemu-x86_64-static not found. Package installation may have failed.")
        logger.error("qemu-x86_64-static not found after package installation.")
        return False
    
    console.print(f"[green]✓[/green] QEMU user static found at: [dim]{qemu_x86_path}[/dim]")
    
    # Enable and start binfmt-support service
    if not run_command(['systemctl', 'enable', 'binfmt-support'], description="Enabling binfmt-support service"):
        logger.error("Failed to enable binfmt-support service.")
        return False
    
    if not run_command(['systemctl', 'start', 'binfmt-support'], description="Starting binfmt-support service"):
        logger.error("Failed to start binfmt-support service.")
        return False
    
    # Register binary formats
    console.print("[cyan]Registering x86 and x86_64 binary formats...[/cyan]")
    if not run_command(['update-binfmts', '--enable'], description="Enabling binary formats", check=False):
        logger.warning("update-binfmts --enable returned non-zero, but this might be normal.")
    
    # Verify binfmt registration
    binfmt_check = run_command(['update-binfmts', '--display'], description="Checking registered binary formats", show_output=True, check=False)
    if binfmt_check and ('qemu-x86_64' in binfmt_check.stdout or 'qemu-i386' in binfmt_check.stdout):
        console.print("[green]✓[/green] x86 binary formats registered successfully.")
        logger.info("x86 binary formats registered successfully.")
    else:
        console.print("[yellow]Warning:[/yellow] Could not verify x86 binary format registration.")
        logger.warning("Could not verify x86 binary format registration.")
    
    # Test x86 emulation if Docker is available
    docker_available = shutil.which('docker')
    if docker_available:
        console.print("[cyan]Testing x86 emulation with Docker...[/cyan]")
        # Test with a simple x86_64 container
        test_cmd = ['docker', 'run', '--rm', '--platform', 'linux/amd64', 'hello-world']
        test_result = run_command(test_cmd, description="Testing x86 emulation", check=False, timeout=60)
        
        if test_result and test_result.returncode == 0:
            console.print("[green]✓[/green] x86 emulation verified working with Docker.")
            logger.info("x86 emulation verified working with Docker.")
        else:
            console.print("[yellow]Warning:[/yellow] x86 emulation test failed. May work after reboot.")
            logger.warning("x86 emulation test with Docker failed.")
    else:
        console.print("[yellow]Info:[/yellow] Docker not available for x86 emulation testing.")
    
    logger.info("QEMU user static and binfmt configuration completed.")
    progress.update(task_id, advance=1)
    return True


@installer_step("Install Additional Package Management Tools")
def step_install_package_tools(progress, task_id, args):
    """Installs additional package management tools like tasksel and aptitude."""
    logger.info("Starting installation of additional package management tools.")
    
    console.print("[cyan]Installing additional package management tools...[/cyan]")
    
    # These packages should already be in REQUIRED_PACKAGES, but verify they're installed
    additional_tools = ["tasksel", "aptitude", "apt-file", "deborphan", "localepurge"]
    
    # Check which tools are already available
    available_tools = []
    missing_tools = []
    
    for tool in additional_tools:
        if shutil.which(tool):
            available_tools.append(tool)
            console.print(f"[green]✓[/green] {tool} already available")
        else:
            missing_tools.append(tool)
    
    if missing_tools:
        console.print(f"[yellow]Installing missing tools:[/yellow] {', '.join(missing_tools)}")
        install_env = os.environ.copy()
        install_env['DEBIAN_FRONTEND'] = 'noninteractive'
        
        if not run_command(['apt-get', 'install', '-y'] + missing_tools, 
                           description="Installing missing package management tools", 
                           env=install_env):
            console.print("[bold red]Error:[/bold red] Failed to install some package management tools.")
            logger.error("Failed to install missing package management tools.")
            return False
    
    # Update apt-file database if apt-file is available
    if shutil.which('apt-file'):
        console.print("[cyan]Updating apt-file database...[/cyan]")
        apt_file_result = run_command(['apt-file', 'update'], 
                                      description="Updating apt-file database", 
                                      check=False, timeout=300)
        if apt_file_result and apt_file_result.returncode == 0:
            console.print("[green]✓[/green] apt-file database updated.")
        else:
            console.print("[yellow]Warning:[/yellow] apt-file database update failed or timed out.")
            logger.warning("apt-file update failed or timed out.")
    
    # Configure tasksel if available
    if shutil.which('tasksel'):
        console.print("[cyan]Configuring tasksel...[/cyan]")
        # Just verify tasksel works
        tasksel_test = run_command(['tasksel', '--list-tasks'], 
                                   description="Testing tasksel functionality", 
                                   check=False, show_output=False)
        if tasksel_test and tasksel_test.returncode == 0:
            console.print("[green]✓[/green] tasksel is functional.")
        else:
            console.print("[yellow]Warning:[/yellow] tasksel may not be fully functional.")
    
    console.print("[green]✓[/green] Additional package management tools configured.")
    logger.info("Additional package management tools installation completed.")
    progress.update(task_id, advance=1)
    return True


@installer_step("Enhance Configuration Files & Environment")
def step_enhance_configs(progress, task_id, args):
    """Backs up and enhances user configuration files with useful aliases and settings."""
    logger.info(f"Starting configuration file enhancement for user {DEBIAN_USER}.")
    
    try:
        user_info = pwd.getpwnam(DEBIAN_USER)
        user_home = Path(user_info.pw_dir)
        user_gid = user_info.pw_gid
        user_group_info = grp.getgrgid(user_gid)
        user_primary_group = user_group_info.gr_name
    except KeyError:
        console.print(f"[bold red]Error:[/bold red] Cannot find user {DEBIAN_USER} for configuration enhancement.")
        logger.error(f"User {DEBIAN_USER} not found for configuration enhancement.")
        return False
    
    console.print(f"[cyan]Enhancing configuration files for user [yellow]{DEBIAN_USER}[/yellow]...[/cyan]")
    
    # Create backup directory
    backup_dir = user_home / f".config_backup_{current_timestamp}"
    if not run_command(['mkdir', '-p', str(backup_dir)], user=DEBIAN_USER, description="Creating backup directory"):
        logger.error(f"Failed to create backup directory {backup_dir}")
        return False
    
    # Configuration files to backup and enhance
    config_files = {
        ".bashrc": user_home / ".bashrc",
        ".profile": user_home / ".profile", 
        ".bash_profile": user_home / ".bash_profile",
        ".zshrc": user_home / ".zshrc",
        ".vimrc": user_home / ".vimrc",
        ".gitconfig": user_home / ".gitconfig"
    }
    
    # Backup existing files
    for filename, filepath in config_files.items():
        if filepath.exists():
            backup_path = backup_dir / filename
            try:
                shutil.copy2(str(filepath), str(backup_path))
                console.print(f"[green]✓[/green] Backed up {filename}")
                logger.info(f"Backed up {filepath} to {backup_path}")
            except Exception as e:
                console.print(f"[yellow]Warning:[/yellow] Failed to backup {filename}: {e}")
                logger.warning(f"Failed to backup {filepath}: {e}")
    
    # Enhance .bashrc
    bashrc_file = user_home / ".bashrc"
    bashrc_enhancements = '''
# Enhanced configuration added by interactive update script
# History settings
export HISTSIZE=10000
export HISTFILESIZE=20000
export HISTCONTROL=ignoredups:erasedups
shopt -s histappend

# Better ls aliases
alias ll='ls -alF'
alias la='ls -A'
alias l='ls -CF'
alias ls='ls --color=auto'

# Useful aliases
alias grep='grep --color=auto'
alias fgrep='fgrep --color=auto'
alias egrep='egrep --color=auto'
alias df='df -h'
alias du='du -h'
alias free='free -h'
alias psg='ps aux | grep'
alias ..='cd ..'
alias ...='cd ../..'

# Docker aliases
alias dps='docker ps'
alias dpsa='docker ps -a'
alias di='docker images'
alias drmi='docker rmi'
alias dexec='docker exec -it'
alias dlog='docker logs'
alias dstop='docker stop'
alias dstart='docker start'

# System information
alias sysinfo='neofetch'
alias ports='netstat -tulanp'
alias myip='curl -s ifconfig.me'

# Safety aliases
alias rm='rm -i'
alias cp='cp -i'
alias mv='mv -i'

# Development environment
export EDITOR=nano
export VISUAL=nano

# Add local bin to PATH if it exists
if [ -d "$HOME/.local/bin" ]; then
    export PATH="$HOME/.local/bin:$PATH"
fi

# Add cargo bin to PATH if it exists
if [ -d "$HOME/.cargo/bin" ]; then
    export PATH="$HOME/.cargo/bin:$PATH"
fi

# Docker environment
export DOCKER_BUILDKIT=1
export COMPOSE_DOCKER_CLI_BUILD=1
'''
    
    try:
        # Check if enhancements are already present
        if bashrc_file.exists():
            current_content = bashrc_file.read_text()
            if "Enhanced configuration added by interactive update script" not in current_content:
                with open(bashrc_file, 'a') as f:
                    f.write(bashrc_enhancements)
                console.print("[green]✓[/green] Enhanced .bashrc with useful aliases and settings.")
                logger.info("Enhanced .bashrc with additional configuration.")
            else:
                console.print("[green]✓[/green] .bashrc already enhanced.")
        else:
            # Create .bashrc if it doesn't exist
            if not write_file(bashrc_file, bashrc_enhancements, owner=DEBIAN_USER, group=user_primary_group, permissions="0644"):
                logger.error(f"Failed to create enhanced .bashrc for {DEBIAN_USER}")
                return False
            console.print("[green]✓[/green] Created enhanced .bashrc.")
    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] Failed to enhance .bashrc: {e}")
        logger.exception("Failed to enhance .bashrc")
        return False
    
    # Enhance .profile
    profile_file = user_home / ".profile"
    profile_enhancements = '''
# Enhanced profile configuration
# Set PATH to include user's private bin directories
if [ -d "$HOME/bin" ] ; then
    PATH="$HOME/bin:$PATH"
fi

if [ -d "$HOME/.local/bin" ] ; then
    PATH="$HOME/.local/bin:$PATH"
fi

# Docker environment
export DOCKER_BUILDKIT=1
export COMPOSE_DOCKER_CLI_BUILD=1

# Development environment variables
export LANG=en_US.UTF-8
export LC_ALL=en_US.UTF-8

# ARM64 specific optimizations
export MAKEFLAGS="-j$(nproc)"
'''
    
    try:
        if profile_file.exists():
            current_content = profile_file.read_text()
            if "Enhanced profile configuration" not in current_content:
                with open(profile_file, 'a') as f:
                    f.write(profile_enhancements)
                console.print("[green]✓[/green] Enhanced .profile with environment settings.")
            else:
                console.print("[green]✓[/green] .profile already enhanced.")
        else:
            if not write_file(profile_file, profile_enhancements, owner=DEBIAN_USER, group=user_primary_group, permissions="0644"):
                logger.error(f"Failed to create enhanced .profile for {DEBIAN_USER}")
                return False
            console.print("[green]✓[/green] Created enhanced .profile.")
    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] Failed to enhance .profile: {e}")
        logger.exception("Failed to enhance .profile")
        return False
    
    # Create a basic .vimrc if it doesn't exist
    vimrc_file = user_home / ".vimrc"
    if not vimrc_file.exists():
        vimrc_content = '''# Basic vim configuration
set number
set tabstop=4
set shiftwidth=4
set expandtab
set autoindent
set hlsearch
set incsearch
syntax on
'''
        if write_file(vimrc_file, vimrc_content, owner=DEBIAN_USER, group=user_primary_group, permissions="0644"):
            console.print("[green]✓[/green] Created basic .vimrc configuration.")
        else:
            console.print("[yellow]Warning:[/yellow] Failed to create .vimrc.")
    
    # Set ownership of backup directory
    try:
        shutil.chown(str(backup_dir), user=DEBIAN_USER, group=user_primary_group)
        for item in backup_dir.iterdir():
            shutil.chown(str(item), user=DEBIAN_USER, group=user_primary_group)
    except Exception as e:
        console.print(f"[yellow]Warning:[/yellow] Failed to set ownership of backup directory: {e}")
        logger.warning(f"Failed to set ownership of backup directory: {e}")
    
    console.print(f"[green]✓[/green] Configuration files enhanced and backed up to [cyan]{backup_dir}[/cyan].")
    logger.info("Configuration file enhancement completed.")
    progress.update(task_id, advance=1)
    return True


@installer_step("Install Starship Cross-Shell Prompt")
def step_install_starship(progress, task_id, args):
    """Installs and configures Starship cross-shell prompt."""
    logger.info("Starting Starship cross-shell prompt installation.")
    
    # Check if Starship is already installed
    starship_path = shutil.which('starship')
    if starship_path:
        console.print(f"Starship already installed ([dim]{starship_path}[/dim]). Skipping installation.")
        logger.info(f"Starship already installed at {starship_path}.")
        progress.update(task_id, advance=1)
        return True
    
    console.print("[cyan]Installing Starship cross-shell prompt...[/cyan]")
    logger.info("Starship not found. Proceeding with installation.")
    
    # Download and install Starship
    install_cmd = "curl -sS https://starship.rs/install.sh | sh -s -- --yes"
    install_result = run_command(install_cmd, description="Installing Starship", shell=True, show_output=True, timeout=300)
    
    if not install_result:
        console.print("[bold red]Error:[/bold red] Starship installation failed.")
        logger.error("Starship installation script failed.")
        return False
    
    # Verify installation
    starship_path_final = shutil.which('starship')
    if not starship_path_final:
        console.print("[bold red]Error:[/bold red] Starship installation succeeded but command not found.")
        logger.error("Starship installation succeeded but verification failed.")
        return False
    
    console.print(f"[green]✓[/green] Starship installed at: [dim]{starship_path_final}[/dim]")
    
    # Get user's home directory
    try:
        user_info = pwd.getpwnam(DEBIAN_USER)
        user_home = Path(user_info.pw_dir)
    except KeyError:
        console.print(f"[bold red]Error:[/bold red] Cannot find user {DEBIAN_USER} for Starship configuration.")
        logger.error(f"User {DEBIAN_USER} not found for Starship configuration.")
        return False
    
    # Create Starship configuration directory
    config_dir = user_home / ".config"
    if not run_command(['mkdir', '-p', str(config_dir)], user=DEBIAN_USER, description="Creating .config directory"):
        logger.error(f"Failed to create config directory {config_dir} as user {DEBIAN_USER}")
        return False
    
    # Create custom Starship configuration
    starship_config_file = config_dir / "starship.toml"
    starship_config_content = '''# Starship configuration for Debian ARM64 VM

format = """
[╭─user───❯](bold blue) $username\\
[@ ](bold blue)$hostname\\
[ in ](bold blue)$directory\\
$git_branch\\
$git_status\\
$python\\
$nodejs\\
$rust\\
$docker_context\\
$time\\

[╰─❯](bold blue) """

[username]
style_user = "bold dimmed blue"
show_always = true

[hostname]
ssh_only = false
style = "bold dimmed blue"

[directory]
style = "cyan"
truncation_length = 3
truncate_to_repo = false

[git_branch]
symbol = "🌱 "
style = "bold green"

[git_status]
style = "bold yellow"

[python]
symbol = "🐍 "
style = "bold green"

[nodejs]
symbol = "⬢ "
style = "bold green"

[rust]
symbol = "🦀 "
style = "bold red"

[docker_context]
symbol = "🐳 "
style = "bold blue"

[time]
disabled = false
format = '🕙[\\[ $time \\]]($style) '
time_format = "%T"
style = "bold yellow"

[character]
success_symbol = "[❯](bold green)"
error_symbol = "[❯](bold red)"
'''
    
    if not write_file(starship_config_file, starship_config_content, owner=DEBIAN_USER, permissions="0644"):
        console.print("[bold red]Error:[/bold red] Failed to write Starship configuration file.")
        logger.error(f"Failed writing Starship configuration {starship_config_file}")
        return False
    
    # Configure Starship for bash
    bashrc_file = user_home / ".bashrc"
    starship_init_bash = 'eval "$(starship init bash)"'
    
    try:
        if bashrc_file.exists():
            bashrc_content = bashrc_file.read_text()
            if starship_init_bash not in bashrc_content:
                # Append Starship initialization
                with open(bashrc_file, 'a') as f:
                    f.write(f"\n# Initialize Starship prompt\n{starship_init_bash}\n")
                console.print("[green]✓[/green] Starship configured for bash.")
                logger.info("Starship configured for bash in .bashrc")
            else:
                console.print("[green]✓[/green] Starship already configured for bash.")
                logger.info("Starship already configured for bash")
        else:
            # Create .bashrc with Starship
            if not write_file(bashrc_file, f"# Starship prompt\n{starship_init_bash}\n", owner=DEBIAN_USER, permissions="0644"):
                logger.error(f"Failed to create .bashrc with Starship for {DEBIAN_USER}")
                return False
            console.print("[green]✓[/green] Created .bashrc with Starship configuration.")
    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] Failed to configure Starship for bash: {e}")
        logger.exception("Failed to configure Starship for bash")
        return False
    
    # Configure Starship for zsh if it exists
    zshrc_file = user_home / ".zshrc"
    starship_init_zsh = 'eval "$(starship init zsh)"'
    
    # Check if zsh is installed
    zsh_path = shutil.which('zsh')
    if zsh_path:
        try:
            if zshrc_file.exists():
                zshrc_content = zshrc_file.read_text()
                if starship_init_zsh not in zshrc_content:
                    with open(zshrc_file, 'a') as f:
                        f.write(f"\n# Initialize Starship prompt\n{starship_init_zsh}\n")
                    console.print("[green]✓[/green] Starship configured for zsh.")
                    logger.info("Starship configured for zsh in .zshrc")
                else:
                    console.print("[green]✓[/green] Starship already configured for zsh.")
            else:
                # Create .zshrc with Starship
                if not write_file(zshrc_file, f"# Starship prompt\n{starship_init_zsh}\n", owner=DEBIAN_USER, permissions="0644"):
                    logger.warning(f"Failed to create .zshrc with Starship for {DEBIAN_USER}")
                else:
                    console.print("[green]✓[/green] Created .zshrc with Starship configuration.")
        except Exception as e:
            console.print(f"[yellow]Warning:[/yellow] Failed to configure Starship for zsh: {e}")
            logger.warning(f"Failed to configure Starship for zsh: {e}")
    
    console.print("[green]✓[/green] Starship cross-shell prompt installed and configured.")
    logger.info("Starship installation and configuration completed.")
    progress.update(task_id, advance=1)
    return True


@installer_step("Install Brave Browser")
def step_install_brave(progress, task_id, args): # Added args
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
        # Use curl command directly
        curl_cmd = ['curl', '-fsSLo', str(keyring_file), key_url]
        if not run_command(curl_cmd, description="Downloading Brave GPG key"):
            logger.error(f"Failed to download Brave GPG key from {key_url}")
            success = False
            # Clean up potentially incomplete/invalid key file
            keyring_file.unlink(missing_ok=True)

    if success:
        # Ensure key has correct permissions (readable by apt)
        try:
             os.chmod(keyring_file, 0o644)
             logger.info(f"Set permissions 644 on {keyring_file}")
        except OSError as e:
             console.print(f"[bold red]Error:[/bold red] Failed setting permissions on GPG key {keyring_file}: {e}")
             logger.error(f"Failed setting permissions on {keyring_file}: {e}")
             success = False
             keyring_file.unlink(missing_ok=True)
             sources_file.unlink(missing_ok=True) # Also remove sources if key is bad


    if success:
        if not write_file(sources_file, repo_line + "\n", permissions="0644", show_content=True):
            logger.error(f"Failed to write Brave sources file {sources_file}")
            success = False
            # Clean up key file if sources file fails
            keyring_file.unlink(missing_ok=True)


    if success:
        # Update apt cache after adding repo
        if not run_command(['apt-get', 'update', '-qq'], description="apt update after adding Brave repo", show_output=False):
            logger.error("apt-get update failed after adding Brave repository.")
            # Don't necessarily fail the whole step yet, maybe install works anyway or user can fix apt
            console.print("[yellow]Warning:[/yellow] apt-get update failed after adding Brave repo. Install might fail.")


    if success:
        # Install the package
        install_env = os.environ.copy()
        install_env['DEBIAN_FRONTEND'] = 'noninteractive'
        if not run_command(['apt-get', 'install', '-y', 'brave-browser'], description="Installing brave-browser package", env=install_env, show_output=False):
            logger.error("Failed to install brave-browser package.")
            success = False

    # Final check
    if success:
        brave_path_final = shutil.which('brave-browser')
        if brave_path_final:
            console.print(f"[green]✓[/green] Brave Browser installed successfully ([dim]{brave_path_final}[/dim]).")
            logger.info(f"Brave Browser installed successfully at {brave_path_final}.")
            progress.update(task_id, advance=1)
            return True
        else:
             # If install command succeeded but binary not found, something is wrong
             console.print("[bold red]Error:[/bold red] Brave installation commands seemed successful, but 'brave-browser' command is still not found.")
             logger.error("Brave installation reported success, but verification failed.")
             success = False # Mark as failed overall

    # Cleanup if any step failed
    if not success:
        console.print("[bold red]Error:[/bold red] Failed during Brave Browser installation process.")
        logger.error("Brave Browser installation process failed.")
        console.print("[cyan]Attempting to clean up Brave repository files...[/cyan]")
        keyring_file.unlink(missing_ok=True)
        sources_file.unlink(missing_ok=True)
        logger.info("Attempted cleanup of Brave repository files.")
        console.print("Consider running 'sudo apt-get update && sudo apt-get --fix-broken install -y' manually.")
        return False # Fail the step


@installer_step("Setup VNC (xstartup & systemd)")
def step_setup_vnc(progress, task_id, args): # Added args
    """Configures the VNC server xstartup script and systemd service."""
    logger.info(f"Starting VNC setup for user {DEBIAN_USER} on display {VNC_DISPLAY}.")
    # Determine user's home dynamically
    try:
        user_info = pwd.getpwnam(DEBIAN_USER)
        user_home = Path(user_info.pw_dir)
        vnc_dir = user_home / ".vnc"
        vnc_xstartup_path_dynamic = vnc_dir / "xstartup" # Use dynamic path
        vnc_pid_file_dynamic = vnc_dir / f"%H{VNC_DISPLAY}.pid" # Use dynamic path for PID
    except KeyError:
         console.print(f"[bold red]Error:[/bold red] Cannot find user {DEBIAN_USER} to determine home directory for VNC setup.")
         logger.error(f"User {DEBIAN_USER} not found when getting home directory for VNC.")
         return False

    console.print(f"Configuring VNC xstartup script: [cyan]{vnc_xstartup_path_dynamic}[/cyan]...")

    # Ensure .vnc directory exists, created as the user
    try:
        if not run_command(['mkdir', '-p', str(vnc_dir)], user=DEBIAN_USER, description=f"Ensuring VNC directory {vnc_dir} exists"):
            # Check if it exists anyway if command failed
            if not vnc_dir.is_dir():
                 raise OSError(f"Failed to create VNC directory {vnc_dir} as user {DEBIAN_USER}")
            else:
                 logger.warning(f"mkdir failed for {vnc_dir}, but it exists.")
        # Set permissions on .vnc dir? Usually 700.
        run_command(['chmod', '700', str(vnc_dir)], user=DEBIAN_USER, description="Setting VNC directory permissions", check=False)

    except Exception as e:
         console.print(f"[bold red]Error:[/bold red] Failed creating/preparing VNC directory {vnc_dir}: {e}")
         logger.exception(f"Failed creating/preparing VNC directory {vnc_dir}")
         return False


    # Define xstartup content
    # Use gnome-session which should handle Wayland/X11 session types appropriately if available
    xstartup_content = f"""#!/bin/sh

# Start a GNOME Session (works for both X11 and Wayland via Xwayland in recent GNOME)
export XDG_SESSION_DESKTOP=gnome
export GNOME_SHELL_SESSION_MODE=debian
# Unset variables that might interfere if set by vncserver itself
unset SESSION_MANAGER
unset DBUS_SESSION_BUS_ADDRESS

# Source user environment (optional, but can be helpful)
# if [ -f /etc/profile ]; then . /etc/profile; fi
# if [ -f $HOME/.profile ]; then . $HOME/.profile; fi

# Load X resources if they exist
[ -r $HOME/.Xresources ] && xrdb $HOME/.Xresources

# Start gnome-session in the background
gnome-session &

# Optional: Start a terminal or other apps if desired
# gnome-terminal &

"""
    # Write the xstartup file AS ROOT, then chown to user
    if not write_file(vnc_xstartup_path_dynamic, xstartup_content, owner=DEBIAN_USER, permissions="0755"): # Make executable
        console.print("[bold red]Error:[/bold red] Failed to write VNC xstartup script.")
        logger.error(f"Failed writing VNC xstartup script {vnc_xstartup_path_dynamic}")
        return False
    console.print("[green]✓[/green] VNC xstartup script configured.")
    logger.info(f"VNC xstartup script {vnc_xstartup_path_dynamic} configured successfully.")


    # --- VNC Systemd Service ---
    vnc_service_file = Path(f"/etc/systemd/system/vncserver@.service")
    console.print(f"Defining VNC systemd service file: [cyan]{vnc_service_file}[/cyan]")
    try:
        vnc_user_info = pwd.getpwnam(DEBIAN_USER)
        # Use primary group of the user unless DEBIAN_GROUP is different and exists
        primary_gid = vnc_user_info.pw_gid
        vnc_group_name = DEBIAN_USER # Default to user's primary group name
        try:
             vnc_group_name = grp.getgrgid(primary_gid).gr_name
        except KeyError:
             logger.warning(f"Could not find group name for primary GID {primary_gid} of user {DEBIAN_USER}. Using GID directly.")

        vnc_service_group = DEBIAN_GROUP if check_group_exists(DEBIAN_GROUP) else vnc_group_name

        logger.debug(f"Using User={DEBIAN_USER}, Group={vnc_service_group} for VNC service.")
    except KeyError as e:
        console.print(f"[bold red]Error:[/bold red] Cannot find VNC user '{DEBIAN_USER}' needed for service file: {e}")
        logger.critical(f"VNC user '{DEBIAN_USER}' not found.")
        return False

    # Define systemd service content
    # Using Type=forking as vncserver daemonizes
    vnc_service_content = f"""[Unit]
Description=TigerVNC per-display remote desktop service for user {DEBIAN_USER}
Documentation=man:vncserver(1) man:Xvnc(1)
# Start after graphical target and potentially storage/network services are up
After=syslog.target network-online.target graphical.target lvm-activate-data-vg.service mnt-data.mount
Wants=network-online.target

[Service]
Type=forking
User={DEBIAN_USER}
WorkingDirectory={user_home}

# Clean any existing lock files before starting (avoids issues after crash)
ExecStartPre=-/usr/bin/vncserver -kill :%i
ExecStartPre=-/bin/rm -f /tmp/.X%i-lock /tmp/.X11-unix/X%i

# Start VNC Server
# -fg is not needed for Type=forking
# -localhost no # Allows connections from non-localhost (use firewall for security)
# -SecurityTypes VncAuth,TLSVnc # Recommend VncAuth (password) + TLS for encryption if possible
ExecStart=/usr/bin/vncserver :%i \\
    -desktop DebianGNOMEonVNC \\
    -geometry {VNC_GEOMETRY} \\
    -depth {VNC_DEPTH} \\
    -localhost no \\
    -alwaysshared \\
    -SecurityTypes VncAuth \\
    -auth {user_home}/.Xauthority \\
    -pidfile {vnc_pid_file_dynamic} \\
    -xstartup {vnc_xstartup_path_dynamic}

# Specify the PID file location explicitly
PIDFile={vnc_pid_file_dynamic}

# Kill the VNC server process on stop
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

    # --- Reload Daemon & Enable Service ---
    console.print("[cyan]Reloading systemd daemon...[/cyan]")
    if not run_command(['systemctl', 'daemon-reload'], description="Daemon reload"):
        console.print("[yellow]Warning:[/yellow] systemctl daemon-reload failed. Service enablement might require manual reload.")
        logger.warning("daemon-reload failed after writing VNC service file.")
        # Don't fail here, enabling might still work if daemon notices changes

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


@installer_step("Configure Enhanced Samba Server")
def step_configure_samba(progress, task_id, args): # Added args
    """Configures enhanced Samba server for sharing the LVM data volume and root filesystem."""
    logger.info(f"Starting enhanced Samba configuration for share '{SAMBA_SHARE_NAME}' -> {SAMBA_SHARE_PATH}")
    smb_conf_file = Path("/etc/samba/smb.conf")
    console.print(f"Configuring enhanced Samba server with multiple shares in [cyan]{smb_conf_file}[/cyan]...")

    # Backup existing config
    if smb_conf_file.exists():
         backup_file = smb_conf_file.with_suffix(f".bak-{current_timestamp}")
         try:
             # Use copy2 to preserve metadata, then overwrite original
             shutil.copy2(str(smb_conf_file), str(backup_file))
             console.print(f"Backed up existing smb.conf to [cyan]{backup_file}[/cyan]")
             logger.info(f"Backed up {smb_conf_file} to {backup_file}")
         except Exception as e:
             console.print(f"[bold yellow]Warning:[/bold yellow] Could not back up {smb_conf_file}: {e}")
             logger.warning(f"Could not back up {smb_conf_file}: {e}")
             # Ask user if they want to continue and overwrite? For now, continue cautiously.
             if not args.non_interactive and not Confirm.ask(f"Could not back up {smb_conf_file}. Overwrite existing file anyway?", default=False):
                  console.print("[red]Aborted by user due to backup failure.[/red]")
                  logger.error("Samba configuration aborted by user due to backup failure.")
                  return False

    # Verify the share path exists and is a directory (should be mounted by now)
    share_path_obj = Path(SAMBA_SHARE_PATH)
    if not share_path_obj.is_dir():
        console.print(f"[bold red]Error:[/bold red] Samba share path '{SAMBA_SHARE_PATH}' does not exist or is not a directory.")
        console.print("  Ensure LVM volume is mounted correctly (check `df -h` and previous steps).")
        logger.error(f"Samba share path {SAMBA_SHARE_PATH} is not a valid directory. Check mount status.")
        # Try to mount it explicitly?
        mount_result = run_command(['mount', str(share_path_obj)], description=f"Attempting to mount {share_path_obj}", check=False)
        if not mount_result or not share_path_obj.is_dir():
             console.print(f"[bold red]Error:[/bold red] Still cannot access share path {share_path_obj} after mount attempt.")
             return False
        else:
            console.print(f"[yellow]Info:[/yellow] Successfully mounted {share_path_obj}. Continuing Samba setup.")
            logger.info(f"Mounted {share_path_obj} before Samba setup.")

    # Define enhanced smb.conf content with root filesystem sharing
    smb_conf_content = f"""# Enhanced Samba configuration generated by AVF installer ({current_timestamp})
[global]
    workgroup = WORKGROUP
    server string = %h Debian ARM64 Server (Enhanced)
    netbios name = debian-arm64-vm
    
    # Security settings
    security = user
    map to guest = Bad User
    guest account = nobody
    
    # Network settings - Allow connections from private networks
    interfaces = lo eth0 zt+ 192.168.0.0/16 172.16.0.0/12 10.0.0.0/8
    bind interfaces only = no
    hosts allow = 127.0.0.1 192.168.0.0/16 172.16.0.0/12 10.0.0.0/8
    hosts deny = 0.0.0.0/0
    
    # Protocol settings
    min protocol = SMB2
    max protocol = SMB3
    
    # Performance settings
    socket options = TCP_NODELAY IPTOS_LOWDELAY SO_RCVBUF=131072 SO_SNDBUF=131072
    read raw = yes
    write raw = yes
    max xmit = 65535
    dead time = 15
    getwd cache = yes
    
    # Logging
    log file = /var/log/samba/log.%m
    max log size = 1000
    log level = 1
    logging = file
    panic action = /usr/share/samba/panic-action %d
    
    # Authentication
    obey pam restrictions = yes
    unix password sync = yes
    passwd program = /usr/bin/passwd %u
    passwd chat = *Enter\\snew\\s*\\spassword:* %n\\n *Retype\\snew\\s*\\spassword:* %n\\n *password\\supdated\\ssuccessfully* .
    pam password change = yes
    
    # Misc
    dns proxy = no
    load printers = no
    printing = bsd
    printcap name = /dev/null
    disable spoolss = yes
    server role = standalone server

# Root filesystem share (READ-ONLY for security)
[RootFS]
    comment = Root Filesystem (Read-Only for Security)
    path = /
    browseable = yes
    read only = yes
    guest ok = no
    valid users = {DEBIAN_USER} @{DEBIAN_GROUP}
    create mask = 0644
    directory mask = 0755
    follow symlinks = yes
    wide links = yes
    unix extensions = no
    # Hide sensitive directories
    veto files = /lost+found/
    hide dot files = yes

# Root filesystem share (READ-WRITE - DANGEROUS!)
[RootFS-RW]
    comment = Root Filesystem (Read-Write - EXTREMELY DANGEROUS!)
    path = /
    browseable = no
    read only = no
    guest ok = no
    valid users = {DEBIAN_USER}
    admin users = {DEBIAN_USER}
    create mask = 0644
    directory mask = 0755
    follow symlinks = yes
    wide links = yes
    unix extensions = no
    # Hide this share by default and add warnings
    hide dot files = yes
    veto files = /lost+found/
    # Force ownership to prevent system damage
    force user = {DEBIAN_USER}
    force group = {DEBIAN_GROUP}

# Home directory share
[Homes]
    comment = Home Directories
    browseable = no
    read only = no
    create mask = 0700
    directory mask = 0700
    valid users = %S
    follow symlinks = yes

# Data volume share (from existing configuration)
[{SAMBA_SHARE_NAME}]
    comment = Shared Data Volume ({SAMBA_SHARE_PATH})
    path = {SAMBA_SHARE_PATH}
    browseable = yes
    read only = no
    # Guest access disabled by default for better security
    guest ok = no
    # Force created files/dirs to be owned by the target user/group
    force user = {DEBIAN_USER}
    force group = {DEBIAN_GROUP}
    # Set reasonable permissions for created files/dirs (user/group write)
    create mask = 0664
    directory mask = 0775
    # Allow specific users (requires setting Samba password for them)
    valid users = @{DEBIAN_GROUP} {DEBIAN_USER} # Allow user and members of the group
    # Or allow anyone in the group: valid users = @{DEBIAN_GROUP}
    # Write access for the group
    write list = @{DEBIAN_GROUP} {DEBIAN_USER}
"""
    if not write_file(smb_conf_file, smb_conf_content, permissions="0644"):
        console.print("[bold red]Error:[/bold red] Failed to write enhanced Samba configuration file.")
        logger.error(f"Failed writing enhanced Samba configuration {smb_conf_file}")
        return False
    console.print("[green]✓[/green] Enhanced Samba configuration file written.")
    logger.info(f"Enhanced Samba configuration {smb_conf_file} written successfully.")

    console.print("[bold yellow]Action Required:[/bold yellow] Set Samba password for user '{DEBIAN_USER}':")
    console.print(f"  Run: [white on black] sudo smbpasswd -a {DEBIAN_USER} [/white on black]")
    logger.info(f"User needs to set samba password for {DEBIAN_USER} using smbpasswd -a.")

    console.print("[cyan]Verifying enhanced Samba configuration using 'testparm'...[/cyan]")
    testparm_result = run_command(['testparm', '-s'], description="Running testparm", show_output=True, check=False) # -s suppresses questions
    if not testparm_result or testparm_result.returncode != 0:
        # testparm returns 0 even with warnings, check stderr for critical errors usually
        if testparm_result and "ERROR:" in testparm_result.stderr:
            console.print("[bold red]Error:[/bold red] 'testparm' reported critical errors in the Samba configuration. Check output above.")
            logger.error(f"'testparm' reported critical errors: {testparm_result.stderr.strip()}")
            return False
        else:
             console.print("[yellow]Warning:[/yellow] 'testparm' returned non-zero or command failed, but no critical 'ERROR:' found in stderr. Check output carefully.")
             logger.warning(f"testparm returned non-zero ({testparm_result.returncode if testparm_result else 'N/A'}) or failed, but no obvious critical errors.")

    console.print("[cyan]Enabling and restarting Samba services (smbd, nmbd)...[/cyan]")
    logger.info("Enabling and restarting Samba services.")
    # Enable first, then restart (more reliable than enable --now sometimes)
    enable_smbd_ok = run_command(['systemctl', 'enable', 'smbd'], description="Enabling smbd service")
    enable_nmbd_ok = run_command(['systemctl', 'enable', 'nmbd'], description="Enabling nmbd service")

    restart_ok = run_command(['systemctl', 'restart', 'smbd', 'nmbd'], description="Restarting smbd/nmbd", check=False) # Restart might fail if already stopped etc.

    if not (enable_smbd_ok and enable_nmbd_ok):
        console.print("[bold yellow]Warning:[/bold yellow] Failed to enable one or both Samba services (smbd/nmbd). They might not start on boot.")
        logger.warning("Failed to enable smbd or nmbd.")
        # Don't fail step yet, try checking status

    # Check status after restart attempt
    time.sleep(2) # Give services time to start/fail
    smbd_active = run_command(['systemctl', 'is-active', '--quiet', 'smbd'], check=False).returncode == 0
    nmbd_active = run_command(['systemctl', 'is-active', '--quiet', 'nmbd'], check=False).returncode == 0
    logger.info(f"Samba service status check: smbd active = {smbd_active}, nmbd active = {nmbd_active}")

    if smbd_active and nmbd_active:
        console.print("[green]✓[/green] Enhanced Samba services (smbd, nmbd) configured, enabled and are active.")
        console.print("[bold yellow]Available Shares:[/bold yellow]")
        console.print(f"  • [cyan]RootFS[/cyan] - Root filesystem (read-only, secure)")
        console.print(f"  • [cyan]RootFS-RW[/cyan] - Root filesystem (read-write, DANGEROUS!)")
        console.print(f"  • [cyan]Homes[/cyan] - User home directories")
        console.print(f"  • [cyan]{SAMBA_SHARE_NAME}[/cyan] - Data volume")
        console.print("[bold red]WARNING:[/bold red] RootFS-RW share provides full system access - use with extreme caution!")
    else:
        failed_services = []
        if not smbd_active: failed_services.append('smbd')
        if not nmbd_active: failed_services.append('nmbd')
        console.print(f"[bold red]Error:[/bold red] Enhanced Samba configuration applied, but service(s) [{', '.join(failed_services)}] failed to start or are not active.")
        logger.error(f"Samba service(s) not active after configuration: {failed_services}")
        run_command(['systemctl', 'status', 'smbd', 'nmbd', '--no-pager'], description="Samba service status", check=False, show_output=True)
        return False # Fail the step if services aren't running

    logger.info("Enhanced Samba configuration step finished.")
    progress.update(task_id, advance=1)
    return True


@installer_step("Enhanced SSH Configuration")
def step_enhanced_ssh_config(progress, task_id, args):
    """Configures SSH with enhanced security and incorporates existing SSH keys."""
    logger.info("Starting enhanced SSH configuration.")
    
    console.print("[cyan]Configuring SSH with enhanced security...[/cyan]")
    
    # Backup existing SSH configuration
    sshd_config_file = Path("/etc/ssh/sshd_config")
    if sshd_config_file.exists():
        backup_file = sshd_config_file.with_suffix(f".backup-{current_timestamp}")
        try:
            shutil.copy2(str(sshd_config_file), str(backup_file))
            console.print(f"[green]✓[/green] Backed up SSH config to [cyan]{backup_file}[/cyan]")
            logger.info(f"Backed up {sshd_config_file} to {backup_file}")
        except Exception as e:
            console.print(f"[yellow]Warning:[/yellow] Could not backup SSH config: {e}")
            logger.warning(f"Could not backup SSH config: {e}")
    
    # Enhanced SSH configuration
    enhanced_sshd_config = '''# Enhanced SSH configuration for security and functionality

# Basic settings
Port 22
Protocol 2
HostKey /etc/ssh/ssh_host_rsa_key
HostKey /etc/ssh/ssh_host_ecdsa_key
HostKey /etc/ssh/ssh_host_ed25519_key

# Security settings
PermitRootLogin no
PasswordAuthentication yes
PubkeyAuthentication yes
AuthorizedKeysFile .ssh/authorized_keys
PermitEmptyPasswords no
ChallengeResponseAuthentication no
UsePAM yes

# Connection settings
X11Forwarding yes
X11DisplayOffset 10
X11UseLocalhost no
PrintMotd no
AcceptEnv LANG LC_*
Subsystem sftp /usr/lib/openssh/sftp-server

# Performance settings
ClientAliveInterval 60
ClientAliveCountMax 3
MaxAuthTries 6
MaxSessions 10
MaxStartups 10:30:100

# Logging
SyslogFacility AUTH
LogLevel INFO

# Allow specific users
AllowUsers droid

# Compression
Compression yes
'''
    
    if not write_file(sshd_config_file, enhanced_sshd_config, permissions="0644"):
        console.print("[bold red]Error:[/bold red] Failed to write enhanced SSH configuration.")
        logger.error("Failed to write enhanced SSH configuration.")
        return False
    
    # Test SSH configuration
    test_result = run_command(['sshd', '-t'], description="Testing SSH configuration", check=False)
    if not test_result or test_result.returncode != 0:
        console.print("[bold red]Error:[/bold red] SSH configuration test failed.")
        logger.error("SSH configuration test failed.")
        return False
    
    # Restart SSH service
    if not run_command(['systemctl', 'restart', 'ssh'], description="Restarting SSH service"):
        logger.error("Failed to restart SSH service.")
        return False
    
    # Ensure SSH directory exists with proper permissions
    try:
        user_info = pwd.getpwnam(DEBIAN_USER)
        ssh_dir = Path(f"/home/{DEBIAN_USER}/.ssh")
        
        # Create SSH directory as user
        if not run_command(['mkdir', '-p', str(ssh_dir)], user=DEBIAN_USER, description="Ensuring SSH directory exists"):
            logger.error(f"Failed to create SSH directory {ssh_dir}")
            return False
        
        # Set proper permissions
        run_command(['chmod', '700', str(ssh_dir)], user=DEBIAN_USER, description="Setting SSH directory permissions")
        
        # Create/ensure authorized_keys file
        auth_keys_file = ssh_dir / "authorized_keys"
        if not run_command(['touch', str(auth_keys_file)], user=DEBIAN_USER, description="Creating authorized_keys file"):
            logger.warning("Failed to create authorized_keys file")
        else:
            run_command(['chmod', '600', str(auth_keys_file)], user=DEBIAN_USER, description="Setting authorized_keys permissions")
        
        console.print(f"[green]✓[/green] SSH directory and authorized_keys configured for [yellow]{DEBIAN_USER}[/yellow].")
        
    except KeyError:
        console.print(f"[bold red]Error:[/bold red] User {DEBIAN_USER} not found for SSH configuration.")
        logger.error(f"User {DEBIAN_USER} not found for SSH configuration.")
        return False
    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] Failed to configure SSH directory: {e}")
        logger.exception("Failed to configure SSH directory")
        return False
    
    console.print("[green]✓[/green] Enhanced SSH configuration completed.")
    console.print(f"[bold yellow]Action Required:[/bold yellow] Add your public SSH key to [cyan]/home/{DEBIAN_USER}/.ssh/authorized_keys[/cyan]")
    
    logger.info("Enhanced SSH configuration completed.")
    progress.update(task_id, advance=1)
    return True


@installer_step("Enhanced VNC Configuration with Security")
def step_enhanced_vnc_config(progress, task_id, args):
    """Configures VNC with enhanced security and better desktop integration."""
    logger.info(f"Starting enhanced VNC configuration for user {DEBIAN_USER}.")
    
    try:
        user_info = pwd.getpwnam(DEBIAN_USER)
        user_home = Path(user_info.pw_dir)
        vnc_dir = user_home / ".vnc"
        vnc_xstartup_path = vnc_dir / "xstartup"
        vnc_config_path = vnc_dir / "config"
    except KeyError:
        console.print(f"[bold red]Error:[/bold red] Cannot find user {DEBIAN_USER} for VNC configuration.")
        logger.error(f"User {DEBIAN_USER} not found for VNC configuration.")
        return False
    
    console.print(f"[cyan]Configuring enhanced VNC for user [yellow]{DEBIAN_USER}[/yellow]...[/cyan]")
    
    # Ensure VNC directory exists
    if not run_command(['mkdir', '-p', str(vnc_dir)], user=DEBIAN_USER, description="Creating VNC directory"):
        logger.error(f"Failed to create VNC directory {vnc_dir}")
        return False
    
    run_command(['chmod', '700', str(vnc_dir)], user=DEBIAN_USER, description="Setting VNC directory permissions")
    
    # Enhanced VNC startup script
    enhanced_xstartup = '''#!/bin/bash
# Enhanced VNC startup script with better desktop integration

# Unset session manager variables
unset SESSION_MANAGER
unset DBUS_SESSION_BUS_ADDRESS

# Set up environment
export XDG_SESSION_DESKTOP=gnome
export GNOME_SHELL_SESSION_MODE=debian
export XDG_CURRENT_DESKTOP=GNOME
export XDG_SESSION_TYPE=x11

# Load X resources
[ -r $HOME/.Xresources ] && xrdb $HOME/.Xresources
[ -r $HOME/.Xdefaults ] && xrdb -merge $HOME/.Xdefaults

# Set up fonts
xset +fp /usr/share/fonts/X11/misc/
xset +fp /usr/share/fonts/X11/100dpi/
xset +fp /usr/share/fonts/X11/75dpi/
xset +fp /usr/share/fonts/X11/Type1/
xset fp rehash

# Start D-Bus session
if [ -z "$DBUS_SESSION_BUS_ADDRESS" ]; then
    eval $(dbus-launch --sh-syntax --exit-with-session)
fi

# Start window manager
if command -v gnome-session >/dev/null 2>&1; then
    gnome-session &
elif command -v xfce4-session >/dev/null 2>&1; then
    xfce4-session &
elif command -v startlxde >/dev/null 2>&1; then
    startlxde &
else
    # Fallback to basic window manager
    if command -v openbox >/dev/null 2>&1; then
        openbox &
    elif command -v fluxbox >/dev/null 2>&1; then
        fluxbox &
    else
        /usr/bin/x-window-manager &
    fi
fi

# Start file manager
if command -v nautilus >/dev/null 2>&1; then
    nautilus --no-desktop &
elif command -v thunar >/dev/null 2>&1; then
    thunar &
elif command -v pcmanfm >/dev/null 2>&1; then
    pcmanfm &
fi

# Start terminal
if command -v gnome-terminal >/dev/null 2>&1; then
    gnome-terminal &
elif command -v xfce4-terminal >/dev/null 2>&1; then
    xfce4-terminal &
elif command -v lxterminal >/dev/null 2>&1; then
    lxterminal &
else
    xterm &
fi

# Keep the session alive
wait
'''
    
    if not write_file(vnc_xstartup_path, enhanced_xstartup, owner=DEBIAN_USER, permissions="0755"):
        console.print("[bold red]Error:[/bold red] Failed to write enhanced VNC startup script.")
        logger.error("Failed to write enhanced VNC startup script.")
        return False
    
    # VNC configuration file
    vnc_config_content = '''# Enhanced VNC configuration
geometry=1920x1080
depth=24
dpi=96
'''
    
    if not write_file(vnc_config_path, vnc_config_content, owner=DEBIAN_USER, permissions="0644"):
        console.print("[yellow]Warning:[/yellow] Failed to write VNC config file.")
        logger.warning("Failed to write VNC config file.")
    
    # Enhanced VNC systemd service
    vnc_service_file = Path("/etc/systemd/system/vncserver@.service")
    
    try:
        vnc_user_info = pwd.getpwnam(DEBIAN_USER)
        primary_gid = vnc_user_info.pw_gid
        vnc_group_name = DEBIAN_USER
        try:
            vnc_group_name = grp.getgrgid(primary_gid).gr_name
        except KeyError:
            logger.warning(f"Could not find group name for primary GID {primary_gid}")
        
        vnc_service_group = DEBIAN_GROUP if check_group_exists(DEBIAN_GROUP) else vnc_group_name
        
    except KeyError as e:
        console.print(f"[bold red]Error:[/bold red] Cannot find VNC user '{DEBIAN_USER}': {e}")
        logger.critical(f"VNC user '{DEBIAN_USER}' not found.")
        return False
    
    # Enhanced systemd service content
    enhanced_vnc_service = f'''[Unit]
Description=Enhanced TigerVNC server for user {DEBIAN_USER}
Documentation=man:vncserver(1) man:Xvnc(1)
After=syslog.target network-online.target graphical.target
Wants=network-online.target

[Service]
Type=forking
User={DEBIAN_USER}
Group={vnc_service_group}
WorkingDirectory={user_home}

# Clean any existing sessions
ExecStartPre=-/usr/bin/vncserver -kill :%i
ExecStartPre=-/bin/rm -f /tmp/.X%i-lock /tmp/.X11-unix/X%i

# Start VNC Server with enhanced settings
ExecStart=/usr/bin/vncserver :%i \\
    -desktop "Debian-ARM64-Desktop" \\
    -geometry 1920x1080 \\
    -depth 24 \\
    -dpi 96 \\
    -localhost no \\
    -alwaysshared \\
    -SecurityTypes VncAuth \\
    -auth {user_home}/.Xauthority \\
    -xstartup {vnc_xstartup_path}

# Stop VNC server
ExecStop=/usr/bin/vncserver -kill :%i

# Restart policy
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
'''
    
    if not write_file(vnc_service_file, enhanced_vnc_service, permissions="0644"):
        console.print("[bold red]Error:[/bold red] Failed to write enhanced VNC systemd service.")
        logger.error("Failed to write enhanced VNC systemd service.")
        return False
    
    # Reload systemd and enable service
    if not run_command(['systemctl', 'daemon-reload'], description="Reloading systemd daemon"):
        console.print("[yellow]Warning:[/yellow] Failed to reload systemd daemon.")
        logger.warning("Failed to reload systemd daemon.")
    
    vnc_instance_service = f"vncserver@{VNC_DISPLAY_NUM}.service"
    if not run_command(['systemctl', 'enable', vnc_instance_service], description=f"Enabling {vnc_instance_service}"):
        console.print(f"[bold red]Error:[/bold red] Failed to enable VNC service {vnc_instance_service}.")
        logger.error(f"Failed to enable VNC service {vnc_instance_service}.")
        return False
    
    console.print("[green]✓[/green] Enhanced VNC configuration completed.")
    console.print(f"[bold yellow]Action Required:[/bold yellow] Set VNC password for user '{DEBIAN_USER}':")
    console.print(f"  Run: [white on black] sudo -u {DEBIAN_USER} vncpasswd [/white on black]")
    console.print(f"  Start VNC: [white on black] sudo systemctl start {vnc_instance_service} [/white on black]")
    
    logger.info("Enhanced VNC configuration completed.")
    progress.update(task_id, advance=1)
    return True


@installer_step("Setup Podman")
def step_setup_podman(progress, task_id, args): # Added args
    """Configures subordinate UIDs/GIDs, enables linger, and sets up separate rootful/rootless storage paths in the user's home."""
    logger.info(f"Starting Podman setup (subids, linger, separate home-based storage).")
    sub_uid_start = 100000
    sub_gid_start = 100000
    sub_id_count = 65536
    sub_uid_file = Path("/etc/subuid")
    sub_gid_file = Path("/etc/subgid")

    # --- Define Home Directory Paths ---
    try:
        user_info = pwd.getpwnam(DEBIAN_USER)
        user_home = Path(user_info.pw_dir)
        # Need user's primary group for ownership, DEBIAN_GROUP might be secondary
        user_gid = user_info.pw_gid
        user_group_info = grp.getgrgid(user_gid)
        user_primary_group = user_group_info.gr_name
    except KeyError:
         console.print(f"[bold red]Error:[/bold red] Cannot find user {DEBIAN_USER} or primary group to determine home directory/ownership.")
         logger.error(f"User {DEBIAN_USER} or primary group not found.")
         return False

    # Rootless paths (following standard locations within home)
    rootless_config_dir = user_home / ".config/containers"
    rootless_storage_conf_file = rootless_config_dir / "storage.conf"
    rootless_storage_path = user_home / ".local/share/containers/storage"
    # Rootful paths (using separate directories within home, as requested)
    rootful_config_dir = user_home / ".config/containers_root" # Separated name
    rootful_storage_conf_file = rootful_config_dir / "storage.conf"
    rootful_storage_path = user_home / ".local/share/containers_root/storage" # Separated name

    storage_driver = "overlay" # Or choose another if preferred/needed

    # --- 1. Configure Subordinate IDs ---
    console.print(f"Configuring subordinate UIDs/GIDs for rootless user [yellow]{DEBIAN_USER}[/yellow]...")
    logger.info(f"Configuring subuids/subgids for {DEBIAN_USER} in {sub_uid_file}, {sub_gid_file}.")
    sub_id_configured = True
    try:
        # Check and add subuid entry
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

        # Check and add subgid entry
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

    except Exception as e:
        console.print(f"[bold yellow]Warning:[/bold yellow] Could not automatically configure /etc/subuid or /etc/subgid: {e}")
        console.print("  Rootless Podman might not work correctly. Manual configuration may be needed.")
        logger.warning(f"Error configuring subid files: {e}", exc_info=True)
        sub_id_configured = False # Mark as potentially problematic, but continue

    if sub_id_configured:
        console.print("[green]✓[/green] Subordinate IDs configured (or already exist).")
        logger.info("Subordinate ID configuration finished.")

    # --- 2. Enable Linger ---
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
            # Don't fail the step for linger failure, just warn.
    else:
        console.print("[green]✓[/green] Session lingering enabled.")
        logger.info(f"Successfully enabled linger for {DEBIAN_USER}.")

    # --- 3. Configure Rootless Storage ---
    console.print(f"Configuring rootless Podman storage for [yellow]{DEBIAN_USER}[/yellow] -> [cyan]{rootless_storage_path}[/cyan]...")
    logger.info(f"Configuring Podman rootless storage in {rootless_storage_conf_file} pointing to {rootless_storage_path}.")

    try:
        # Need user's UID/GID for write_file later if creating files as root
        # We already got user_info and user_primary_group above

        # Create config directory AS THE USER
        if not run_command(['mkdir', '-p', str(rootless_config_dir)], user=DEBIAN_USER, description=f"Ensuring rootless config dir exists"):
             raise OSError(f"Failed to create rootless config directory {rootless_config_dir} as user {DEBIAN_USER}")
        logger.info(f"Ensured rootless config directory exists: {rootless_config_dir}")

        # Create storage directory parent AS THE USER
        if not run_command(['mkdir', '-p', str(rootless_storage_path.parent)], user=DEBIAN_USER, description=f"Ensuring rootless storage parent dir exists"):
            raise OSError(f"Failed to create rootless storage parent directory {rootless_storage_path.parent} as user {DEBIAN_USER}")
        logger.info(f"Ensured rootless storage parent directory exists: {rootless_storage_path.parent}")
        # The storage dir itself is created by podman later or can be created here too
        if not run_command(['mkdir', '-p', str(rootless_storage_path)], user=DEBIAN_USER, description=f"Ensuring rootless storage dir exists"):
            raise OSError(f"Failed to create rootless storage directory {rootless_storage_path} as user {DEBIAN_USER}")
        logger.info(f"Ensured rootless storage directory exists: {rootless_storage_path}")

    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] Failed setting up directories for rootless Podman: {e}")
        logger.exception(f"Failed setting up directories for rootless Podman {DEBIAN_USER}")
        return False # Fail the step if dirs can't be made

    # Create the rootless storage.conf file
    # Note: Podman often works without this if subids are correct, but creating it makes the path explicit.
    rootless_storage_conf_content = f"""# Podman rootless storage configuration ({rootless_storage_conf_file})
# Managed by AVF installer script ({current_timestamp}) for user {DEBIAN_USER}

[storage]
driver = "{storage_driver}"
graphroot = "{rootless_storage_path}"
# runroot will typically default to XDG_RUNTIME_DIR/containers/storage or similar

[storage.options]
# Add rootless-specific options if needed
# Example for fuse-overlayfs:
# mount_program = "/usr/bin/fuse-overlayfs"

[storage.options.{storage_driver}]
# Options specific to the driver, e.g., overlay options

"""
    # Use write_file to create the config file as root, but chown it to the user/primary_group
    if not write_file(rootless_storage_conf_file, rootless_storage_conf_content,
                      owner=DEBIAN_USER, group=user_primary_group, permissions="0644", show_content=False): # Don't show default content
        console.print(f"[bold red]Error:[/bold red] Failed to write rootless Podman storage configuration file: {rootless_storage_conf_file}")
        logger.error(f"Failed writing rootless Podman storage configuration {rootless_storage_conf_file}")
        return False

    console.print(f"[green]✓[/green] Rootless Podman storage directories and config prepared for [yellow]{DEBIAN_USER}[/yellow].")
    logger.info(f"Rootless Podman storage config prepared at {rootless_storage_conf_file}")

    # --- 4. Setup Directories/Config for Rootful Storage (in Home) ---
    # IMPORTANT: This setup does NOT automatically make 'sudo podman' use this.
    # The user MUST explicitly point 'sudo podman' to this config/storage.
    console.print(f"Preparing directories for rootful Podman storage (non-standard location): [cyan]{rootful_storage_path}[/cyan]...")
    logger.info(f"Preparing non-standard rootful Podman storage location {rootful_storage_path} and config {rootful_storage_conf_file}")

    try:
        # Create config directory AS THE USER (root will use it via flags/env vars)
        if not run_command(['mkdir', '-p', str(rootful_config_dir)], user=DEBIAN_USER, description=f"Ensuring rootful config dir exists"):
             raise OSError(f"Failed to create rootful config directory {rootful_config_dir} as user {DEBIAN_USER}")
        logger.info(f"Ensured rootful config directory exists: {rootful_config_dir}")

        # Create storage directory parent AS THE USER
        if not run_command(['mkdir', '-p', str(rootful_storage_path.parent)], user=DEBIAN_USER, description=f"Ensuring rootful storage parent dir exists"):
            raise OSError(f"Failed to create rootful storage parent directory {rootful_storage_path.parent} as user {DEBIAN_USER}")
        logger.info(f"Ensured rootful storage parent directory exists: {rootful_storage_path.parent}")
        # Create storage directory itself AS THE USER
        if not run_command(['mkdir', '-p', str(rootful_storage_path)], user=DEBIAN_USER, description=f"Ensuring rootful storage dir exists"):
            raise OSError(f"Failed to create rootful storage directory {rootful_storage_path} as user {DEBIAN_USER}")
        logger.info(f"Ensured rootful storage directory exists: {rootful_storage_path}")

    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] Failed setting up directories for home-based rootful Podman: {e}")
        logger.exception(f"Failed setting up directories for home-based rootful Podman for {DEBIAN_USER}")
        return False # Fail the step if dirs can't be made

    # Create the rootful storage.conf file (in the user's home)
    # Define a separate runroot for this non-standard rootful setup
    rootful_runroot = user_home / ".local/run/containers_root/storage"
    rootful_runroot.parent.mkdir(parents=True, exist_ok=True) # Ensure parent exists (as root)
    # Chown the runroot parent to the user? Or leave as root? Leave as root for now.
    # os.chown(rootful_runroot.parent, user_info.pw_uid, user_gid)

    rootful_storage_conf_content = f"""# Podman ROOTFUL storage configuration ({rootful_storage_conf_file})
# Located in user's home - MUST BE SPECIFIED EXPLICITLY when running 'sudo podman'
# e.g., sudo podman --storage-config={rootful_storage_conf_file} --runroot={rootful_runroot} ...
# OR set environment variables:
# export CONTAINERS_STORAGE_CONF={rootful_storage_conf_file}
# export CONTAINERS_RUNROOT={rootful_runroot}
# Managed by AVF installer script ({current_timestamp})

[storage]
driver = "{storage_driver}"
graphroot = "{rootful_storage_path}"
runroot = "{rootful_runroot}" # Use a separate runroot within home too

[storage.options]
# Add rootful-specific options if needed

[storage.options.{storage_driver}]
# Options specific to the driver

"""
    # Use write_file to create the config file as root, but chown it to the user/primary_group
    if not write_file(rootful_storage_conf_file, rootful_storage_conf_content,
                      owner=DEBIAN_USER, group=user_primary_group, permissions="0644", show_content=False): # Don't show default content
        console.print(f"[bold red]Error:[/bold red] Failed to write home-based rootful Podman storage configuration file: {rootful_storage_conf_file}")
        logger.error(f"Failed writing home-based rootful Podman storage configuration {rootful_storage_conf_file}")
        return False

    console.print(f"[green]✓[/green] Home-based rootful Podman storage directories and config file prepared.")
    console.print(f"  [bold yellow]IMPORTANT:[/bold yellow] To use this rootful storage, run podman with sudo and explicitly specify config/runroot:")
    console.print(f"  Method 1 (Flags): [white on black] sudo podman --storage-config={rootful_storage_conf_file} --runroot={rootful_runroot} info [/white on black]")
    console.print(f"  Method 2 (Env Vars): Set [white on black]CONTAINERS_STORAGE_CONF[/white on black] and [white on black]CONTAINERS_RUNROOT[/white on black] before `sudo podman`.")
    logger.info(f"Home-based rootful Podman storage prepared at {rootful_storage_conf_file}. User must specify this path and runroot ({rootful_runroot}) when running sudo podman.")


    # --- 5. Final Info ---
    console.print(f"[green]✓[/green] Podman setup complete. Rootless uses standard home paths, rootful prepared in separate home path (requires explicit config/runroot).")
    logger.info("Podman setup step finished successfully.")
    progress.update(task_id, advance=1)
    return True


@installer_step("Final Cleanup & System Optimization")
def step_cleanup(progress, task_id, args): # Added args
    """Performs final cleanup tasks, system optimization, and verification."""
    logger.info("Starting final cleanup and system optimization step.")
    
    console.print("[cyan]Performing final cleanup and system optimization...[/cyan]")
    
    # Clean APT cache
    clean_env = os.environ.copy()
    clean_env['DEBIAN_FRONTEND'] = 'noninteractive'
    if run_command(['apt-get', 'clean'], env=clean_env, description="Cleaning APT cache"):
        console.print("[green]✓[/green] APT cache cleaned.")
        logger.info("APT cache cleaned successfully.")
    else:
        console.print("[yellow]Warning:[/yellow] 'apt-get clean' command failed.")
        logger.warning("'apt-get clean' failed.")
    
    # Remove unnecessary packages
    if run_command(['apt-get', 'autoremove', '-y'], env=clean_env, description="Removing unnecessary packages"):
        console.print("[green]✓[/green] Unnecessary packages removed.")
        logger.info("Unnecessary packages removed successfully.")
    else:
        console.print("[yellow]Warning:[/yellow] 'apt-get autoremove' failed.")
        logger.warning("'apt-get autoremove' failed.")
    
    # Update locate database if available
    if shutil.which('updatedb'):
        console.print("[cyan]Updating locate database...[/cyan]")
        updatedb_result = run_command(['updatedb'], description="Updating locate database", check=False, timeout=300)
        if updatedb_result and updatedb_result.returncode == 0:
            console.print("[green]✓[/green] Locate database updated.")
        else:
            console.print("[yellow]Warning:[/yellow] Failed to update locate database.")
    
    # Update man database
    if shutil.which('mandb'):
        console.print("[cyan]Updating man database...[/cyan]")
        mandb_result = run_command(['mandb', '-q'], description="Updating man database", check=False, timeout=180)
        if mandb_result and mandb_result.returncode == 0:
            console.print("[green]✓[/green] Man database updated.")
        else:
            console.print("[yellow]Warning:[/yellow] Failed to update man database.")
    
    # Verify key services are running
    console.print("[cyan]Verifying key services...[/cyan]")
    key_services = ['ssh', 'smbd', 'docker']
    service_status = {}
    
    for service in key_services:
        status_result = run_command(['systemctl', 'is-active', '--quiet', service], check=False, description=f"Checking {service} status")
        service_status[service] = status_result and status_result.returncode == 0
        
        if service_status[service]:
            console.print(f"[green]✓[/green] {service} service is active")
        else:
            console.print(f"[yellow]⚠[/yellow] {service} service is not active")
    
    # Create system information script
    try:
        user_info = pwd.getpwnam(DEBIAN_USER)
        user_home = Path(user_info.pw_dir)
        sysinfo_script = user_home / "system-info.sh"
        
        sysinfo_content = '''#!/bin/bash
# System Information Script
# Generated by Ultima-interactive.py installer

echo "=== Debian ARM64 System Information ==="
echo "Date: $(date)"
echo "Uptime: $(uptime -p)"
echo "Kernel: $(uname -r)"
echo "Architecture: $(uname -m)"
echo ""

echo "=== Hardware Information ==="
echo "CPU: $(lscpu | grep 'Model name' | cut -d: -f2 | xargs)"
echo "Memory: $(free -h | grep '^Mem:' | awk '{print $2 " total, " $3 " used, " $7 " available"}')"
echo "Disk Usage: $(df -h / | tail -1 | awk '{print $2 " total, " $3 " used, " $4 " available (" $5 " used)"}')"
echo ""

echo "=== Network Information ==="
echo "IP Addresses:"
ip -4 addr show | grep -oP '(?<=inet\\s)\\d+(\\.\\d+){3}' | grep -v 127.0.0.1
echo ""

echo "=== Docker Information ==="
if command -v docker >/dev/null 2>&1; then
    echo "Docker Version: $(docker --version)"
    echo "Docker Status: $(systemctl is-active docker)"
    echo "Docker Images: $(docker images --format 'table {{.Repository}}\\t{{.Tag}}\\t{{.Size}}' 2>/dev/null || echo 'None')"
else
    echo "Docker: Not installed"
fi
echo ""

echo "=== Services Status ==="
for service in ssh smbd docker vncserver@1; do
    status=$(systemctl is-active $service 2>/dev/null || echo "inactive")
    echo "$service: $status"
done
echo ""

echo "=== Storage Information ==="
echo "LVM Status:"
if command -v vgs >/dev/null 2>&1; then
    vgs 2>/dev/null || echo "No volume groups found"
    lvs 2>/dev/null || echo "No logical volumes found"
else
    echo "LVM not available"
fi
echo ""

echo "=== Samba Shares ==="
if command -v smbclient >/dev/null 2>&1; then
    smbclient -L localhost -N 2>/dev/null | grep -E "Disk|IPC" || echo "No shares available"
else
    echo "Samba client not available"
fi
'''
        
        if write_file(sysinfo_script, sysinfo_content, owner=DEBIAN_USER, permissions="0755"):
            console.print(f"[green]✓[/green] System information script created at [cyan]{sysinfo_script}[/cyan]")
        else:
            console.print("[yellow]Warning:[/yellow] Failed to create system information script.")
            
    except Exception as e:
        console.print(f"[yellow]Warning:[/yellow] Failed to create system information script: {e}")
        logger.warning(f"Failed to create system information script: {e}")
    
    # Set up log rotation for installer logs
    logrotate_config = Path("/etc/logrotate.d/avf-installer")
    logrotate_content = f'''{LOG_FILENAME} {{
    weekly
    rotate 4
    compress
    delaycompress
    missingok
    notifempty
    create 644 root root
}}
'''
    
    if write_file(logrotate_config, logrotate_content, permissions="0644"):
        console.print("[green]✓[/green] Log rotation configured for installer logs.")
    else:
        console.print("[yellow]Warning:[/yellow] Failed to configure log rotation.")
    
    logger.info("Final cleanup and system optimization finished.")
    progress.update(task_id, advance=1)
    return True


# --- Main Execution Logic ---
def main():
    """Main function to orchestrate the installation process."""

    # --- Argument Parsing ---
    parser = argparse.ArgumentParser(
        description="Interactive installer for Ultimate AVF Debian Setup.",
        formatter_class=argparse.RawDescriptionHelpFormatter # Keep formatting in help
    )
    parser.add_argument(
        '--non-interactive', '-y', # Add short flag -y as well
        action='store_true',      # Store True if flag is present
        help='Run in non-interactive mode, assuming yes to confirmations (use with caution!).'
    )
    args = parser.parse_args()
    # --- End Argument Parsing ---


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
    logger.info(f"Command line arguments: {sys.argv}")
    logger.info(f"Parsed arguments: {args}")

    # --- Modified Initial Confirmation ---
    if not args.non_interactive:
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
    else:
        console.print("[yellow]Running in non-interactive mode. Skipping initial confirmation.[/yellow]")
        logger.info("Running in non-interactive mode (--non-interactive). Skipping initial confirmation.")
    # --- End Modified Initial Confirmation ---


    total_steps = len(installer_steps)
    console.print(f"\n[bold green]Starting installation process ({total_steps} steps)...[/bold green]")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console,
        transient=False, # Keep progress visible after completion
    ) as progress:
        overall_task = progress.add_task("[bold green]Overall Progress[/bold green]", total=total_steps)
        all_steps_successful = True

        # Verify step order reflects dependencies (Enable Services should be after LVM/Fstab)
        # This check seems reasonable to keep.
        step_titles = [s['title'] for s in installer_steps]
        try:
            lvm_idx = step_titles.index("Configure LVM (Create if Needed)")
            fstab_idx = step_titles.index("Configure Mount Point & fstab")
            enable_svc_idx = step_titles.index("Enable Storage Persistence Services")
            if not (enable_svc_idx > lvm_idx and enable_svc_idx > fstab_idx):
                 console.print("[bold red]INTERNAL ERROR: Step order incorrect. Enable Storage Services must come after LVM and Fstab config.[/bold red]")
                 logger.critical("Installer step order incorrect regarding Enable Storage Services.")
                 sys.exit(99)
        except ValueError:
             console.print("[bold red]INTERNAL ERROR: One of the storage configuration steps is missing.[/bold red]")
             logger.critical("One or more essential storage steps missing from installer_steps list.")
             sys.exit(98)

        for i, step_info in enumerate(installer_steps):
            step_title = step_info['title']
            step_func = step_info['func']
            step_number = i + 1
            task_description = f"Step {step_number}/{total_steps}: {step_title}"
            # Add task but don't start it immediately, let the step function advance it
            step_task = progress.add_task(task_description, total=1, start=False, visible=True)

            console.print(Rule(f"[bold cyan]Starting: {step_title}[/bold cyan] ({step_number}/{total_steps})"))
            logger.info(f"Starting step ({step_number}/{total_steps}): {step_title}")
            progress.start_task(step_task) # Mark task as started visually

            step_success = False
            try:
                # Pass the parsed arguments object to the step function
                step_success = step_func(progress, step_task, args)
            except Exception as step_exception:
                 logger.exception(f"Critical error occurred within step: {step_title}")
                 console.print(f"[bold red]Fatal Error:[/bold red] Unexpected error during step '{step_title}':")
                 console.print_exception(show_locals=False, word_wrap=True)
                 step_success = False

            if step_success:
                # Ensure task shows 100% completed state
                if not progress.tasks[step_task].finished:
                     progress.update(step_task, completed=1)
                # Update description to show success
                progress.update(step_task, description=f"[green]✓ {step_title}[/green]")
                progress.stop_task(step_task) # Stop spinner, keep completed bar
                progress.update(overall_task, advance=1)
                logger.info(f"Successfully completed step: {step_title}")
                console.print(Rule(f"[bold green]Finished: {step_title}[/bold green]"))
            else:
                # Mark task as failed
                progress.update(step_task, description=f"[bold red]✗ Failed: {step_title}[/bold red]")
                progress.stop_task(step_task) # Stop spinner, keep failed bar
                # Don't advance overall progress

                console.print(Panel(
                    f"[bold red]Error during step: '{step_title}'.[/bold red]\nInstallation cannot continue.\nPlease check the output above and logs for details:\n[dim]{LOG_FILENAME}[/dim]",
                    title="Installation Failed",
                    border_style="red",
                    expand=False
                ))
                logger.critical(f"Failed step: {step_title}. Aborting installation.")
                all_steps_successful = False
                progress.update(overall_task, description="[bold red]Overall Progress (Failed)[/bold red]")
                # Keep progress bar visible on failure
                # progress.stop()
                break # Exit the loop

            time.sleep(0.5) # Small pause between steps

        # After the loop finishes
        if not all_steps_successful:
             # Save console output on failure
             try:
                 html_log = f"installer_error_console_{current_timestamp}.html"
                 console.save_html(html_log)
                 console.print(f"\n[yellow]Tip:[/yellow] Detailed console output saved to [dim]'{html_log}'[/dim] for review.")
             except Exception as save_err:
                 logger.warning(f"Could not save console HTML log on failure: {save_err}")
             sys.exit(1) # Exit with error code


    # If all steps completed successfully
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


    # Post-Installation Info - Updated with all new features
    # Get paths again dynamically for the summary message
    try:
        user_info = pwd.getpwnam(DEBIAN_USER)
        user_home = Path(user_info.pw_dir)
        rootless_storage_conf_file = user_home / ".config/containers/storage.conf"
        rootful_config_dir = user_home / ".config/containers_root"
        rootful_storage_conf_file = rootful_config_dir / "storage.conf"
        rootful_runroot = user_home / ".local/run/containers_root/storage" # As defined in step
        starship_config = user_home / ".config/starship.toml"
        sysinfo_script = user_home / "system-info.sh"
    except KeyError:
         # Should not happen if install succeeded, but handle gracefully
         rootless_storage_conf_file = f"/home/{DEBIAN_USER}/.config/containers/storage.conf (approx)"
         rootful_storage_conf_file = f"/home/{DEBIAN_USER}/.config/containers_root/storage.conf (approx)"
         rootful_runroot = f"/home/{DEBIAN_USER}/.local/run/containers_root/storage (approx)"
         starship_config = f"/home/{DEBIAN_USER}/.config/starship.toml (approx)"
         sysinfo_script = f"/home/{DEBIAN_USER}/system-info.sh (approx)"

    console.print(Rule("[bold yellow]Post-Installation Actions & Reminders[/bold yellow]"))
    
    # Essential Setup Actions
    console.print("[bold cyan]🔧 Essential Setup Actions:[/bold cyan]")
    console.print(f"- [key] SSH Key:[/key] Add your public SSH key to [cyan]/home/{DEBIAN_USER}/.ssh/authorized_keys[/cyan]")
    console.print(f"- [key] VNC Password:[/key] Set VNC password: [white on black] sudo -u {DEBIAN_USER} vncpasswd [/white on black]")
    console.print(f"- [key] Samba Password:[/key] Set Samba password: [white on black] sudo smbpasswd -a {DEBIAN_USER} [/white on black]")
    console.print(f"- [network] ZeroTier Auth:[/network] Authorize device in ZeroTier Central for network [cyan]{ZT_NETWORK_ID}[/cyan]")
    
    # New Docker & Containerization Features
    console.print("\n[bold cyan]🐳 Docker & Containerization:[/bold cyan]")
    console.print(f"- [docker] Docker Status:[/docker] [white on black] sudo systemctl status docker [/white on black]")
    console.print(f"- [docker] Test Docker:[/docker] [white on black] sudo -u {DEBIAN_USER} docker run hello-world [/white on black]")
    console.print(f"- [docker] Test x86 Emulation:[/docker] [white on black] sudo -u {DEBIAN_USER} docker run --platform linux/amd64 hello-world [/white on black]")
    console.print(f"- [docker] Multi-arch Build:[/docker] [white on black] docker buildx create --use [/white on black]")
    console.print(f"- [storage] Podman Rootless:[/storage] Config: [cyan]{rootless_storage_conf_file}[/cyan]")
    console.print(f"- [storage] Podman Rootful:[/storage] Config: [cyan]{rootful_storage_conf_file}[/cyan]")
    console.print(f"    Use: [white on black] sudo podman --storage-config={rootful_storage_conf_file} --runroot={rootful_runroot} ... [/white on black]")
    
    # Enhanced Network Sharing
    console.print("\n[bold cyan]🌐 Enhanced Network Sharing:[/bold cyan]")
    console.print(f"- [network] Samba Shares Available:[/network]")
    console.print(f"    • [cyan]RootFS[/cyan] - Root filesystem (read-only, secure)")
    console.print(f"    • [cyan]RootFS-RW[/cyan] - Root filesystem (read-write, [bold red]DANGEROUS![/bold red])")
    console.print(f"    • [cyan]Homes[/cyan] - User home directories")
    console.print(f"    • [cyan]{SAMBA_SHARE_NAME}[/cyan] - Data volume")
    console.print(f"- [network] Access Shares:[/network] [white on black] \\\\\\\\<server-ip>\\\\RootFS [/white on black] (Windows) or [white on black] smb://<server-ip>/RootFS [/white on black] (Linux)")
    
    # Package Management & Tools
    console.print("\n[bold cyan]📦 Package Management & Tools:[/bold cyan]")
    console.print(f"- [package] Tasksel:[/package] [white on black] sudo tasksel [/white on black] (task-based package selection)")
    console.print(f"- [package] Aptitude:[/package] [white on black] sudo aptitude [/white on black] (advanced package manager)")
    console.print(f"- [package] Search Files:[/package] [white on black] apt-file search <filename> [/white on black]")
    console.print(f"- [package] Find Orphans:[/package] [white on black] deborphan [/white on black]")
    
    # Shell & User Experience
    console.print("\n[bold cyan]🚀 Shell & User Experience:[/bold cyan]")
    console.print(f"- [shell] Starship Prompt:[/shell] Config at [cyan]{starship_config}[/cyan]")
    console.print(f"- [shell] Enhanced Aliases:[/shell] Available in .bashrc (ll, la, dps, di, sysinfo, etc.)")
    console.print(f"- [shell] System Info:[/shell] [white on black] {sysinfo_script} [/white on black] or [white on black] neofetch [/white on black]")
    
    # Services & System Management
    console.print("\n[bold cyan]⚙️ Services & System Management:[/bold cyan]")
    console.print(f"- [desktop] VNC Service:[/desktop] [white on black] sudo systemctl start vncserver@{VNC_DISPLAY_NUM}.service [/white on black]")
    console.print(f"- [desktop] VNC Connect:[/desktop] VNC client to [cyan]<server-ip>:590{VNC_DISPLAY_NUM}[/cyan]")
    console.print(f"- [storage] LVM Volume:[/storage] Mounted at [cyan]{LVM_MOUNT_POINT}[/cyan] - [white on black] df -h {LVM_MOUNT_POINT} [/white on black]")
    console.print(f"- [system] Service Status:[/system] [white on black] systemctl status ssh smbd docker [/white on black]")
    
    # Security & Access
    console.print("\n[bold cyan]🔒 Security & Access:[/bold cyan]")
    console.print(f"- [security] SSH Config:[/security] Enhanced security in [cyan]/etc/ssh/sshd_config[/cyan]")
    console.print(f"- [security] VNC Security:[/security] Password-protected, localhost disabled for network access")
    console.print(f"- [security] Samba Security:[/security] User authentication required, private network access")
    console.print(f"- [security] Firewall:[/security] Consider configuring [white on black] ufw [/white on black] for additional security")
    
    # Architecture & Emulation
    console.print("\n[bold cyan]🏗️ Architecture & Emulation:[/bold cyan]")
    console.print(f"- [arch] Native:[/arch] ARM64/aarch64 architecture")
    console.print(f"- [arch] x86 Emulation:[/arch] QEMU user static + binfmt support enabled")
    console.print(f"- [arch] Test Emulation:[/arch] [white on black] file /usr/bin/qemu-x86_64-static [/white on black]")
    console.print(f"- [arch] Verify Formats:[/arch] [white on black] update-binfmts --display [/white on black]")
    
    # Final Recommendations
    console.print("\n[bold cyan]🎯 Final Recommendations:[/bold cyan]")
    console.print("- [system] Reboot:[/system] [bold]Recommended[/bold] to ensure all services start correctly: [white on black] sudo reboot [/white on black]")
    console.print(f"- [backup] Backup:[/backup] Consider backing up [cyan]/home/{DEBIAN_USER}[/cyan] and [cyan]/etc[/cyan] directories")
    console.print(f"- [monitoring] Monitoring:[/monitoring] Use [white on black] htop [/white on black], [white on black] docker stats [/white on black], [white on black] systemctl status [/white on black]")
    console.print(f"- [log] Logs:[/log] Installation logs: [dim]{LOG_FILENAME}[/dim]")
    
    console.print(Rule())
    console.print("[bold green]🎉 Installation completed successfully! Your Debian ARM64 system is now fully enhanced with:")
    console.print("   • Docker CE with x86 emulation support")
    console.print("   • Enhanced Samba sharing (including root filesystem)")
    console.print("   • Advanced package management tools")
    console.print("   • Starship cross-shell prompt")
    console.print("   • Enhanced SSH and VNC configurations")
    console.print("   • Comprehensive system optimizations")
    console.print("[/bold green]")
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
         # Try to save console output on critical failure
         try:
              html_log = f"installer_CRITICAL_error_console_{current_timestamp}.html"
              console.save_html(html_log)
              console.print(f"\n[yellow]Tip:[/yellow] Detailed console output saved to [dim]'{html_log}'[/dim] for review.")
         except Exception as save_err:
              logger.warning(f"Could not save console HTML log on critical failure: {save_err}")
         sys.exit(2)
