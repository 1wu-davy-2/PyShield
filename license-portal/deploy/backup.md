# PyShield 授权门户：备份与恢复

本文适用于 `license-portal` 的默认单机部署：

- 数据目录：`/var/lib/pyshield-portal`
- 配置目录：`/etc/pyshield-portal`
- 服务名：`pyshield-license-portal`
- 数据库：`/var/lib/pyshield-portal/portal.sqlite3`（WAL 模式）

目标不是“拷贝一个数据库文件”，而是同时保住**授权数据、加密密钥、签发密钥和配置**，并且能在另一台机器上恢复出可签发、可验签、可登录管理端的完整服务。

## 1. 备份范围

| 文件/目录 | 内容 | 丢失后果 | 备份方式 |
| --- | --- | --- | --- |
| `portal.sqlite3` | 客户、申请、授权、激活码、订单、审计、管理员、会话 | 丢失全部业务记录；客户端已有本地授权在到期前仍可离线使用，但服务端状态变 unknown | SQLite 在线 `.backup` 或 `VACUUM INTO` |
| `portal.secret` | 派生激活码哈希、激活码加密、IP 哈希、会话/CSRF 密钥的 32 字节主密钥 | 已生成的未激活激活码无法再校验或展示；管理员会话全部失效；数据库里的 `key_encrypted` 不可恢复 | 与数据库**成对**备份 |
| `signing.key` | 口令加密的 Ed25519 签发私钥 | 无法签发新授权/续费文档；已有本地授权到期前仍可用 | 单独加密备份，与口令分开保管 |
| `signing.pass` | 自动生成的签发口令（如果使用） | 无法解密 `signing.key` | 单独密封保管；生产建议删除并改用环境变量/credential |
| `/etc/pyshield-portal/config.json` | 站点、试用参数、SMTP/Turnstile 等配置 | 需要重建配置；SMTP/Turnstile/白名单可能失效 | 单独加密备份 |
| `/etc/pyshield-portal/portal.env` | SMTP 密码、Turnstile Secret、签发口令等 | 需要重新配置；签发口令丢失可能导致 `signing.key` 无法解密 | 单独加密备份，权限 0600 |
| 备份加密私钥/口令 | 解密备份所需 | 备份全部不可读 | 离线保存，至少两份异地 |

> **不要**把 `signing.key` 和它的口令放进同一个未加密压缩包；也不要让备份加密密钥长期留在同一台服务器上。

## 2. SQLite 在线备份

### 2.1 推荐：`.backup`

`.backup` 使用 SQLite 在线备份 API，在服务运行时也能得到事务一致的快照，不需要停机。建议先创建只有 `pyshield` 可写的临时目录：

```bash
sudo install -d -o pyshield -g pyshield -m 0700 /var/lib/pyshield-portal/.backup

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
sudo -u pyshield sqlite3 /var/lib/pyshield-portal/portal.sqlite3 \
  ".backup '/var/lib/pyshield-portal/.backup/portal-$STAMP.sqlite3'"

# 验证备份可打开且结构完整
sudo -u pyshield sqlite3 "/var/lib/pyshield-portal/.backup/portal-$STAMP.sqlite3" \
  'PRAGMA integrity_check;'
```

期望输出 `ok`。如果输出不是 `ok`，不要删除上一份可用备份。

### 2.2 备选：`VACUUM INTO`

SQLite 3.27+ 支持在线生成紧凑副本：

```bash
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
sudo -u pyshield sqlite3 /var/lib/pyshield-portal/portal.sqlite3 \
  "VACUUM INTO '/var/lib/pyshield-portal/.backup/portal-$STAMP.sqlite3';"
```

`VACUUM INTO` 需要目标文件不存在，且会重写数据库，耗时可能比 `.backup` 长；客户量大时优先 `.backup`。

### 2.3 WAL checkpoint 与冷备份

在线 `.backup` 不需要 checkpoint。只有在“停服务后直接复制文件”的冷备份场景才需要处理 WAL：

```bash
# 可选：把 WAL 内容合并回主库，减少需要复制的文件
sudo -u pyshield sqlite3 /var/lib/pyshield-portal/portal.sqlite3 \
  'PRAGMA wal_checkpoint(TRUNCATE);'
```

然后**停止服务**再复制：

```bash
sudo systemctl stop pyshield-license-portal
sudo install -d -o root -g root -m 0700 /path/to/cold-backup
sudo cp -a /var/lib/pyshield-portal/portal.sqlite3 /path/to/cold-backup/
for suffix in -wal -shm; do
  [ -e "/var/lib/pyshield-portal/portal.sqlite3$suffix" ] && \
    sudo cp -a "/var/lib/pyshield-portal/portal.sqlite3$suffix" /path/to/cold-backup/
done
sudo systemctl start pyshield-license-portal
```

如果服务仍在运行，**绝不能只复制 `portal.sqlite3` 一个文件**：WAL 中未合并的事务会丢失，副本可能损坏或回退到旧状态。

## 3. 加密、命名与保留

### 3.1 分组打包

建议至少分成三组：

1. `portal.sqlite3` + `portal.secret`：必须来自同一时间点，否则激活码哈希/密文无法对应。
2. `signing.key`：单独一组；对应的口令/`signing.pass` 放在另一个安全位置。
3. `config.json` + `portal.env`：包含 SMTP/Turnstile/签发口令，单独加密。

示例（在数据目录的 `.backup` 暂存区完成）：

```bash
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
cd /var/lib/pyshield-portal/.backup
sudo -u pyshield cp -a ../portal.secret ./portal.secret
sudo -u pyshield tar -czf "portal-data-$STAMP.tar.gz" \
  "portal-$STAMP.sqlite3" portal.secret
sudo chown root:root "portal-data-$STAMP.tar.gz"
sudo chmod 0600 "portal-data-$STAMP.tar.gz"
sha256sum "portal-data-$STAMP.tar.gz" | sudo tee "portal-data-$STAMP.tar.gz.sha256"
```

### 3.2 加密

先创建备份目录：

```bash
sudo install -d -o root -g root -m 0700 /var/backups/pyshield-portal
```

优先使用 `age`：

```bash
age -r age1替换为你的备份公钥 \
  -o /var/backups/pyshield-portal/portal-data-$STAMP.tar.gz.age \
  /var/lib/pyshield-portal/.backup/portal-data-$STAMP.tar.gz
```

或 GPG：

```bash
gpg --encrypt --recipient backup@example.com \
  --output /var/backups/pyshield-portal/portal-data-$STAMP.tar.gz.gpg \
  /var/lib/pyshield-portal/.backup/portal-data-$STAMP.tar.gz
```

或 OpenSSL（口令不要直接写在命令行，使用权限 0600 的口令文件）：

```bash
openssl enc -aes-256-cbc -pbkdf2 -salt \
  -pass file:/root/.backup-passphrase \
  -in /var/lib/pyshield-portal/.backup/portal-data-$STAMP.tar.gz \
  -out /var/backups/pyshield-portal/portal-data-$STAMP.tar.gz.enc
```

加密后删除暂存明文，并确认退出码：

```bash
rm -f /var/lib/pyshield-portal/.backup/portal-data-$STAMP.tar.gz \
      /var/lib/pyshield-portal/.backup/portal-$STAMP.sqlite3
```

### 3.3 保留与异地

示例保留策略：

- 每日 1 份，保留 14 天；
- 每周 1 份，保留 8 周；
- 每月 1 份，保留 12 个月；
- 每季度做一次恢复演练。

清理示例（按实际命名调整）：

```bash
find /var/backups/pyshield-portal -name 'portal-*.age' -mtime +14 -delete
```

把加密后的文件同步到异地对象存储/另一台主机（restic、borg、rclone 均可）。**加密必须在离开本机之前完成**。备份目录不要放在数据目录、Web 根目录或 Nginx 可访问路径下。

## 4. 自动化示例

`/usr/local/sbin/pyshield-portal-backup`：

```bash
#!/usr/bin/env bash
set -euo pipefail

DATA=/var/lib/pyshield-portal
STAGE=$DATA/.backup
DEST=/var/backups/pyshield-portal
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
AGE_RECIPIENT=${AGE_RECIPIENT:?set AGE_RECIPIENT}

install -d -o pyshield -g pyshield -m 0700 "$STAGE"
install -d -o root -g root -m 0700 "$DEST"

# Consistent online snapshot
sudo -u pyshield sqlite3 "$DATA/portal.sqlite3" \
  ".backup '$STAGE/portal-$STAMP.sqlite3'"
sudo -u pyshield sqlite3 "$STAGE/portal-$STAMP.sqlite3" \
  'PRAGMA integrity_check;' | grep -qx ok

# DB and portal.secret must be backed up together
sudo -u pyshield cp -a "$DATA/portal.secret" "$STAGE/portal.secret"
sudo -u pyshield tar -C "$STAGE" -czf "$STAGE/portal-data-$STAMP.tar.gz" \
  "portal-$STAMP.sqlite3" portal.secret

# Encrypt before leaving the host
age -r "$AGE_RECIPIENT" \
  -o "$DEST/portal-data-$STAMP.tar.gz.age" \
  "$STAGE/portal-data-$STAMP.tar.gz"
sha256sum "$DEST/portal-data-$STAMP.tar.gz.age" > "$DEST/portal-data-$STAMP.tar.gz.age.sha256"

# Signing key is a separate backup group; never bundle its passphrase with it.
age -r "$AGE_RECIPIENT" \
  -o "$DEST/signing-key-$STAMP.age" "$DATA/signing.key"

rm -rf "$STAGE"
find "$DEST" -name 'portal-*.age' -mtime +14 -delete
find "$DEST" -name 'signing-key-*.age' -mtime +14 -delete
```

systemd timer：

```ini
# /etc/systemd/system/pyshield-portal-backup.service
[Unit]
Description=Backup PyShield License Portal

[Service]
Type=oneshot
Environment=AGE_RECIPIENT=age1替换为你的备份公钥
ExecStart=/usr/local/sbin/pyshield-portal-backup
```

```ini
# /etc/systemd/system/pyshield-portal-backup.timer
[Unit]
Description=Daily PyShield License Portal backup

[Timer]
OnCalendar=*-*-* 03:17:00
RandomizedDelaySec=10m
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
sudo chmod 0700 /usr/local/sbin/pyshield-portal-backup
sudo systemctl daemon-reload
sudo systemctl enable --now pyshield-portal-backup.timer
systemctl list-timers pyshield-portal-backup.timer
```

无论用哪种调度方式，都要监控失败并告警；只“配置了定时任务”不等于备份可用。

## 5. 恢复

### 5.1 恢复前

1. 记录当前时间、备份时间点和业务影响范围。
2. 尽量停止写入：

   ```bash
   sudo systemctl stop pyshield-license-portal
   ```

3. 把当前数据目录**改名保留**，不要直接覆盖：

   ```bash
   sudo mv /var/lib/pyshield-portal /var/lib/pyshield-portal.old-$(date -u +%Y%m%dT%H%M%SZ)
   sudo install -d -o pyshield -g pyshield -m 0700 /var/lib/pyshield-portal
   ```

4. 解密备份到暂存目录，并验证校验和/完整性。

### 5.2 恢复数据

```bash
# 以 age 为例
age -d -i /root/backup-key.txt \
  -o /tmp/portal-data.tar.gz \
  /var/backups/pyshield-portal/portal-data-20260101T031700Z.tar.gz.age

sudo tar -C /var/lib/pyshield-portal -xzf /tmp/portal-data.tar.gz

# 备份包中的数据库文件名带时间戳，恢复为服务期望的 portal.sqlite3
sudo mv /var/lib/pyshield-portal/portal-*.sqlite3 \
        /var/lib/pyshield-portal/portal.sqlite3

sudo chown -R pyshield:pyshield /var/lib/pyshield-portal
sudo chmod 0700 /var/lib/pyshield-portal
sudo chmod 0600 /var/lib/pyshield-portal/portal.sqlite3 \
                 /var/lib/pyshield-portal/portal.secret
sudo -u pyshield sqlite3 /var/lib/pyshield-portal/portal.sqlite3 'PRAGMA integrity_check;'
```

如果恢复的是 `.backup` 出来的独立数据库文件：

- 应在启动前删除旧的 `portal.sqlite3-wal` 和 `portal.sqlite3-shm`，避免旧 WAL 覆盖新库；
- 确认 `portal.secret` 与数据库来自同一备份点。

恢复 `signing.key`（如备份中保留）：

```bash
sudo install -o pyshield -g pyshield -m 0600 \
  /path/to/restored/signing.key /var/lib/pyshield-portal/signing.key
```

恢复配置：

```bash
sudo install -d -o root -g pyshield -m 0750 /etc/pyshield-portal
sudo install -o root -g pyshield -m 0640 \
  /path/to/restored/config.json /etc/pyshield-portal/config.json
sudo install -o root -g root -m 0600 \
  /path/to/restored/portal.env /etc/pyshield-portal/portal.env
```

确认 `portal.env` 中的 `PYSHELD_PORTAL_SIGNING_PASSPHRASE` 与恢复的 `signing.key` 匹配。如果不匹配，`signing.key` 无法解密，服务会报“签发密钥解密失败，口令可能不正确”。

### 5.3 启动与验证

```bash
sudo systemctl daemon-reload
sudo systemctl start pyshield-license-portal
sudo systemctl status pyshield-license-portal
sudo journalctl -u pyshield-license-portal -n 100 --no-pager
curl -fsS https://license.example.com/health
```

核对：

1. 日志中的 `signing key id` 与备份/构建记录一致。
2. `signing show` 的公钥指纹与客户端 `_tool_key.py` 注入值一致：

   ```bash
   sudo -u pyshield env \
     PYTHONPATH=/opt/pyshield-portal/license-portal:/opt/pyshield-portal \
     PYSHELD_PORTAL_SIGNING_PASSPHRASE='...' \
     /opt/pyshield-portal/.venv/bin/python -m license_portal signing show \
     --config /etc/pyshield-portal/config.json
   ```

3. 管理端可登录，客户/授权/激活码数量与备份时间点相符。
4. 用一个测试序列号完成试用申请、领取、激活、刷新和吊销。
5. 检查备份点之后是否发生过需要补做的操作：新授权、激活、续费、吊销、退款/订单状态。

### 5.4 恢复后清理

- 确认无误后再删除 `.old-*` 目录；其中可能含敏感数据，删除前先安全擦除或加密归档。
- 删除 `/tmp` 中的明文恢复包。
- 若恢复了旧数据库，重新执行备份点之后的吊销操作，并核对订单/支付记录。
- 记录本次恢复的时间线、备份点、影响范围和后续动作。

## 6. 常见故障场景

| 场景 | 影响 | 处理建议 |
| --- | --- | --- |
| 只有数据库损坏，`portal.secret`/`signing.key` 完好 | 业务记录丢失，签发能力仍在 | 恢复最近一致的数据库 + `portal.secret`；核对并补做备份点之后的吊销/订单 |
| `portal.secret` 丢失/损坏 | 未激活激活码无法再校验或展示，管理员会话失效；已签发文档仍可被客户端离线验证 | 从备份恢复 `portal.secret`；若确实没有备份，只能接受旧激活码作废并重新发码 |
| `signing.key` 丢失但口令还在 | 无法签发新文档 | 从备份恢复；没有备份只能 `signing rotate --yes` 生成新密钥，并重新构建、分发所有客户端 |
| 口令丢失但 `signing.key` 还在 | 无法解密私钥，等同于无法签发 | 找回口令或按上一行轮换；不要尝试暴力破解生产密钥 |
| `signing.key` 与 `portal.secret` 都丢失 | 只能重建服务 | 新数据库/新密钥/新客户端构建；已有客户端在本地授权到期前仍可离线使用旧授权 |
| 配置目录丢失 | 服务配置、SMTP、Turnstile、IP 白名单、签发口令丢失 | 从加密备份恢复；核对 `SITE_URL` 与域名一致 |
| 恢复到旧备份点 | 新授权、激活、吊销、订单可能回退 | 从审计/支付记录补录；重新执行吊销；告知客服可能的重复激活/退款核对 |

## 7. 演练与检查清单

建议至少每季度在隔离环境做一次完整恢复演练：

- [ ] 备份文件能在没有原服务器的环境解密。
- [ ] `PRAGMA integrity_check` 返回 `ok`。
- [ ] 数据目录权限为 `pyshield:pyshield`，目录 0700、敏感文件 0600。
- [ ] 服务能启动，`/health` 返回 200。
- [ ] 管理端能登录，授权/客户/激活码列表可读。
- [ ] 能完成一次试用签发、一次激活、一次续费和一次吊销。
- [ ] 客户端能用内置公钥验签新文档。
- [ ] 恢复耗时满足 RTO（建议目标 30-60 分钟），可接受的数据丢失窗口满足 RPO（按业务决定，早期可每日，成交高峰期建议每小时）。
- [ ] 备份失败告警能送达负责人。

恢复演练使用的域名、证书、SMTP 和 Turnstile 可以使用测试值；但**签发密钥和客户端公钥必须成对**，否则验签会失败。
