import React, { useEffect, useState } from 'react';
import {
  Box,
  Card,
  CardContent,
  Typography,
  Chip,
  Alert,
  Button,
  CircularProgress,
  Stack,
  Divider,
} from '@mui/material';
import {
  CheckCircle,
  Error,
  Warning,
  Refresh,
} from '@mui/icons-material';
import { apiClient } from '../services/apiClient';

interface LicenseStatus {
  valid: boolean;
  code: string;
  message: string;
  license_id?: string;
  expires_at?: string;
  maintenance_until?: string;
  allowed_modules?: string[];
  payload_checksum?: string;
  last_sync_at?: string;
  sync_status?: string;
}

const LicensePanel: React.FC = () => {
  const [status, setStatus] = useState<LicenseStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchStatus = async (forceRefresh = false) => {
    try {
      setError(null);
      if (forceRefresh) {
        setRefreshing(true);
      } else {
        setLoading(true);
      }

      let response;
      if (forceRefresh) {
        response = await apiClient.post<LicenseStatus>('/api/license/sync', {});
      } else {
        response = await apiClient.get<LicenseStatus>('/api/license/status');
      }

      setStatus(response);
    } catch (err: any) {
      console.error('License status error:', err);
      setError(err.response?.data?.detail || 'Failed to fetch license status');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    fetchStatus();
  }, []);

  const handleRefresh = () => {
    fetchStatus(true);
  };

  const getStatusIcon = () => {
    if (!status) return null;
    
    if (status.valid) {
      return <CheckCircle color="success" fontSize="large" />;
    }
    
    if (status.code === 'LICENSE_MISSING') {
      return <Error color="error" fontSize="large" />;
    }
    
    return <Warning color="warning" fontSize="large" />;
  };

  const getStatusColor = () => {
    if (!status) return 'default';
    return status.valid ? 'success' : 'error';
  };

  const formatDate = (isoDate?: string) => {
    if (!isoDate) return 'N/A';
    try {
      return new Date(isoDate).toLocaleDateString('it-IT', {
        year: 'numeric',
        month: 'long',
        day: 'numeric',
      });
    } catch {
      return isoDate;
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
    <Card>
      <CardContent>
        <Stack spacing={3}>
          <Box display="flex" justifyContent="space-between" alignItems="center">
            <Box display="flex" alignItems="center" gap={2}>
              {getStatusIcon()}
              <Typography variant="h5">Stato Licenza</Typography>
            </Box>
            <Button
              startIcon={refreshing ? <CircularProgress size={16} /> : <Refresh />}
              onClick={handleRefresh}
              disabled={refreshing}
              variant="outlined"
              size="small"
            >
              Aggiorna
            </Button>
          </Box>

          {error && (
            <Alert severity="error">{error}</Alert>
          )}

          {status && (
            <>
              <Box>
                <Stack direction="row" spacing={2} alignItems="center">
                  <Typography variant="body2" color="text.secondary">
                    Stato:
                  </Typography>
                  <Chip
                    label={status.code}
                    color={getStatusColor()}
                    size="small"
                  />
                </Stack>
              </Box>

              <Alert severity={status.valid ? 'success' : 'error'}>
                {status.message}
              </Alert>

              {status.license_id && (
                <Box>
                  <Typography variant="body2" color="text.secondary" gutterBottom>
                    ID Licenza
                  </Typography>
                  <Typography variant="body1" fontFamily="monospace">
                    {status.license_id}
                  </Typography>
                </Box>
              )}

              <Divider />

              <Stack spacing={2}>
                {status.expires_at && (
                  <Box>
                    <Typography variant="body2" color="text.secondary">
                      Scadenza
                    </Typography>
                    <Typography variant="body1">
                      {formatDate(status.expires_at)}
                    </Typography>
                  </Box>
                )}

                {status.maintenance_until && (
                  <Box>
                    <Typography variant="body2" color="text.secondary">
                      Manutenzione fino a
                    </Typography>
                    <Typography variant="body1">
                      {formatDate(status.maintenance_until)}
                    </Typography>
                  </Box>
                )}

                {status.allowed_modules && status.allowed_modules.length > 0 && (
                  <Box>
                    <Typography variant="body2" color="text.secondary" gutterBottom>
                      Moduli Licenziati
                    </Typography>
                    <Stack direction="row" spacing={1} flexWrap="wrap">
                      {status.allowed_modules.map((module: string) => (
                        <Chip
                          key={module}
                          label={module}
                          size="small"
                          variant="outlined"
                          color="primary"
                        />
                      ))}
                    </Stack>
                  </Box>
                )}
              </Stack>

              <Divider />

              <Typography variant="body2" color="text.secondary" gutterBottom>
                Sincronizzazione Portal
              </Typography>

              <Stack spacing={1.5}>
                {status.sync_status && (
                  <Box display="flex" alignItems="center" gap={1}>
                    <Typography variant="caption" color="text.secondary">
                      Stato sync:
                    </Typography>
                    <Chip
                      label={status.sync_status}
                      size="small"
                      color={status.sync_status === 'ok' ? 'success' : 'warning'}
                      variant="outlined"
                    />
                  </Box>
                )}

                {status.last_sync_at && (
                  <Box display="flex" alignItems="center" gap={1}>
                    <Typography variant="caption" color="text.secondary">
                      Ultimo sync:
                    </Typography>
                    <Typography variant="caption">
                      {formatDate(status.last_sync_at)}
                    </Typography>
                  </Box>
                )}

                {status.payload_checksum && (
                  <Box display="flex" alignItems="center" gap={1}>
                    <Typography variant="caption" color="text.secondary">
                      Checksum:
                    </Typography>
                    <Typography variant="caption" fontFamily="monospace">
                      {status.payload_checksum.substring(0, 12)}...
                    </Typography>
                  </Box>
                )}
              </Stack>

              {!status.valid && (
                <Alert severity="info">
                  <Typography variant="body2">
                    Per assistenza contattare il team Platform o Sales.
                  </Typography>
                </Alert>
              )}
            </>
          )}
        </Stack>
      </CardContent>
    </Card>
  );
};

export default LicensePanel;

