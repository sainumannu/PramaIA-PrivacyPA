import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from '@mui/material';
import RestartAltIcon from '@mui/icons-material/RestartAlt';
import DeleteSweepIcon from '@mui/icons-material/DeleteSweep';
import FolderOffIcon from '@mui/icons-material/FolderOff';
import CloudDownloadIcon from '@mui/icons-material/CloudDownload';
import { apiClient } from '../services/apiClient';
import { useTranslation } from '../i18n/useTranslation';
import { ErrorBanner } from './ErrorBanner';

interface DemoSection {
  code: string;
  label_it: string;
  label_en: string;
  group: string;
  records: number;
  documents: number;
  has_documents: boolean;
}

interface ConfirmState {
  section: string;
  target: 'data' | 'documents' | 'both';
  label: string;
}

export const DemoDataPanel: React.FC = () => {
  const { t, i18n } = useTranslation('settings');
  const [sections, setSections] = useState<DemoSection[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<ConfirmState | null>(null);

  const labelOf = (s: DemoSection) => (i18n.language?.startsWith('en') ? s.label_en : s.label_it);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await apiClient.get<{ sections: DemoSection[] }>('/api/settings/demo-data');
      setSections(data.sections);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const run = async (section: string, target: 'data' | 'documents' | 'both') => {
    setBusy(true);
    setError(null);
    setInfo(null);
    try {
      if (section === 'seed') {
        await apiClient.post('/api/settings/demo-data/seed', {});
        setInfo(t('demo.seeded'));
      } else {
        const result = await apiClient.post<{ records: number; files: number }>(
          '/api/settings/demo-data/reset',
          { section, target },
        );
        setInfo(t('demo.reset_done', { records: result.records, files: result.files }));
      }
      await load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
      setConfirm(null);
    }
  };

  return (
    <Box sx={{ px: 3, pb: 3 }}>
      <Typography variant="h6" gutterBottom>
        {t('demo.title')}
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        {t('demo.subtitle')}
      </Typography>

      <ErrorBanner message={error} onClose={() => setError(null)} />
      {info && (
        <Alert severity="success" sx={{ mb: 2 }} onClose={() => setInfo(null)}>
          {info}
        </Alert>
      )}

      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} sx={{ mb: 2 }}>
        <Button
          variant="contained"
          startIcon={<CloudDownloadIcon />}
          disabled={busy}
          onClick={() => setConfirm({ section: 'seed', target: 'both', label: t('demo.seed') })}
        >
          {t('demo.seed')}
        </Button>
        <Button
          variant="outlined"
          color="warning"
          startIcon={<DeleteSweepIcon />}
          disabled={busy}
          onClick={() => setConfirm({ section: 'all', target: 'data', label: t('demo.reset_all_data') })}
        >
          {t('demo.reset_all_data')}
        </Button>
        <Button
          variant="outlined"
          color="warning"
          startIcon={<FolderOffIcon />}
          disabled={busy}
          onClick={() => setConfirm({ section: 'all', target: 'documents', label: t('demo.reset_all_documents') })}
        >
          {t('demo.reset_all_documents')}
        </Button>
      </Stack>

      {loading ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}>
          <CircularProgress />
        </Box>
      ) : (
        <TableContainer component={Paper} variant="outlined">
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>{t('demo.column_section')}</TableCell>
                <TableCell align="right">{t('demo.column_records')}</TableCell>
                <TableCell align="right">{t('demo.column_documents')}</TableCell>
                <TableCell align="right">{t('demo.column_actions')}</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {sections.map((section) => (
                <TableRow key={section.code} hover>
                  <TableCell>
                    <Typography variant="body2" fontWeight={600}>
                      {labelOf(section)}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {section.code}
                    </Typography>
                  </TableCell>
                  <TableCell align="right">
                    <Chip size="small" label={section.records} color={section.records ? 'primary' : 'default'} />
                  </TableCell>
                  <TableCell align="right">
                    {section.has_documents ? (
                      <Chip size="small" label={section.documents} color={section.documents ? 'secondary' : 'default'} />
                    ) : (
                      <Typography variant="caption" color="text.disabled">
                        —
                      </Typography>
                    )}
                  </TableCell>
                  <TableCell align="right">
                    <Stack direction="row" spacing={1} justifyContent="flex-end">
                      <Button
                        size="small"
                        color="warning"
                        startIcon={<RestartAltIcon />}
                        disabled={busy || section.records === 0}
                        onClick={() =>
                          setConfirm({
                            section: section.code,
                            target: 'data',
                            label: t('demo.reset_section_data', { section: labelOf(section) }),
                          })
                        }
                      >
                        {t('demo.reset_data')}
                      </Button>
                      {section.has_documents && (
                        <Button
                          size="small"
                          color="warning"
                          startIcon={<FolderOffIcon />}
                          disabled={busy || section.documents === 0}
                          onClick={() =>
                            setConfirm({
                              section: section.code,
                              target: 'documents',
                              label: t('demo.reset_section_documents', { section: labelOf(section) }),
                            })
                          }
                        >
                          {t('demo.reset_documents')}
                        </Button>
                      )}
                    </Stack>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}

      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 2 }}>
        {t('demo.audit_note')}
      </Typography>

      <Dialog open={Boolean(confirm)} onClose={() => !busy && setConfirm(null)}>
        <DialogTitle>{t('demo.confirm_title')}</DialogTitle>
        <DialogContent>
          <DialogContentText>
            {confirm?.section === 'seed' ? t('demo.confirm_seed') : t('demo.confirm_reset', { action: confirm?.label || '' })}
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConfirm(null)} disabled={busy}>
            {t('actions.cancel_changes')}
          </Button>
          <Button
            color={confirm?.section === 'seed' ? 'primary' : 'warning'}
            variant="contained"
            disabled={busy}
            onClick={() => confirm && run(confirm.section, confirm.target)}
          >
            {busy ? <CircularProgress size={18} /> : t('demo.confirm')}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default DemoDataPanel;
