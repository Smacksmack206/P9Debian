@installer_step("Configure User Groups & Xorg Wrapper")
def step_groups_xorg(progress, task_id):
    """Adds target user to necessary groups and configures Xwrapper."""
    logger.info(f"Starting user group and Xwrapper configuration for {DEBIAN_USER}.")
    groups_to_add = ['input', 'video', 'tty', 'sudo', 'disk', DEBIAN_GROUP]
    console.print(f"Adding user [yellow]{DEBIAN_USER}[/yellow] to required groups: [cyan]{', '.join(groups_to_add)}[/cyan]...")

    # Run usermod -aG to add user to groups
    usermod_result = run_command(['usermod', '-aG', ','.join(groups_to_add), DEBIAN_USER], description="Adding user to groups", check=False) # check=False as it might return error if already in groups
    if not usermod_result or usermod_result.returncode != 0:
        # Verify if user is already in groups before declaring failure
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
                # Decide if fatal? If sudo or disk missing, likely fatal.
                if "sudo" in missing_groups or "disk" in missing_groups:
                    console.print(f"[bold red]Fatal Error:[/bold red] Failed to add user to critical group(s): {missing_groups}.")
                    return False
        else:
            console.print("[bold yellow]Warning:[/bold yellow] 'usermod' command failed, and could not verify current groups.")
            logger.warning(f"usermod failed for {DEBIAN_USER} and group verification failed.")
            # Assume non-fatal for now, but risky
    else:
        console.print("[green]✓[/green] User added to groups (or already present).")
        logger.info(f"Successfully added {DEBIAN_USER} to groups {groups_to_add}.")
        # Verify visually
        run_command(['groups', DEBIAN_USER], description=f"Verifying groups for {DEBIAN_USER}", show_output=True)

    # Configure /etc/X11/Xwrapper.config
    xwrapper_conf = Path("/etc/X11/Xwrapper.config")
    allowed_line = "allowed_users=anybody"
    console.print(f"Configuring Xorg session permissions in [cyan]{xwrapper_conf}[/cyan]...")
    logger.info(f"Configuring {xwrapper_conf} to set '{allowed_line}'.")
    needs_update = False
    content = ""
    try: # <--- Outer TRY block for file operations
        if xwrapper_conf.exists():
            content = xwrapper_conf.read_text()
            # Check if line exists, ignoring leading/trailing whitespace and comments
            line_found = any(line.strip() == allowed_line for line in content.splitlines() if line.strip() and not line.strip().startswith('#'))
            if not line_found:
                needs_update = True
                logger.info(f"'{allowed_line}' not found in existing {xwrapper_conf}.")
            else:
                 logger.info(f"'{allowed_line}' already present in {xwrapper_conf}.")
        else:
            needs_update = True # Create file if it doesn't exist
            logger.info(f"{xwrapper_conf} does not exist, creating.")

        if needs_update:
            console.print(f"Adding/Ensuring line '[yellow]{allowed_line}[/yellow]' in {xwrapper_conf}...")
            # Append the line, ensuring a newline before it if content exists
            if content and not content.endswith('\n'):
                 content += "\n"
            content += f"{allowed_line}\n"
            # Write the updated content back to the file
            if not write_file(xwrapper_conf, content, permissions="0644", show_content=False): # Don't show full file content
                 console.print(f"[bold red]Error:[/bold red] Failed to write updated {xwrapper_conf}.")
                 logger.error(f"Failed writing updated {xwrapper_conf}")
                 return False # Exit function if write fails
            console.print(f"[green]✓[/green] {xwrapper_conf} updated.")
            logger.info(f"Successfully updated {xwrapper_conf}.")
        else:
             console.print(f"[green]✓[/green] Xwrapper config '{allowed_line}' already correctly set.")
             # Ensure permissions are correct even if no content change needed
             if xwrapper_conf.exists():
                  try: # Nested try for chmod specifically
                      os.chmod(xwrapper_conf, 0o644)
                  except OSError as e: # except for nested try
                      logger.warning(f"Could not ensure permissions on existing {xwrapper_conf}: {e}")
                      # Don't make failure to chmod existing file fatal, just warn

    # --- ADDED Except block for the outer try ---
    except Exception as e:
         # Catch potential errors during read_text() or other file operations
         console.print(f"[bold red]Error:[/bold red] Failed during Xwrapper config read/write operation: {e}")
         logger.exception(f"Failed reading/writing Xwrapper config {xwrapper_conf}") # Log traceback
         return False # Exit function on error
    # --- END ADDED Except block ---

    # This code now runs only if the try block completed without returning False or raising an unhandled exception
    logger.info("User group and Xwrapper configuration finished.")
    progress.update(task_id, advance=1)
    return True
