#!/bin/bash
set -e

SERVER="root@204.168.230.16"

echo "📦 Deploying Igor..."

ssh "$SERVER" bash -s <<'EOF'
cd /root/assistant
git pull
source venv/bin/activate
pip install -q -r requirements.txt
systemctl restart assistant
echo "✅ Deploy done. Service status:"
systemctl is-active assistant
EOF
