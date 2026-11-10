# ☁️ Ultra Low-Cost AWS Singapore Deployment Guide (<= $15/month)

> **Target Architecture:** Single EC2 Linux Instance in **AWS Singapore (`ap-southeast-1`)**  
> **Instance Type:** `t4g.small` (2 vCPU, 2 GiB ARM64 Graviton) at **~$12.30/mo**, or `t4g.micro` (1 GiB) at **~$6.15/mo**  
> **Storage:** 10 GiB gp3 EBS Root Volume at **~$0.85/mo**  
> **Total Monthly Bill:** **~$7.00 – $14.50 / month** (Strictly $\le \$15$)

---

## 🛑 Cost Guardrails to Guarantee $\le \$15/\text{month}$

1. **NO NAT Gateway**: Never create a NAT Gateway (costs ~$32/mo). Use a standard **Default VPC Public Subnet** with a public IPv4.
2. **NO Paid CloudWatch / SSM**: Basic CloudWatch metrics (5-min intervals) are 100% free. Keep credentials in a local `.env` file instead of paid SSM parameter lookups.
3. **Burstable CPU Savings**: Heartbeat runs at `:58` and candle scans trigger at `:01`. The remaining **56 minutes of every hour the daemon sleeps in deep `time.sleep()`**, maintaining $0.01\%$ CPU and banking burst credits.

---

## 1. Launching the EC2 Instance

1. Open AWS Console in **Singapore (`ap-southeast-1`)**.
2. Go to **EC2 $\to$ Launch Instances**:
   - **Name:** `breakout-scanner-prod`
   - **AMI:** `Amazon Linux 2023 AMI` (or Ubuntu 22.04 LTS)
   - **Architecture:** `64-bit (Arm)`
   - **Instance type:** `t4g.small` (or `t4g.micro`)
   - **Key pair:** Select your SSH key
   - **Network settings:** Default VPC, Auto-assign public IP: **Enable**
   - **Storage:** 10 GiB gp3
3. Click **Launch Instance**.

---

## 2. One-Click Bootstrap on EC2

SSH into your EC2 instance and run:

```bash
# Clone the repository
git clone https://github.com/allaparthinaveen/breakout-scanner.git
cd breakout-scanner

# Run automated bootstrap (installs Python 3.11, sets up 2GB swap, configures systemd)
chmod +x deploy/setup_ec2.sh deploy/backup_s3.sh
./deploy/setup_ec2.sh
```

---

## 3. Configure Telegram Secrets (Zero AWS SSM Overhead)

Create your `.env` file in `/home/ec2-user/breakout-scanner/.env`:

```bash
cat << 'EOF' > .env
TG_BOT_TOKEN="your_telegram_bot_token_here"
TG_CHAT_ID="your_telegram_chat_id_here"
EOF

chmod 600 .env
```

*(Note: `.env` is ignored by `.gitignore` and never committed to GitHub).*

---

## 4. Starting the Service

```bash
# Start background daemon
sudo systemctl start breakout-scanner

# Verify status
sudo systemctl status breakout-scanner

# Check live health status
python3 production_daemon.py --status

# Follow multi-market live scan logs
tail -f logs/cloud_daemon.log
```

---

## 5. Docker Compose Option (Alternative)

If you prefer containerized deployment:

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f
```
