import React, { useEffect, useState } from 'react';
import {
  Box, Button, Chip, Container, Dialog, DialogContent, DialogTitle, MenuItem,
  Paper, Table, TableBody, TableCell, TableHead, TableRow, TextField, Typography,
} from '@mui/material';
import HistoryIcon from '@mui/icons-material/History';
import { PramaLayout } from '../components/PramaLayout';
import { apiClient } from '../services/apiClient';
import { useTranslation } from '../i18n/useTranslation';
import { ErrorBanner } from '../components/ErrorBanner';

interface AuditRow {
  id: string;
  event_time: string;
  actor_user_id: string | null;
  action: string;
  entity_type: string;
  entity_id: string | null;
  event_type: string;
  old_values: Record<string, unknown> | null;
  new_values: Record<string, unknown> | null;
}

export const AuditPage: React.FC = () => {
  const { t } = useTranslation('privacypa');
  const [rows, setRows] = useState<AuditRow[]>([]);
  const [entityType, setEntityType] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [detail, setDetail] = useState<AuditRow | null>(null);

  const load = async () => {
    try {
      const qs = entityType ? `?entity_type=${encodeURIComponent(entityType)}&limit=100` : '?limit=100';
      setRows(await apiClient.get<AuditRow[]>(`/api/privacypa/audit${qs}`));
    } catch (e: any) { setError(e.message); }
  };
  useEffect(() => { load(); }, [entityType]);

  const exportJson = () => {
    const blob = new Blob([JSON.stringify(rows, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'audit-trail.json';
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <PramaLayout pageTitle={t('audit.title')}>
      <Container maxWidth="lg">
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
          <HistoryIcon color="primary" />
          <Typography variant="h4" fontWeight={600}>{t('audit.title')}</Typography>
          <Box sx={{ flex: 1 }} />
          <TextField select size="small" label={t('audit.entity')} value={entityType} onChange={(e) => setEntityType(e.target.value)} sx={{ minWidth: 200 }}>
            <MenuItem value="">{t('audit.all')}</MenuItem>
            {['processing_activity','asset','party','document','task','organization','organizational_unit'].map((x) => (
              <MenuItem key={x} value={x}>{x}</MenuItem>
            ))}
          </TextField>
          <Button variant="outlined" onClick={exportJson}>{t('audit.export')}</Button>
        </Box>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>{t('audit.subtitle')}</Typography>
        <ErrorBanner message={error} onClose={() => setError(null)} />
        <Paper>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>{t('audit.time')}</TableCell>
                <TableCell>{t('audit.actor')}</TableCell>
                <TableCell>{t('audit.action')}</TableCell>
                <TableCell>{t('audit.entity')}</TableCell>
                <TableCell>{t('audit.record')}</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {rows.map((row) => (
                <TableRow key={row.id} hover sx={{ cursor: 'pointer' }} onClick={() => setDetail(row)}>
                  <TableCell>{row.event_time?.replace('T', ' ').slice(0, 19)}</TableCell>
                  <TableCell>{row.actor_user_id || '—'}</TableCell>
                  <TableCell><Chip size="small" label={row.action} /></TableCell>
                  <TableCell>{row.entity_type}</TableCell>
                  <TableCell sx={{ fontFamily: 'monospace', fontSize: '0.75rem' }}>{row.entity_id || '—'}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Paper>
      </Container>
      <Dialog open={Boolean(detail)} onClose={() => setDetail(null)} maxWidth="md" fullWidth>
        <DialogTitle>{detail?.event_type}</DialogTitle>
        <DialogContent>
          <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12 }}>
            {JSON.stringify({ old: detail?.old_values, new: detail?.new_values }, null, 2)}
          </pre>
        </DialogContent>
      </Dialog>
    </PramaLayout>
  );
};
export default AuditPage;
