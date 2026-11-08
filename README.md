# PIPLAY

PIPLAY turns a Raspberry Pi and a small LCD into a **desktop video player and AirPlay receiver**. I built it to leave short clips playing on my desk and switch to screen mirroring whenever I wanted to show something from my iPhone or iPad.

**Version v1.0.0**

## What PIPLAY does

- **Play local video.** PIPLAY reads MP4 files from the microSD card and plays them directly on the attached LCD. It shuffles the library by default. From the dashboard, you can play a specific clip, loop it, queue it, or skip to the next one.
- **Receive AirPlay.** Turn on AirPlay in the dashboard, select PIPLAY from Screen Mirroring on your Apple device, and enter the displayed PIN. PIPLAY pauses local playback while AirPlay is active and resumes it when you turn AirPlay off.

## Web dashboard

Use a browser on the same Wi-Fi network to manage the video library, upload MP4s, and switch between local playback and AirPlay. The dashboard also lets you schedule the display by day and time, check microSD space, and change the device name, language, and schedule time zone.

English is the default, with Spanish available in setup, sign-in, and settings. On the LCD used for this build, turning the display off stops playback and blanks the image. The backlight stays on, and the Pi remains reachable over Wi-Fi.

## Hardware

- Raspberry Pi 3 Model B Rev 1.2.
- 3.5-inch, 480 × 320 SPI LCD with the `fb_ili9486` driver and `/dev/fb1` framebuffer.
- microSD for the OS and MP4 library, plus Wi-Fi for the dashboard on port 8080.

## How it works

`app/player.py` sends local video to the framebuffer with FFmpeg and runs UxPlay for AirPlay. `app/display.py` stores and applies the display schedule. `app/panel.py` serves the dashboard and talks to the player over a Unix socket. The player and dashboard run as separate systemd services.

## Set up another Raspberry Pi

The example units assume a Linux account named `admin` and an LCD already working at `/dev/fb1`. Install Python 3.9 or later, FFmpeg with fbdev output, and DejaVu Sans. For AirPlay, install UxPlay with GStreamer and the fbdev sink.

1. Create `/home/admin/desk-screen/videos`, make it writable by `admin`, and add your MP4 files.
2. Install the files in `app/` as root-owned files in `/opt/desk-screen-panel/`. Copy the units in `systemd/` to `/etc/systemd/system/`.
3. Create `/etc/desk-screen-panel.conf` with `LAN_CIDR=192.168.1.0/24`, adjusted for your network. Optionally set `LOCAL_NAME=rpi3.local` if that name resolves on your network.
4. Generate a setup code with `sudo sh -c 'umask 077; openssl rand -hex 16 > /etc/desk-screen-setup.code'`.
5. Run `sudo systemctl daemon-reload` and `sudo systemctl enable --now desk-screen.service desk-screen-panel.service`. Open the Pi’s local IP address on port 8080.
6. Read the setup code with `sudo cat /etc/desk-screen-setup.code`. In the wizard, enter the code and choose a device name, web username, and password of at least 12 characters. The code can only complete setup once. Keep the file so systemd can load the example unit.

The dashboard uses HTTP on the LAN. Complete setup on a trusted network. The web account is separate from the Linux account. For manual upgrades, the panel still supports an existing `TOKEN_FILE` with the web username `admin`.
