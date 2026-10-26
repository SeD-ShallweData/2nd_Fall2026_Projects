#!/usr/bin/env bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
data_device=/dev/disk/by-id/google-bbigeudeok-data
test -b "$data_device"
test "$(lsblk -bdno SIZE "$data_device")" = 32212254720
if ! blkid "$data_device" >/dev/null 2>&1; then
  test -z "$(lsblk -no MOUNTPOINT "$data_device")"
  mkfs.ext4 -q -L bbigeudeok-data "$data_device"
fi
test "$(blkid -s TYPE -o value "$data_device")" = ext4
mkdir -p /srv/bbigeudeok
data_uuid=$(blkid -s UUID -o value "$data_device")
if ! grep -q "UUID=$data_uuid " /etc/fstab; then
  printf 'UUID=%s /srv/bbigeudeok ext4 defaults,nofail 0 2\n' "$data_uuid" >> /etc/fstab
fi
mountpoint -q /srv/bbigeudeok || mount /srv/bbigeudeok
apt-get update -qq
apt-get install -y -qq python3-venv python3-pip curl ca-certificates gnupg
if ! id bbigeudeok >/dev/null 2>&1; then
  useradd --system --home-dir /srv/bbigeudeok --shell /usr/sbin/nologin bbigeudeok
fi
install -d -o bbigeudeok -g bbigeudeok -m 700 /srv/bbigeudeok/state
install -d -o root -g bbigeudeok -m 750 /srv/bbigeudeok/app /etc/bbigeudeok
if ! command -v tailscale >/dev/null; then
  curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/noble.noarmor.gpg -o /usr/share/keyrings/tailscale-archive-keyring.gpg
  curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/noble.tailscale-keyring.list -o /etc/apt/sources.list.d/tailscale.list
  apt-get update -qq
  apt-get install -y -qq tailscale
fi
systemctl enable --now tailscaled
if ! test -x /opt/bbigeudeok-venv/bin/python; then
  python3 -m venv /opt/bbigeudeok-venv
fi
printf 'BOOTSTRAP_READY\n'
free -b
df -h /srv/bbigeudeok
