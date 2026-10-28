import React, { useState, useEffect } from 'react';
import {
  Box,
  Paper,
  Typography,
  Button,
  Switch,
  FormControlLabel,
  TextField,
  Alert,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  IconButton,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogContentText,
  DialogActions,
  Chip,
  CircularProgress,
  Tooltip,
  MenuItem,
  Select,
  FormControl,
  InputLabel,
  Grid,
} from '@mui/material';
import {
  Backup as BackupIcon,
  Restore as RestoreIcon,
  Delete as DeleteIcon,
  Download as DownloadIcon,
  Refresh as RefreshIcon,
  Schedule as ScheduleIcon,
  Warning as WarningIcon,
  FactCheck as VerifyIcon,
} from '@mui/icons-material';
import { apiClient } from '../services/apiClient';
import { authGuard } from '../services/authGuard';
import { useTranslation } from '../i18n/useTranslation';
import { ErrorBanner } from './ErrorBanner';

interface BackupConfig {
  enabled: boolean;
  schedule_hour: number;
  retention_count: number;
  last_backup_at?: string | null;
  encryption?: boolean;
  offsite?: boolean;
}

interface BackupFile {
  filename: string;
  size: number;
  created_at: string;
  type: string;
  encrypted?: boolean;
  documents_filename?: string | null;
}

interface VerifyResult {
  filename: string;
  ok: boolean;
  checks: { name: string; ok: boolean; detail: string }[];
  warnings: string[];
  documents_filename?: string | null;
}

interface BackupLog {
  id: number;
  operation: string;
  status: string;
  filename?: string | null;
  error_message?: string | null;
  created_at: string;
  created_by: string | null;
}

const formatBytes = (bytes: number): string => {
  if (!bytes) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  const exponent = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / Math.pow(1024, exponent)).toFixed(1)} ${units[exponent]}`;
};

export const BackupSection: React.FC = () => {
  const { t } = useTranslation('settings');
  const [config, setConfig] = useState<BackupConfig | null>(null);
  const [backups, setBackups] = useState<BackupFile[]>([]);
  const [logs, setLogs] = useState<BackupLog[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  
  const [verifying, setVerifying] = useState<string | null>(null);
  const [verifyResult, setVerifyResult] = useState<VerifyResult | null>(null);
  const [restoreDialog, setRestoreDialog] = useState<{ open: boolean; filename?: string }>({ open: false });
  const [deleteDialog, setDeleteDialog] = useState<{ open: boolean; filename?: string }>({ open: false });

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    setLoading(true);
    setError(null);
    try {
      const [configRes, backupsRes, logsRes] = await Promise.all([
        apiClient.get<BackupConfig>('/api/backup/config'),
        apiClient.get<BackupFile[]>('/api/backup/list'),
        apiClient.get<BackupLog[]>('/api/backup/logs'),
      ]);

      setConfig(configRes);
      setBackups(backupsRes || []);
      setLogs(logsRes || []);
    } catch (err: any) {
      setError(err.message || 'Failed to load backup data');
    } finally {
      setLoading(false);
    }
  };

  const handleSaveConfig = async () => {
    if (!config) return;

    setSaving(true);
    setError(null);
    setSuccess(null);

    try {
      await apiClient.put('/api/backup/config', config);
      setSuccess('Backup configuration saved successfully');
      await loadData();
    } catch (err: any) {
      setError(err.message || 'Failed to save configuration');
    } finally {
      setSaving(false);
    }
  };

  const handleCreateBackup = async () => {
    setCreating(true);
    setError(null);
    setSuccess(null);

    try {
      const res = await apiClient.post<{ success: boolean; message: string; backup: BackupFile }>(
        '/api/backup/create',
        { scheduled: false }
      );
      setSuccess(`Backup created: ${res.backup.filename}`);
      await loadData();
    } catch (err: any) {
      setError(err.message || 'Failed to create backup');
    } finally {
      setCreating(false);
    }
  };

  // Prova di ripristino: decifra e controlla il backup in una copia temporanea, senza toccare il database in uso.
  const handleVerify = async (filename: string) => {
    setVerifying(filename);
    setVerifyResult(null);
    try {
      setVerifyResult(await apiClient.post<VerifyResult>('/api/backup/verify', { filename }));
      await loadData();
    } catch (err: any) {
      setError(err.message || 'Failed to verify backup');
    } finally {
      setVerifying(null);
    }
  };

  const handleRestore = async () => {
    if (!restoreDialog.filename) return;

    try {
      const res = await apiClient.post<{ success: boolean; message: string }>('/api/backup/restore', {
        filename: restoreDialog.filename,
        confirm: true,
      });
      setSuccess(res.message);
      setRestoreDialog({ open: false });
      await loadData();
    } catch (err: any) {
      setError(err.message || 'Failed to restore backup');
      setRestoreDialog({ open: false });
    }
  };

  const handleDelete = async () => {
    if (!deleteDialog.filename) return;

    try {
      await apiClient.delete(`/api/backup/${deleteDialog.filename}`);
      setSuccess('Backup deleted successfully');
      setDeleteDialog({ open: false });
      await loadData();
    } catch (err: any) {
      setError(err.message || 'Failed to delete backup');
      setDeleteDialog({ open: false });
    }
  };

  const handleDownload = async (filename: string) => {
    try {
      const token = authGuard.getToken();
      const backendUrl = process.env.REACT_APP_BACKEND_URL || '';
      const response = await fetch(`${backendUrl}/api/backup/${filename}`, {
        headers: token ? { Authorization: `Bearer ${token}` } : undefined,
      });
      if (!response.ok) throw new Error('Failed to download backup');
      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', filename);
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
    } catch (err: any) {
      setError('Failed to download backup');
    }
  };

  if (loading) {
    return (
      <Box display="flex" justifyContent="center" p={4}>
        <CircularProgress />
      </Box>
    );
  }

  return (
    <Box>
      <ErrorBanner message={error} onClose={() => setError(null)} />
      {success && <Alert severity="success" sx={{ mb: 2 }} onClose={() => setSuccess(null)}>{success}</Alert>}

      {/* Configuration Panel */}
      <Paper sx={{ p: 3, mb: 3 }}>
        <Typography variant="h6" gutterBottom display="flex" alignItems="center">
          <ScheduleIcon sx={{ mr: 1 }} />
          Backup Configuration
        </Typography>
        
        {config && (
          <Grid container spacing={3} sx={{ mt: 1 }}>
            <Grid item xs={12} md={6}>
              <FormControlLabel
                control={
                  <Switch
                    checked={config.enabled}
                    onChange={(e) => setConfig({ ...config, enabled: e.target.checked })}
                  />
                }
                label="Enable Scheduled Backups"
              />
            </Grid>
            
            <Grid item xs={12} md={3}>
              <FormControl fullWidth>
                <InputLabel>Schedule Hour (UTC)</InputLabel>
                <Select
                  value={config.schedule_hour}
                  onChange={(e) => setConfig({ ...config, schedule_hour: Number(e.target.value) })}
                  disabled={!config.enabled}
                  label="Schedule Hour (UTC)"
                >
                  {Array.from({ length: 24 }, (_, i) => (
                    <MenuItem key={i} value={i}>
                      {String(i).padStart(2, '0')}:00
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
            </Grid>
            
            <Grid item xs={12} md={3}>
              <TextField
                fullWidth
                type="number"
                label="Retention Count"
                value={config.retention_count}
                onChange={(e) => setConfig({ ...config, retention_count: Number(e.target.value) })}
                inputProps={{ min: 1, max: 30 }}
                disabled={!config.enabled}
                helperText="Max 30 backups"
              />
            </Grid>
            
            {config.last_backup_at && (
              <Grid item xs={12}>
                <Typography variant="body2" color="text.secondary">
                  Last scheduled backup: {new Date(config.last_backup_at).toLocaleString()}
                </Typography>
              </Grid>
            )}
            
            <Grid item xs={12}>
              <Button
                variant="contained"
                onClick={handleSaveConfig}
                disabled={saving}
                startIcon={saving ? <CircularProgress size={20} /> : null}
              >
                Save Configuration
              </Button>
            </Grid>
          </Grid>
        )}
      </Paper>

      {/* Manual Backup */}
      <Paper sx={{ p: 3, mb: 3 }}>
        <Box display="flex" justifyContent="space-between" alignItems="center">
          <Typography variant="h6" display="flex" alignItems="center">
            <BackupIcon sx={{ mr: 1 }} />
            Manual Backup
          </Typography>
          <Box>
            <Button
              variant="contained"
              color="primary"
              onClick={handleCreateBackup}
              disabled={creating}
              startIcon={creating ? <CircularProgress size={20} /> : <BackupIcon />}
              sx={{ mr: 1 }}
            >
              Create Backup Now
            </Button>
            <IconButton onClick={loadData} color="primary">
              <RefreshIcon />
            </IconButton>
          </Box>
        </Box>
      </Paper>

      {/* Backup List */}
      <Paper sx={{ p: 3, mb: 3 }}>
        <Typography variant="h6" gutterBottom>
          Existing Backups ({backups.length})
        </Typography>
        
        {backups.length === 0 ? (
          <Alert severity="info">No backups available</Alert>
        ) : (
          <TableContainer>
            {verifyResult && (
              <Alert severity={verifyResult.ok ? (verifyResult.warnings.length ? 'warning' : 'success') : 'error'} sx={{ mb: 2 }} onClose={() => setVerifyResult(null)}>
                <strong>{verifyResult.filename}: {verifyResult.ok ? 'verification passed' : 'verification FAILED'}</strong>
                {verifyResult.checks.map((c) => (
                  <div key={c.name}>{c.ok ? '✓' : '✗'} {c.name}: {c.detail}</div>
                ))}
                {verifyResult.warnings.map((w) => (
                  <div key={w}>⚠ {w}</div>
                ))}
              </Alert>
            )}
            <Table>
              <TableHead>
                <TableRow>
                  <TableCell>Filename</TableCell>
                  <TableCell>Size</TableCell>
                  <TableCell>Created</TableCell>
                  <TableCell align="right">Actions</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {backups.map((backup) => (
                  <TableRow key={backup.filename}>
                    <TableCell>
                      {backup.filename}
                      {backup.encrypted && <Chip size="small" color="success" label="Encrypted" sx={{ ml: 1 }} />}
                      {backup.documents_filename && <Chip size="small" label="+ documents" sx={{ ml: 1 }} />}
                    </TableCell>
                    <TableCell>{formatBytes(backup.size)}</TableCell>
                    <TableCell>{new Date(backup.created_at).toLocaleString()}</TableCell>
                    <TableCell align="right">
                      <Tooltip title="Download">
                        <IconButton
                          size="small"
                          onClick={() => handleDownload(backup.filename)}
                          color="primary"
                        >
                          <DownloadIcon />
                        </IconButton>
                      </Tooltip>
                      <Tooltip title="Verify (restore test on a temporary copy)">
                        <span>
                          <IconButton
                            size="small"
                            onClick={() => handleVerify(backup.filename)}
                            disabled={verifying === backup.filename}
                            color="info"
                          >
                            {verifying === backup.filename ? <CircularProgress size={18} /> : <VerifyIcon />}
                          </IconButton>
                        </span>
                      </Tooltip>
                      <Tooltip title="Restore">
                        <IconButton
                          size="small"
                          onClick={() => setRestoreDialog({ open: true, filename: backup.filename })}
                          color="success"
                        >
                          <RestoreIcon />
                        </IconButton>
                      </Tooltip>
                      <Tooltip title="Delete">
                        <IconButton
                          size="small"
                          onClick={() => setDeleteDialog({ open: true, filename: backup.filename })}
                          color="error"
                        >
                          <DeleteIcon />
                        </IconButton>
                      </Tooltip>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        )}
      </Paper>

      {/* Backup Logs */}
      <Paper sx={{ p: 3 }}>
        <Typography variant="h6" gutterBottom>
          Recent Operations
        </Typography>
        
        {logs.length === 0 ? (
          <Alert severity="info">No operations logged yet</Alert>
        ) : (
          <TableContainer>
            <Table>
              <TableHead>
                <TableRow>
                  <TableCell>Operation</TableCell>
                  <TableCell>Status</TableCell>
                  <TableCell>Filename</TableCell>
                  <TableCell>User</TableCell>
                  <TableCell>Date</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {logs.slice(0, 10).map((log) => (
                  <TableRow key={log.id}>
                    <TableCell>{log.operation}</TableCell>
                    <TableCell>
                      <Chip
                        label={log.status}
                        color={log.status === 'success' ? 'success' : 'error'}
                        size="small"
                      />
                    </TableCell>
                    <TableCell>{log.filename || '-'}</TableCell>
                    <TableCell>{log.created_by}</TableCell>
                    <TableCell>{new Date(log.created_at).toLocaleString()}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        )}
      </Paper>

      {/* Restore Confirmation Dialog */}
      <Dialog open={restoreDialog.open} onClose={() => setRestoreDialog({ open: false })}>
        <DialogTitle display="flex" alignItems="center">
          <WarningIcon color="warning" sx={{ mr: 1 }} />
          Confirm Restore
        </DialogTitle>
        <DialogContent>
          <DialogContentText>
            Are you sure you want to restore from <strong>{restoreDialog.filename}</strong>?
            <br /><br />
            The current database will be backed up as a safety measure before restoration.
            <br /><br />
            <Alert severity="warning">This action will replace all current data!</Alert>
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setRestoreDialog({ open: false })}>Cancel</Button>
          <Button onClick={handleRestore} color="warning" variant="contained">
            Restore
          </Button>
        </DialogActions>
      </Dialog>

      {/* Delete Confirmation Dialog */}
      <Dialog open={deleteDialog.open} onClose={() => setDeleteDialog({ open: false })}>
        <DialogTitle>Confirm Delete</DialogTitle>
        <DialogContent>
          <DialogContentText>
            Are you sure you want to delete backup <strong>{deleteDialog.filename}</strong>?
            <br /><br />
            This action cannot be undone.
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeleteDialog({ open: false })}>Cancel</Button>
          <Button onClick={handleDelete} color="error" variant="contained">
            Delete
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

