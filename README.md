# AVF Pro / AVF+ (Android Virtualization Framework Pro): Advanced Linux Environment for Android AVF

**Transform the basic Android Virtualization Framework (AVF) Linux terminal into a powerful, persistent, and fully-featured Linux desktop and development environment running directly on your phone.**

[![Android Version](https://img.shields.io/badge/Android-16%2B%20(Beta)-brightgreen)](https://developer.android.com/about/versions/16)
[![Platform](https://img.shields.io/badge/Platform-Pixel%209%20(Tested)-blue)](https://store.google.com/product/pixel_9)
[![Linux Distro](https://img.shields.io/badge/Linux-Debian%20Based-orange)](https://www.debian.org/)
[![Virtualization](https://img.shields.io/badge/Virtualization-AVF%20/%20crosvm-lightgrey)](https://source.android.com/docs/core/virtualization)

---

**Problem:** Google introduced a basic Debian terminal for developers via AVF in Android 16 Beta 3. While promising, it's heavily restricted, lacks documentation, suffers from severe storage limitations, and misses essential features for practical use.

**Solution:** Project Terminus overcomes these limitations through deep system analysis, reverse engineering, and advanced Linux techniques, providing a truly usable Linux environment on your Android device.

---

## Showcase

*(Include compelling screenshots or GIFs here)*

* **Example 1:** Screenshot of a full XFCE or GNOME desktop running via VNC, showing applications like a file manager, terminal, and browser.
* **Example 2:** GIF demonstrating launching a graphical application (like VS Code or GIMP) within the Linux environment.
* **Example 3:** Screenshot of `df -h` output inside the VM showing the significantly expanded storage available thanks to the custom LVM setup.
* **Example 4:** Screenshot of a development server (e.g., Node.js) running and being accessed, or an IDE compiling code.

---

## Features

* **Full Linux Desktop:** Run standard desktop environments (XFCE4, GNOME) accessible via VNC.
* **Massive Storage Expansion:** Overcomes the default small disk limit using a robust LVM-on-`qcow2`-via-`nbd` solution, providing ample space for applications and development work.
* **Persistent Environment:** Your files, applications, and configurations persist across VM restarts.
* **Complete Development Toolchains:** Pre-configured or easily installable environments for Python, Node.js, C/C++, Go, Rust, and more.
* **Run Standard Linux Apps:** Install and use tools like VS Code, Firefox/Brave, Docker (experimental), office suites, and even experiment with running local LLMs.
* **Automated Setup:** Complex configurations are handled by sophisticated automation scripts.
* **Seamless Integration:** Leverages `virtiofs` for straightforward file sharing between Android and the Linux VM (where supported).

---

## Technical Highlights & Skills Demonstrated

This project required navigating a highly undocumented and evolving Android feature. Key technical challenges and accomplishments include:

* **Deep AVF Analysis:** Reverse engineering the VM boot process, configuration (`vm_config.json`), and initial ramdisk (`initrd.img`) behavior without official documentation.
* **Hypervisor Interaction:** Capturing and analyzing low-level `crosvm` commands to understand secure resource provisioning (disks, networking via file descriptors) and Device Tree Overlay usage.
* **Circumventing Restrictions:** Modifying guest-side `systemd` services, permissions, and environment variables to achieve necessary control and bypass limitations.
* **Novel Storage Solution:** Designing and implementing a complex but reliable method to attach and integrate large, flexible `qcow2` disk images using `qemu-nbd` and managing them seamlessly with LVM within the guest. This was crucial to overcoming the default storage constraints.
* **Robust Automation:** Developing comprehensive shell scripts and custom `systemd` services for automated first-boot setup, desktop environment configuration, toolchain installation, and ensuring reliable operation.
* **Full-Stack Problem Solving:** Addressing issues across multiple layers: Android host, AVF framework, `crosvm` hypervisor, Linux kernel, Debian guest OS, storage (LVM, `qcow2`, `nbd`), networking, and application setup.
* **Packaging & Reproducibility:** Developing methods for backing up the environment and structuring the automation for potential deployment.

---

## Motivation

The standard AVF terminal, while a step forward, felt like a locked room. I wanted to unlock its full potential, driven by the challenge of working within an undocumented, restricted environment and the desire to create a truly powerful mobile Linux workstation. This project showcases deep technical curiosity, advanced problem-solving, and the ability to engineer complex, practical solutions.

---

## Status

* **Experimental:** Built on Android 16 Beta features. Subject to change as Google updates AVF.
* **Tested Primarily On:** Google Pixel 9 (may work on other devices supporting AVF).

---

## Getting Started & Accessing the Solution

Manually replicating this environment is highly complex due to the lack of official documentation, the need for specific versions of tools, and the intricate nature of the storage and automation setup.

**To provide a streamlined, reliable, and easy-to-use experience, Project Terminus is available as [Your App Name], a dedicated Android application.**

**AVF Pro / AVF+ (Android Virtualization Framework Pro)** handles the complex setup, provides the necessary pre-configured components, and manages the environment for you.

**(Option A: Direct Link - Preferred if App is Ready)**
[**>> Get  AVF Pro / AVF+ (Android Virtualization Framework Pro) on the Google Play Store <<**](YOUR_PLAY_STORE_LINK_HERE)

**(Option B: Landing Page Link - Good for pre-launch or more info)**
[**>> Learn More & Get Access on Our Project Website <<**](YOUR_WEBSITE_OR_LANDING_PAGE_LINK_HERE)

**(Option C: More Generic - If linking directly isn't possible yet)**
*Details on accessing the streamlined setup solution can be found at [YOUR_WEBSITE_OR_CONTACT_INFO].*

---

## License

The underlying components (Android Virtualization Framework, Linux kernel, Debian, `crosvm`, `qemu`, LVM, etc.) are governed by their respective open-source licenses.

The specific integration methods, advanced automation scripts, and the streamlined setup process developed for **Project Terminus** are proprietary and distributed through the **[Your App Name]** application / associated project website.

---

## Author / Contact

* **Cedric Vallieu / @Smacksmack206**
* **https://www.linkedin.com/in/cedric-signifyd**
