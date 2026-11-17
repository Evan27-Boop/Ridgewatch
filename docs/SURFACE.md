# Surface Go 2 setup

How the reference display is set up: a Surface Go 2 running Arch Linux that boots
straight into Ridgewatch in under 10 seconds. Touch, sound and the GPS all work on the
standard Arch kernel; the linux-surface kernel isn't needed.

Do the steps over SSH from another computer where you can, so you can paste. Steps that
start cage must be typed on the Surface itself.

## 1. Network

```
iwctl station wlan0 connect "YourWiFi"
sudo systemctl enable --now iwd systemd-resolved
sudo ln -sf /run/systemd/resolve/stub-resolv.conf /etc/resolv.conf
```

Connect to your phone's hotspot once the same way. iwd remembers it and joins on its own
when it's in range, which is how the dashboard gets data in the vehicle.

## 2. Packages and permissions

```
sudo pacman -Syu python git cage foot ttf-dejavu alsa-utils openssh polkit
sudo usermod -aG uucp evan                  # GPS serial port; log out and back in
sudo systemctl enable --now sshd systemd-timesyncd
sudo timedatectl set-timezone America/New_York
```

The clock matters: day and night colors, alert times and data ages all depend on it.
polkit lets Ridgewatch shut the Surface down cleanly when the ignition turns off.

## 3. Ridgewatch

```
gh repo clone Evan27-Boop/Ridgewatch ridgewatch     # or: git clone https://github.com/...
```

To update later: `cd ~/ridgewatch && git pull`, then reboot.

## 4. Font size

foot's default size (8) gives 213×69 on this screen: it fits, but the text is tiny at
arm's length. Size 13 gives about 131×42, which keeps the big digits full height with
the button bar.

```
mkdir -p ~/.config/foot
echo 'font=DejaVu Sans Mono:size=13' > ~/.config/foot/foot.ini
```

## 5. Sound

Ridgewatch plays its beeps with `aplay` (from alsa-utils) and turns the volume all the
way up when it starts. Check the speakers work:

```
aplay -l                                    # should list the internal sound card
aplay ~/.config/ridgewatch/alert.wav        # the warning double beep (made on first run)
aplay ~/.config/ridgewatch/beep.wav         # the status beep
```

If `aplay -l` lists no cards, install `sof-firmware` and reboot. alsa-utils saves the
volume at shutdown and restores it at boot.

## 6. Boot straight into Ridgewatch

Log in automatically on the first console:

```
sudo mkdir -p /etc/systemd/system/getty@tty1.service.d
sudo tee /etc/systemd/system/getty@tty1.service.d/autologin.conf <<'EOF'
[Service]
ExecStart=
ExecStart=-/usr/bin/agetty -o '-p -f -- \\u' --noclear --autologin evan - $TERM
EOF
sudo systemctl daemon-reload
```

Start Ridgewatch on that console only:

```
cat >> ~/.bash_profile <<'EOF'

if [ "$(tty)" = /dev/tty1 ]; then
  exec cage -s -- foot ~/ridgewatch/rw --follow
fi
EOF
```

`-s` lets you switch consoles. If Ridgewatch quits or crashes, the system logs back in
and starts it again.

## 7. Keep the logs small

The system log otherwise grows without limit on a small disk:

```
sudo mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nSystemMaxUse=50M\n' | sudo tee /etc/systemd/journald.conf.d/size.conf
sudo systemctl restart systemd-journald
```

## 8. Firmware: Battery Limit

A tablet that is always plugged in wears out its battery. Turn on Battery Limit, which
stops charging at 50%: hold **Volume Up** and press **Power** to open the Surface UEFI,
then **Boot configuration → Kiosk Mode → Battery Limit: Enabled**. The top bar then reads
PLUGGED IN instead of CHARGING.

## In the vehicle

- **Charger:** at least 24 W (USB-C PD at 15 V, or a Surface Connect car charger). A
  phone-style USB port can't keep up and the battery drains while it runs.
- **Ignition off:** Ridgewatch counts down 10 minutes, then shuts down. Press the power
  button to start it again.
- **Heat:** there is no fan. Keep it out of direct sun and don't leave it in a parked car
  on a hot day.

## Maintenance

- **A console:** Fn+Ctrl+Alt+F2, log in. Fn+Ctrl+Alt+F1 goes back to Ridgewatch.
- **Remotely:** `ssh evan@<address>`. Find the address with `ip -4 addr show wlan0`.
- **Run something under cage by hand:** from a console, as evan (not root, whose home
  folder is different): `cage -s -- foot <command>`.
