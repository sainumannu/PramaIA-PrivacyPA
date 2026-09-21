# Database Backup System

This document describes the database backup and restore system integrated into the PramaIA AppTemplate.

## 📋 Overview

The backup system provides comprehensive database backup and restoration capabilities with:

- **Scheduled Backups**: Automatic daily backups at a configurable hour (UTC)
- **Manual Backups**: On-demand backup creation
- **Retention Policy**: Automatic cleanup of old backups (configurable, max 30)
- **Safe Restoration**: Creates safety backup before restoring
- **Audit Trail**: Complete logging of all backup operations
- **Admin UI**: Integrated into Settings page with full management interface

## 🏗️ Architecture

### Backend Components

1. **Models** (`backend/models/backup_models.py`):
   - `BackupConfig`: Stores backup configuration (schedule, retention)
   - `BackupLog`: Audit log of backup operations

2. **Service** (`backend/services/backup_service.py`):
   - Core backup logic
   - Uses SQLite backup API (`connection.backup()`) for safe operation
   - Automatic retention policy enforcement
   - File listing and management

3. **Router** (`backend/routers/backup_router.py`):
   - 7 RESTful endpoints for backup management
   - Admin-only access (requires `require_app_admin`)

4. **Scheduler** (`backend/services/backup_scheduler.py`):
   - APScheduler integration for automated backups
   - Dynamic schedule updates when configuration changes

### Frontend Components

1. **BackupSection** (`frontend/src/components/BackupSection.tsx`):
   - Configuration panel (enable/disable, schedule, retention)
   - Manual backup creation
   - Backup list with restore/delete actions
   - Operation logs display

2. **Settings Integration** (`frontend/src/pages/SettingsPage.tsx`):
   - Backup tab in admin settings
   - Access control via `canAccessSettings()`

## 🔧 Configuration

### Environment Variables

No special environment variables required. Backup directory is automatically created at:
```
backups/
```

### Database Initialization

Backup tables are created automatically on app startup via `backend/db/init_db.py`.

### Backup Configuration

Configuration is stored in the `backup_configs` table:

| Field | Type | Description | Range |
|-------|------|-------------|-------|
| `enabled` | Boolean | Enable/disable scheduled backups | true/false |
| `schedule_hour` | Integer | Daily backup hour in UTC | 0-23 |
| `retention_count` | Integer | Max backups to keep | 1-30 |
| `last_run` | DateTime | Last scheduled backup timestamp | - |

## 📡 API Endpoints

All endpoints require App Admin authentication.

### GET `/api/backup/config`
Get current backup configuration.

**Response:**
```json
{
  "enabled": true,
  "schedule_hour": 2,
  "retention_count": 14,
  "last_backup_at": "2025-04-09T02:00:00Z"
}
```

### PUT `/api/backup/config`
Update backup configuration.

**Request:**
```json
{
  "enabled": true,
  "schedule_hour": 3,
  "retention_count": 20
}
```

**Notes:**
- Automatically reloads scheduler with new configuration
- All fields are optional (partial updates supported)

### GET `/api/backup/list`
List all available backups.

**Response:**
```json
{
  "backups": [
    {
      "filename": "backup_20250409_020000.db",
      "size": 1048576,
      "created_at": "2025-04-09T02:00:00Z",
      "formatted_size": "1.0 MB",
      "formatted_date": "Apr 9, 2025 02:00 AM"
    }
  ]
}
```

### POST `/api/backup/create`
Create a manual backup.

**Request:**
```json
{
  "scheduled": false
}
```

**Response:**
```json
{
  "success": true,
  "message": "Backup created successfully",
  "filename": "backup_manual_20250409_143022.db",
  "size": 1048576
}
```

### POST `/api/backup/restore`
Restore database from backup.

**Request:**
```json
{
  "filename": "backup_20250409_020000.db"
}
```

**Response:**
```json
{
  "success": true,
  "message": "Database restored successfully",
  "safety_backup": "backup_safety_20250409_143500.db"
}
```

**Notes:**
- Creates a safety backup before restoration
- Requires app restart to take effect fully

### DELETE `/api/backup/{filename}`
Delete a specific backup file.

**Response:**
```json
{
  "success": true,
  "message": "Backup deleted successfully"
}
```

### GET `/api/backup/logs`
Get recent backup operation logs.

**Response:**
```json
{
  "logs": [
    {
      "id": 1,
      "operation": "create",
      "status": "success",
      "filename": "backup_20250409_020000.db",
      "created_by": "admin@example.com",
      "created_at": "2025-04-09T02:00:00Z"
    }
  ]
}
```

## 🖥️ User Interface

### Accessing Backup Settings

1. Navigate to **Settings** (gear icon in header)
2. Click on the **Backup** tab
3. Only visible to Global Admin, Tenant Admin, or App Admin

### Configuration Panel

- **Enable Scheduled Backups**: Toggle automatic backups on/off
- **Schedule Hour (UTC)**: Select daily backup hour (00:00 - 23:00)
- **Retention Count**: Number of backups to keep (1-30)
- **Save Configuration**: Apply changes and update scheduler

### Manual Backup

Click **"Create Backup Now"** to trigger immediate backup creation.

### Backup List

View all existing backups with:
- **Download**: Download backup file to local machine
- **Restore**: Restore database from this backup (with confirmation)
- **Delete**: Remove backup file (with confirmation)

### Operation Logs

View recent backup operations with:
- Operation type (create, restore, delete)
- Status (success, error)
- Filename
- User who performed the operation
- Timestamp

## 🔒 Security & Access Control

### Authentication

All backup endpoints require:
1. Valid JWT token from Portal
2. App Admin role (`require_app_admin`)

### Permissions Hierarchy

Users who can access backup features:
- **Global Admin**: Full access across all tenants
- **Tenant Admin**: Access within their tenant
- **App Admin**: Access to app backup settings

### File Security

- Backup files stored in `backups/` directory
- Directory excluded from Git (`.gitignore`)
- Files named with timestamp for easy identification
- No sensitive data in filenames

## 🔄 Backup Process

### Scheduled Backup Flow

1. **APScheduler** triggers at configured hour (UTC)
2. **BackupScheduler** calls `BackupService.create_backup()`
3. SQLite backup API creates consistent snapshot
4. File saved to `backups/backup_YYYYMMDD_HHMMSS.db`
5. **Retention policy** automatically deletes old backups
6. Operation logged to `backup_logs` table
7. `last_run` timestamp updated in config

### Manual Backup Flow

1. User clicks **"Create Backup Now"**
2. Frontend calls `POST /api/backup/create`
3. Router validates admin access
4. Service creates backup with `manual` prefix
5. File saved to `backups/backup_manual_YYYYMMDD_HHMMSS.db`
6. Retention policy applied
7. Operation logged

### Restore Flow

1. User selects backup and clicks **Restore**
2. Confirmation dialog warns about data replacement
3. Frontend calls `POST /api/backup/restore`
4. **Safety backup** created from current database
5. Selected backup copied over current database
6. Success message with safety backup filename
7. **App restart recommended** for full effect

## 🛠️ Maintenance

### Monitoring Backups

Check backup status via:
- **Settings > Backup tab**: View all backups and logs
- **Backend logs**: Search for `BackupService` or `BackupScheduler`
- **Database**: Query `backup_logs` table

### Retention Policy

Retention policy runs automatically after each backup:
- Keeps most recent N backups (configured in `retention_count`)
- Deletes oldest backups when limit exceeded
- Manual and scheduled backups count toward limit
- Safety backups **excluded** from retention (prefix: `backup_safety_`)

### Troubleshooting

**Scheduled backups not running:**
- Check `enabled` is `true` in config
- Verify `schedule_hour` is correct (UTC timezone)
- Check backend logs for scheduler errors
- Ensure APScheduler dependency installed

**Backup creation fails:**
- Check database file path in `DATABASE_URL`
- Verify `backups/` directory exists and is writable
- Check disk space availability
- Review logs for specific error messages

**Restore fails:**
- Ensure backup file exists in `backups/` directory
- Check file is valid SQLite database
- Verify sufficient disk space for safety backup
- Restart app after restore

## 📊 Best Practices

### Configuration Recommendations

1. **Enable scheduled backups** for production environments
2. Set backup hour during **low-traffic periods** (e.g., 2-3 AM UTC)
3. Keep **7-14 days** of backups for most apps (retention_count: 7-14)
4. Increase retention for **critical data** (up to 30 days)

### Backup Frequency

- **Development**: Manual backups before major changes
- **Staging**: Daily scheduled backups
- **Production**: Daily scheduled + manual before deployments

### Storage Considerations

- Each backup = full database copy
- Monitor `backups/` directory size
- External backup storage for critical apps (manual download)

### Testing Restores

1. **Test restoration** in development/staging first
2. Verify data integrity after restore
3. Keep safety backups for rollback

### Disaster Recovery

1. Download critical backups to **external storage**
2. Document restoration procedures
3. Test recovery process periodically

## 🔍 Monitoring & Alerting

### Log Analysis

Search backend logs for:
```
BackupScheduler.*failed
BackupService.*error
create_backup.*success
```

### Health Checks

Monitor:
- Last backup timestamp (`last_run` in config)
- Backup file sizes (sudden changes = issue)
- Log table for consecutive failures

### Recommended Alerts

Set up alerts for:
- **No backup in 48 hours** (scheduler stopped)
- **Backup failures** (3+ consecutive errors)
- **Disk space low** (< 10% free)

## 📝 Development Notes

### Extending Backup System

To add cloud backup integration:
1. Modify `BackupService._upload_to_cloud()` method
2. Add cloud credentials to `.env`
3. Update retention policy to handle remote files

To add compression:
1. Modify `BackupService.create_backup()` to compress after creation
2. Update `BackupService.restore_backup()` to decompress before restore
3. Update filename patterns

### Database Compatibility

Current implementation uses **SQLite backup API**:
- Safe for active databases (no corruption risk)
- Atomic operation (all-or-nothing)
- Preserves database integrity

For **PostgreSQL/MySQL**:
- Replace with `pg_dump` / `mysqldump` commands
- Update `BackupService` implementation
- Test thoroughly with active connections

## 🚀 Quick Start

### Enable Backups (First Time)

1. Go to **Settings > Backup**
2. Toggle **"Enable Scheduled Backups"** ON
3. Select **schedule hour** (e.g., 02:00)
4. Set **retention count** (e.g., 14)
5. Click **"Save Configuration"**

### Create First Backup

Click **"Create Backup Now"** to verify system is working.

### Verify Scheduler

Check backend logs for:
```
[INFO] Scheduled backup at 02:00 UTC (enabled)
[INFO] Backup scheduler started
```

### Monitor Backups

Return to **Settings > Backup** to view:
- Backup files in list
- Recent operations in logs
- Last scheduled run timestamp

## 📚 Related Documentation

- `LOGGING_GUIDE.md`: Backend logging system
- `PERMISSIONS_SYSTEM.md`: Access control details
- `LICENSE_SYSTEM_README.md`: License integration

---

**Version**: 1.0  
**Last Updated**: April 9, 2025  
**Component**: PramaIA AppTemplate Backup System
