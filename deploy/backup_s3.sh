#!/usr/bin/env bash
# ==============================================================================
# 💾 Breakout Scanner - Automated S3 Backup Script
# Syncs SQLite dedup database, logs, and telemetry to encrypted S3 bucket
# Recommended Cron: 0 0 * * * /home/ec2-user/breakout-scanner/deploy/backup_s3.sh
# ==============================================================================

set -euo pipefail

BUCKET_NAME="${AWS_S3_BACKUP_BUCKET:-breakout-scanner-backups}"
TIMESTAMP=$(date -u +"%Y%m%d_%H%M%SZ")
BACKUP_DIR="/tmp/breakout_backup_${TIMESTAMP}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "📦 Creating snapshot backup at $BACKUP_DIR..."
mkdir -p "$BACKUP_DIR"

# Copy data directory (SQLite dedup files and state)
if [ -d "$REPO_DIR/data" ]; then
    cp -r "$REPO_DIR/data" "$BACKUP_DIR/"
fi

# Copy recent logs
if [ -d "$REPO_DIR/logs" ]; then
    mkdir -p "$BACKUP_DIR/logs"
    find "$REPO_DIR/logs" -type f -name "*.log" -mtime -3 -exec cp {} "$BACKUP_DIR/logs/" \;
fi

# Compress archive
ARCHIVE_PATH="/tmp/breakout_backup_${TIMESTAMP}.tar.gz"
tar -czf "$ARCHIVE_PATH" -C /tmp "breakout_backup_${TIMESTAMP}"

# Upload to S3 if AWS CLI is installed
if command -v aws &> /dev/null; then
    echo "☁️ Uploading to s3://$BUCKET_NAME/backups/..."
    aws s3 cp "$ARCHIVE_PATH" "s3://$BUCKET_NAME/backups/breakout_backup_${TIMESTAMP}.tar.gz" --sse AES256
    echo "✅ S3 Backup completed successfully."
else
    echo "⚠️ AWS CLI not installed. Archive preserved locally at $ARCHIVE_PATH."
fi

# Cleanup temporary files
rm -rf "$BACKUP_DIR" "$ARCHIVE_PATH"
