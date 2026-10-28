import React, { useEffect, useState } from 'react';
import { Alert, Box, Button, Paper, TextField, Typography } from '@mui/material';
import { apiClient } from '../services/apiClient';
import { useTranslation } from '../i18n/useTranslation';
import { ErrorBanner } from './ErrorBanner';

interface Payload { row_version: number; values: Record<string, number> }

// Chiave → intervallo ammesso (deve coincidere con `DEADLINE_LIMITS` del backend). Mesi di calendario; 0 = calcolo automatico spento.
const FIELDS: [string, number, number][] = [
  ['processing_review_months', 0, 60], ['dpia_review_months', 0, 60], ['dsr_base_months', 1, 3], ['dsr_extension_months', 0, 6],
];

// Scadenze relative per ente (§156): quanti mesi dopo l'approvazione cade la prossima revisione, e i termini delle richieste degli interessati.
export const DeadlinesPanel: React.FC = () => {
  const { t } = useTranslation('privacypa');
  const [data, setData] = useState<Payload | null>(null);
  const [values, setValues] = useState<Record<string, number>>({});
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  const apply = (p: Payload) => { setData(p); setValues(p.values); };
  useEffect(() => { apiClient.get<Payload>('/api/privacypa/policy/deadlines').then(apply).catch((e) => setError(e.message)); }, []);
  if (!data) return error ? <Alert severity="error">{error}</Alert> : null;

  const save = () => apiClient.put<Payload>('/api/privacypa/policy/deadlines', values, { version: data.row_version })
    .then((next) => { apply(next); setError(null); setInfo(t('deadlines.saved')); })
    .catch((e) => { setInfo(null); setError(e.message); });

  return (
    <Paper sx={{ p: 2, mt: 3 }}>
      <Typography variant="h6">{t('deadlines.title')}</Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>{t('deadlines.subtitle')}</Typography>
      <ErrorBanner message={error} onClose={() => setError(null)} />
      {info && <Alert severity="success" sx={{ mb: 2 }} onClose={() => setInfo(null)}>{info}</Alert>}
      <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap' }}>
        {FIELDS.map(([key, min, max]) => (
          <TextField
            key={key} size="small" type="number" sx={{ width: 260 }} label={t(`deadlines.fields.${key}`)} value={values[key] ?? ''}
            helperText={t('deadlines.range', { min, max })} inputProps={{ min, max }}
            onChange={(e) => setValues({ ...values, [key]: Number(e.target.value) })}
          />
        ))}
      </Box>
      <Box sx={{ mt: 2 }}><Button variant="contained" onClick={save}>{t('common.save')}</Button></Box>
    </Paper>
  );
};
