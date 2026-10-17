#!/usr/bin/env bash
# Create the admin and test users on the local Forgejo and save their
# passwords and API tokens to .env.local (never committed).
#
#   ./setup_users.sh [user ...]      default users: geoadmin nino giorgi
set -euo pipefail
cd "$(dirname "$0")"
container=geogit-forgejo
env_file=.env.local
users=("$@")
[ ${#users[@]} -eq 0 ] && users=(geoadmin nino giorgi)

until docker exec -u git "$container" forgejo admin user list >/dev/null 2>&1; do sleep 1; done
touch "$env_file"
chmod 600 "$env_file"

for u in "${users[@]}"; do
    key=$(echo "$u" | tr '[:lower:]' '[:upper:]')
    if grep -q "^${key}_TOKEN=" "$env_file"; then
        echo "$u: already set up"
        continue
    fi
    pass=$(head -c 18 /dev/urandom | base64 | tr -d '/+=')
    admin=""
    [ "$u" = "${users[0]}" ] && admin="--admin"
    docker exec -u git "$container" forgejo admin user create --username "$u" --password "$pass" \
        --email "$u@geogit.test" --must-change-password=false $admin >/dev/null
    token=$(docker exec -u git "$container" forgejo admin user generate-access-token --username "$u" \
        --token-name geogit --scopes all --raw)
    printf '%s_PASSWORD=%s\n%s_TOKEN=%s\n' "$key" "$pass" "$key" "$token" >> "$env_file"
    echo "$u: created${admin:+ (admin)}"
done
echo "passwords and tokens: $(pwd)/$env_file"
