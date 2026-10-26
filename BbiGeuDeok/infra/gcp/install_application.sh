#!/usr/bin/env bash
set -euo pipefail
staging=${BBIGEUDEOK_STAGING_DIR:?Set the private staging directory}
test -f "$staging/application-private.tar.gz"
test -d /srv/bbigeudeok/app
test "$(findmnt -n -o SOURCE /srv/bbigeudeok)" = /dev/sdb
tar --no-same-owner -xzf "$staging/application-private.tar.gz" -C /srv/bbigeudeok/app
chown -R root:bbigeudeok /srv/bbigeudeok/app
find /srv/bbigeudeok/app -type d -exec chmod 750 {} +
find /srv/bbigeudeok/app -type f -exec chmod 640 {} +
install -d -o bbigeudeok -g bbigeudeok -m 700 /srv/bbigeudeok/app/Code/sk_admin_mapping/outputs/validation/p0_runtime
/opt/bbigeudeok-venv/bin/pip install --disable-pip-version-check --no-input -r /srv/bbigeudeok/app/infra/gcp/requirements-linux.txt
/opt/bbigeudeok-venv/bin/pip freeze > /srv/bbigeudeok/state/requirements-resolved.txt
chmod 640 /srv/bbigeudeok/state/requirements-resolved.txt
chown root:bbigeudeok /srv/bbigeudeok/state/requirements-resolved.txt
for unit in bbigeudeok-backend.service bbigeudeok-gateway.service bbigeudeok-funnel.service bbigeudeok-health.service bbigeudeok-health.timer; do
  install -o root -g root -m 644 "/srv/bbigeudeok/app/infra/gcp/$unit" "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemd-analyze verify /etc/systemd/system/bbigeudeok-{backend,gateway,funnel,health}.service /etc/systemd/system/bbigeudeok-health.timer
printf 'APPLICATION_INSTALLED_NOT_YET_STARTED\n'
